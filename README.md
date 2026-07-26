# QueueCTL

QueueCTL is a minimal, production-grade CLI-based background job queue system built in Python and powered by SQLite.

## Features

- **CLI-Native:** Full management via simple CLI commands.
- **Concurrent Workers:** Run multiple worker processes across different terminals safely. Atomic job claiming via SQLite `UPDATE ... RETURNING` prevents double-execution.
- **Robust Crash Recovery:** Detects `SIGKILL`ed workers via a heartbeat mechanism and recovers stuck jobs within 60 seconds.
- **Exponential Backoff & Retries:** Automatically retries failed jobs with a configurable backoff interval before moving them to a Dead Letter Queue (DLQ).
- **Graceful Shutdown:** `queuectl worker stop` safely signals running workers via `SIGTERM` to complete their in-flight jobs before exiting.

## Setup

### Requirements
- Python 3.8+
- SQLite3 (built into Python)

### Installation
Clone the repository and run the CLI directly from the root:
```bash
git clone <repo_url>
cd queuectl
chmod +x queuectl
```

You can optionally add it to your PATH:
```bash
export PATH="$(pwd):$PATH"
```

## Usage

### 1. Enqueue Jobs
Add a job by providing a JSON string containing at least an `id` and a `command`.
```bash
./queuectl enqueue '{"id":"job1","command":"sleep 2"}'
./queuectl enqueue '{"id":"job2","command":"echo Hello World"}'
```

### 2. Start Workers
Start a single worker, or use `--count` to fork multiple workers in the foreground.
```bash
./queuectl worker start --count 3
```

### 3. Check Status
Get an overview of job states and active worker processes.
```bash
./queuectl status
```

### 4. List Jobs
List jobs by state. Use `--json` to output a clean JSON array (useful for programmatic parsing).
```bash
./queuectl list --state pending
./queuectl list --state failed --json
```

### 5. Stop Workers Gracefully
From another terminal, you can cleanly shut down all active workers.
```bash
./queuectl worker stop
```

### 6. Dead Letter Queue (DLQ)
Jobs that exceed their maximum retries are moved to the DLQ.
```bash
./queuectl dlq list
./queuectl dlq retry job1
```

### 7. Configuration
Adjust max retries and exponential backoff base on the fly. Changes apply to future attempts.
```bash
./queuectl config set max-retries 5
./queuectl config set backoff-base 3
```

## Architecture

QueueCTL relies heavily on **SQLite** to manage state, utilizing its file-locking mechanisms to provide atomic transactions across multiple separate Python processes.

- **Storage:** Persisted locally at `~/.queuectl/queue.db` by default. Can be overridden via the `QUEUECTL_DB_PATH` environment variable.
- **Atomic Operations:** Claiming a job uses a single `UPDATE ... RETURNING` query so that no two workers can claim the same job.
- **Heartbeats:** Active workers ping the database every 15 seconds. If a job is `processing` but hasn't received a heartbeat in 45 seconds, it is considered stale and recovered by another worker.

For detailed architectural justifications, please read [DECISIONS.md](DECISIONS.md).

## Testing

An automated shell script is provided to verify the core requirements (Scenarios 1-5).
```bash
bash test_queuectl.sh
```

## Demo

[Link to Demo Recording] (To be added)
