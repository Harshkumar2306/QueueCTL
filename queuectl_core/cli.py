import argparse
import json
import sys
import os
import signal
from datetime import datetime
from typing import Any, Optional, Dict

from .db import init_db, get_connection, set_config, get_config
from .worker import run_worker
from .dashboard import run_server

def print_json(data: Any) -> None:
    print(json.dumps(data))

def get_job_by_id(job_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    if row:
        return dict(row)
    return None

def cmd_enqueue(args: argparse.Namespace) -> None:
    try:
        data = json.loads(args.job_json)
    except json.JSONDecodeError:
        print("Invalid JSON")
        sys.exit(1)

    if "id" not in data or "command" not in data:
        print("Job must contain at least 'id' and 'command'")
        sys.exit(1)
        
    job_id = data["id"]
    command = data["command"]
    max_retries = int(get_config("max-retries", "3"))

    conn = get_connection()
    with conn:
        try:
            conn.execute("""
                INSERT INTO jobs (id, command, state, attempts, max_retries, created_at, updated_at)
                VALUES (?, ?, 'pending', 0, ?, datetime('now'), datetime('now'))
            """, (job_id, command, max_retries))
            print(f"Enqueued job {job_id}")
        except sqlite3.IntegrityError:
            print(f"Job with id {job_id} already exists")
            sys.exit(1)

def cmd_worker_start(args: argparse.Namespace) -> None:
    print(f"Starting {args.count} workers...")
    run_worker(count=args.count)

def cmd_worker_stop(args: argparse.Namespace) -> None:
    # stop workers gracefully
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT pid FROM workers")
    workers = cur.fetchall()
    
    if not workers:
        print("No active workers found.")
        return
        
    for w in workers:
        pid = w['pid']
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Sent SIGTERM to worker PID {pid}")
        except ProcessLookupError:
            print(f"Worker PID {pid} is already dead, removing from db.")
            with conn:
                conn.execute("DELETE FROM workers WHERE pid = ?", (pid,))
        except Exception as e:
            print(f"Failed to signal worker PID {pid}: {e}")
            
def cmd_status(args: argparse.Namespace) -> None:
    conn = get_connection()
    cur = conn.cursor()
    
    # job stats
    cur.execute("SELECT state, COUNT(*) as cnt FROM jobs GROUP BY state")
    stats = {row['state']: row['cnt'] for row in cur.fetchall()}
    
    cur.execute("SELECT pid, started_at, heartbeat_at FROM workers")
    workers = [dict(row) for row in cur.fetchall()]
    
    print("=== QueueCTL Status ===")
    print("Jobs:")
    for state in ['pending', 'processing', 'completed', 'failed', 'dead']:
        print(f"  {state}: {stats.get(state, 0)}")
        
    print(f"\nActive Workers: {len(workers)}")
    for w in workers:
        print(f"  PID {w['pid']} (started at {w['started_at']}, last heartbeat {w['heartbeat_at']})")

def cmd_list(args: argparse.Namespace) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE state = ?", (args.state,))
    rows = cur.fetchall()
    
    jobs = [dict(r) for r in rows]
    
    if args.json:
        # Interface contract 2: print JSON array and nothing else
        print(json.dumps(jobs, indent=2))
    else:
        for job in jobs:
            print(f"[{job['id']}] {job['command']} (attempts: {job['attempts']}/{job['max_retries']})")

def cmd_dlq_list(args: argparse.Namespace) -> None:
    args.state = 'dead'
    cmd_list(args)

def cmd_dlq_retry(args: argparse.Namespace) -> None:
    job_id = args.id
    conn = get_connection()
    # Reset attempts to 0 so it gets the full retry logic again.
    # We justify this in DECISIONS.md.
    with conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobs 
            SET state = 'pending', attempts = 0, run_after = NULL, updated_at = datetime('now')
            WHERE id = ? AND state = 'dead'
        """, (job_id,))
        if cur.rowcount > 0:
            print(f"Job {job_id} moved back to pending.")
        else:
            print(f"Job {job_id} not found in DLQ.")

def cmd_config_set(args: argparse.Namespace) -> None:
    key = args.key
    value = args.value
    if key not in ['max-retries', 'backoff-base', 'job-timeout']:
        print(f"Unknown config key: {key}")
        sys.exit(1)
    
    set_config(key, value)
    print(f"Config '{key}' set to '{value}'")

def cmd_dashboard(args: argparse.Namespace) -> None:
    run_server(port=args.port)

def main() -> None:
    import sqlite3 # local import for catching exceptions
    init_db()

    parser = argparse.ArgumentParser(description="QueueCTL - Background job queue system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # enqueue
    enqueue_parser = subparsers.add_parser("enqueue")
    enqueue_parser.add_argument("job_json", help="JSON string representing the job")

    # worker
    worker_parser = subparsers.add_parser("worker")
    worker_subs = worker_parser.add_subparsers(dest="worker_cmd", required=True)
    
    w_start = worker_subs.add_parser("start")
    w_start.add_argument("--count", type=int, default=1, help="Number of worker processes")
    
    w_stop = worker_subs.add_parser("stop")

    # status
    subparsers.add_parser("status")

    # list
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--state", required=True, choices=["pending", "processing", "completed", "failed", "dead"])
    list_parser.add_argument("--json", action="store_true", help="Output in JSON format")

    # dlq
    dlq_parser = subparsers.add_parser("dlq")
    dlq_subs = dlq_parser.add_subparsers(dest="dlq_cmd", required=True)
    
    d_list = dlq_subs.add_parser("list")
    d_list.add_argument("--json", action="store_true")
    
    d_retry = dlq_subs.add_parser("retry")
    d_retry.add_argument("id", help="Job ID to retry")

    # config
    config_parser = subparsers.add_parser("config")
    config_subs = config_parser.add_subparsers(dest="config_cmd", required=True)
    
    c_set = config_subs.add_parser("set")
    c_set.add_argument("key")
    c_set.add_argument("value")

    # dashboard
    dashboard_parser = subparsers.add_parser("dashboard")
    dashboard_parser.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()

    if args.command == "enqueue":
        cmd_enqueue(args)
    elif args.command == "worker":
        if args.worker_cmd == "start":
            cmd_worker_start(args)
        elif args.worker_cmd == "stop":
            cmd_worker_stop(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "dlq":
        if args.dlq_cmd == "list":
            cmd_dlq_list(args)
        elif args.dlq_cmd == "retry":
            cmd_dlq_retry(args)
    elif args.command == "config":
        if args.config_cmd == "set":
            cmd_config_set(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
