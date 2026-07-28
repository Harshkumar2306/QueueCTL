# DECISIONS.md

This document explains the core design decisions for **QueueCTL**, answering the five required architectural questions.

## 1. Which exact line(s) prevent two workers from claiming the same job, and why is that operation atomic across separate OS processes?

In `queuectl_core/worker.py` (line 127), the job claiming logic relies on this SQL query:

```sql
UPDATE jobs 
SET state = 'processing', 
    locked_by = ?, 
    heartbeat_at = datetime('now'), 
    updated_at = datetime('now')
WHERE id = (
    SELECT id FROM jobs 
    WHERE state IN ('pending', 'failed') 
    AND (run_after IS NULL OR run_after <= datetime('now'))
    ORDER BY created_at ASC 
    LIMIT 1
)
RETURNING id, command, attempts, max_retries;
```

**Why it's atomic:** The claim operation is a single SQL statement executed inside a database transaction. SQLite serializes conflicting write transactions, meaning only one worker can successfully claim a given job at a time. Furthermore, we connect to SQLite with `isolation_level="IMMEDIATE"`. `BEGIN IMMEDIATE` acquires a reserved write lock as soon as the transaction begins rather than waiting for the first write statement. This reduces contention and avoids races between competing writers. Finally, using `RETURNING id, command, attempts, max_retries` avoids a second `SELECT` after the `UPDATE`, ensuring the database returns the claimed row as part of the exact same atomic operation.

## 2. A worker is SIGKILLed halfway through a job. Walk through, step by step, what state the job is in and how it eventually runs again. What is the worst-case delay before recovery?

1. **Crash state:** When the worker is `SIGKILL`ed, no cleanup code runs. The job remains stuck in the `processing` state in the database.
2. **Heartbeat Failure:** The heartbeat indicates that the worker process is still making forward progress. When the process dies, the heartbeat thread dies with it, and the `heartbeat_at` timestamp stops updating.
3. **Detection:** All active workers periodically run a `recover_stale_jobs` check. They query for jobs where `state = 'processing'` and `heartbeat_at` is older than 40 seconds.
4. **Recovery:** Recovery logic treats workers whose heartbeat becomes stale as failed. When a live worker detects this stale job, it marks the original worker as dead, formally increments the job's `attempts` counter in the database, calculates the exponential backoff penalty, and transitions the job to `failed` (scheduling it via `run_after`).
5. **Worst-case delay:** 
   - A worker's heartbeat is updated every 15 seconds (balancing responsiveness with low database overhead).
   - The threshold for being considered "stale" is 40 seconds. Forty seconds allows for delayed scheduling and temporary CPU pauses while still comfortably meeting the assignment's under-60-second recovery requirement.
   - Therefore, the worst-case time for a crashed job to be identified and recovered is under **55 seconds** (the 40-second threshold + up to 14.9 seconds of lost heartbeat time).

## 3. Does dlq retry reset attempts? Why is that the right call?

Yes, `queuectl dlq retry <id>` sets `attempts = 0` when it moves the job back to `pending`.

**Justification:** When a job lands in the Dead Letter Queue (DLQ), it means the system's automatic retry logic (including exponential backoff) fundamentally failed to resolve the issue. DLQ jobs require human intervention—a developer must investigate, fix an external API outage, patch a bug, or correct invalid data. Once the human intervention is complete, the job is effectively running under new conditions. By resetting the attempts to 0, we give the job the full, standard retry lifecycle again. *(Note: Preserving attempts is also a valid design. I chose to reset the counter because entering the DLQ represents the end of one automatic retry lifecycle. A manual retry starts a new lifecycle after the underlying issue has presumably been resolved).*

## 4. What designs did you consider and reject for worker stop (cross-process signaling), and why?

To implement `worker stop` across separate terminal sessions, I needed an Inter-Process Communication (IPC) mechanism. 

**Rejected: Named Pipes / UNIX Domain Sockets**
- *Why:* Building a control socket server into every worker introduces significant complexity (managing socket files, handling async IO alongside the blocking `subprocess.wait()`).

**Rejected: Pub/Sub via Database Polling**
- *Why:* Having workers constantly poll a `commands` table adds unnecessary database traffic and latency to the shutdown process.

**Chosen: Database-backed PID tracking + POSIX Signals (`os.kill`)**
- *Why:* Every worker already interacts with SQLite. When a worker starts via `os.fork()`, the kernel creates a child process using copy-on-write memory pages, demonstrating true process isolation. We chose processes instead of threads because workers execute external commands and we wanted complete isolation. *(Note: `os.fork()` is a Unix-specific mechanism. For a cross-platform implementation with Windows support, I would replace it with Python's `multiprocessing` module while keeping the rest of the architecture unchanged).* 
- *The Flow:* The worker inserts its Process ID (`PID`) into the `workers` table. `queuectl worker stop` queries this table and sends a `SIGTERM` signal. The worker receives `SIGTERM`, stops accepting new jobs, waits for the current running job to finish via `proc.wait()`, updates the database, and then exits cleanly.

## 5. If priorities were added tomorrow (high-priority jobs jump the queue), which parts of your design survive unchanged and which break?

**Survives unchanged:**
- The CLI parser, DB connection handling, heartbeat tracking, DLQ logic, and cross-process shutdown via PIDs remain entirely unaffected.

**Breaks / Needs modification:**
- **The DB Schema:** We would need to add a `priority` integer column to the `jobs` table.
- **The Index:** The current index `CREATE INDEX idx_jobs_state_run_after ON jobs (state, run_after)` would be suboptimal. We would need to recreate it as `CREATE INDEX idx_jobs_state_priority_run_after ON jobs (state, priority, run_after)`.
- **The Claim Query:** The core `SELECT` subquery in `worker.py` must be updated to sort by priority first, then creation time (`ORDER BY priority DESC, created_at ASC`).

## 6. Advanced Architecture Trade-offs (Bonus)

**Why SQLite instead of Redis?**
Redis is excellent for high-throughput distributed queues, but this assignment requires persistence, crash recovery, and no external infrastructure. SQLite already provides durable storage, ACID transactions, and atomic updates in a single embedded database.

**What happens if power is lost while SQLite is writing?**
SQLite transactions are atomic. If power is lost before the commit finishes, the transaction is rolled back upon reboot. If the commit completes successfully, the changes are durable. The WAL (Write-Ahead Logging) mode is explicitly designed to maintain consistency and recover gracefully even across total system power failures.

## 7. Do config changes affect already-enqueued jobs?

Yes, but conditionally based on the job's current state:
- **`max-retries`:** This config is locked in at the moment the job is created (it is stored in the `jobs` table as `max_retries`). Changing the global config will **not** affect already-enqueued jobs; they will respect their original maximum limit.
- **`backoff-base`:** This config is evaluated dynamically at runtime whenever a job fails. If you change the `backoff-base`, the very next time any job fails, it will calculate its `delay` using the new global base. However, jobs that have *already* failed and are currently waiting with a scheduled `run_after` timestamp will not have their timestamp retroactively modified.
