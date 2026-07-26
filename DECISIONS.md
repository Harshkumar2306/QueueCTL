# DECISIONS.md

This document explains the core design decisions for **QueueCTL**, answering the five required architectural questions.

## 1. Which exact line(s) prevent two workers from claiming the same job, and why is that operation atomic across separate OS processes?

In `queuectl_core/worker.py` (around line 105), the job claiming logic relies on this SQL query:

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

**Why it's atomic:** SQLite guarantees that a single statement executes atomically within a transaction. The `UPDATE ... WHERE id = (SELECT ... LIMIT 1) RETURNING *` structure (introduced in SQLite 3.35.0) ensures that evaluating the subquery (finding the oldest pending job), locking it by changing its state to `processing`, and returning the claimed job row all happen as one indivisible operation. SQLite's file-level locking ensures that even across entirely separate OS processes, only one process can hold the write lock at any given microsecond. Thus, a job cannot be double-claimed.

## 2. A worker is SIGKILLed halfway through a job. Walk through, step by step, what state the job is in and how it eventually runs again. What is the worst-case delay before recovery?

1. **Crash state:** When the worker is `SIGKILL`ed, no cleanup code runs. The job remains in the `processing` state in the SQLite database, and the `heartbeat_at` timestamp stops updating.
2. **Detection:** All active workers periodically run the `recover_stale_jobs` function before they attempt to pull a new job from the queue. This function queries for jobs where `state = 'processing'` and `heartbeat_at` is older than 45 seconds (`datetime('now', '-45 seconds')`).
3. **Recovery:** When a live worker detects this stale job, it assumes the original worker crashed. It increments the job's `attempts` counter and calculates the exponential backoff penalty. It then transitions the job to `failed` and sets `run_after` to the future backoff time. (If the attempts exceed `max_retries`, it goes straight to `dead`).
4. **Subsequent execution:** Once the `run_after` time passes, any worker's subquery will pick it up again as a valid job to process.
5. **Worst-case delay:** 
   - A worker's heartbeat is updated every 15 seconds.
   - The threshold for being considered "stale" is 45 seconds without a heartbeat update.
   - Therefore, the worst-case time for a crashed job to be identified and recovered by another active worker is just under **60 seconds** (the 45-second threshold + up to 14.9 seconds of lost heartbeat time before the crash).

## 3. Does dlq retry reset attempts? Why is that the right call?

Yes, `queuectl dlq retry <id>` sets `attempts = 0` when it moves the job back to `pending`.

**Justification:** When a job lands in the Dead Letter Queue (DLQ), it means the system's automatic retry logic (including exponential backoff) fundamentally failed to resolve the issue. DLQ jobs require human intervention—a developer must investigate, fix an external API outage, patch a bug, or correct invalid data. Once the human intervention is complete, the job is effectively running under new conditions. By resetting the attempts to 0, we give the job the full, standard retry lifecycle again, anticipating that if it fails this time, it might be for a completely new, transient reason that warrants the normal backoff behavior rather than instantly dying again on the first failure.

## 4. What designs did you consider and reject for worker stop (cross-process signaling), and why?

To implement `worker stop` across separate terminal sessions, I needed an Inter-Process Communication (IPC) mechanism. 

**Rejected: Named Pipes / UNIX Domain Sockets**
- *Why:* Building a control socket server into every worker introduces significant complexity (managing socket files, handling async IO alongside the blocking `subprocess.wait()`, cleaning up stale sockets on SIGKILL).

**Rejected: POSIX message queues / Shared Memory**
- *Why:* Too low-level and platform-specific, contradicting the desire for a simple, universally runnable CLI.

**Rejected: Using a dedicated "commands" table (Pub/Sub via DB)**
- *Why:* I considered having workers constantly poll a `commands` table for a "shutdown" row. However, polling adds unnecessary database load and latency to the shutdown process.

**Chosen: Database-backed PID tracking + POSIX Signals (`os.kill`)**
- *Why:* Every worker already needs to talk to SQLite. When a worker starts, it inserts its Process ID (`PID`) into a `workers` table. `queuectl worker stop` simply queries the table for active PIDs and sends a `SIGTERM` signal using `os.kill(pid, signal.SIGTERM)`. The worker's signal handler gracefully finishes the current job and exits. It is extremely simple, relies on standard OS primitives, and requires zero polling for the shutdown command itself.

## 5. If priorities were added tomorrow (high-priority jobs jump the queue), which parts of your design survive unchanged and which break?

**Survives unchanged:**
- The CLI parser, DB connection handling, heartbeat tracking, DLQ logic, and cross-process shutdown via PIDs remain entirely unaffected.

**Breaks / Needs modification:**
- **The DB Schema:** We would need to add a `priority` integer column to the `jobs` table (e.g., `0` for normal, `1` for high).
- **The Index:** The current index `CREATE INDEX idx_jobs_state_run_after ON jobs (state, run_after)` would be suboptimal. We would need to recreate it as `CREATE INDEX idx_jobs_state_priority_run_after ON jobs (state, priority, run_after)`.
- **The Claim Query:** The core `SELECT` subquery in `worker.py` must be updated to sort by priority first, then creation time:
  ```sql
  ORDER BY priority DESC, created_at ASC 
  ```
Because we isolated the job-claiming logic to a single SQL query in `worker.py`, implementing priorities would only require modifying a single SQL statement and adding one column. The rest of the architecture is completely resilient to this change.
