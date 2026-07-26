import sqlite3
import time
import subprocess
import os
import signal
import threading
from datetime import datetime, timezone
import json

from .db import get_connection, get_config

shutdown_requested = False

def handle_sigterm(signum, frame):
    global shutdown_requested
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def recover_stale_jobs(conn, worker_id):
    # If a job is in 'processing' but heartbeat_at is older than 45 seconds,
    # we consider the worker dead and recover the job.
    # SQLite datetime('now', '-45 seconds') works for ISO-8601 strings.
    # Note: we use python's datetime if we are dealing with pure strings or sqlite builtin.
    # We'll use sqlite builtin for simplicity.
    with conn:
        # Revert stale jobs. If attempts == 0 -> pending, else failed (so it backs off or goes to DLQ on next worker)
        # Actually, if it failed mid-flight, we increment attempts and apply backoff.
        # But for simplicity, let's just mark it pending if we want immediate retry, or failed.
        # The prompt says: "recover the job so it can run again".
        
        # Let's find stale jobs
        cur = conn.cursor()
        cur.execute("""
            SELECT id, attempts, max_retries
            FROM jobs 
            WHERE state = 'processing' 
            AND heartbeat_at < datetime('now', '-45 seconds')
        """)
        stale_jobs = cur.fetchall()
        
        for job in stale_jobs:
            attempts = job['attempts'] + 1
            max_retries = job['max_retries']
            
            if attempts > max_retries:
                state = 'dead'
                run_after = None
            else:
                state = 'failed'
                # backoff
                base = int(get_config('backoff-base', '2'))
                delay = base ** (attempts - 1)  # attempts is already incremented, so attempts-1 is the number of completed attempts
                run_after = f"datetime('now', '+{delay} seconds')"

            if state == 'dead':
                conn.execute(f"""
                    UPDATE jobs 
                    SET state = 'dead', attempts = ?, updated_at = datetime('now'), run_after = NULL, locked_by = NULL, heartbeat_at = NULL
                    WHERE id = ?
                """, (attempts, job['id']))
            else:
                conn.execute(f"""
                    UPDATE jobs 
                    SET state = 'failed', attempts = ?, updated_at = datetime('now'), run_after = {run_after}, locked_by = NULL, heartbeat_at = NULL
                    WHERE id = ?
                """, (attempts, job['id']))

def heartbeat_worker(conn, pid):
    while not shutdown_requested:
        try:
            with conn:
                conn.execute("UPDATE workers SET heartbeat_at = datetime('now') WHERE pid = ?", (pid,))
        except Exception:
            pass
        time.sleep(15)

def heartbeat_job(conn, job_id):
    try:
        with conn:
            conn.execute("UPDATE jobs SET heartbeat_at = datetime('now') WHERE id = ?", (job_id,))
    except Exception:
        pass

def run_worker(count=1):
    if count > 1:
        children = []
        for _ in range(count - 1):
            child_pid = os.fork()
            if child_pid == 0:
                # Child process
                run_single_worker()
                os._exit(0)
            else:
                children.append(child_pid)
        # Parent also acts as a worker
        run_single_worker()
        for child in children:
            os.waitpid(child, 0)
    else:
        run_single_worker()

def run_single_worker():
    pid = os.getpid()
    conn = get_connection()
    
    # Register this worker
    with conn:
        conn.execute(
            "INSERT INTO workers (pid, status, started_at, heartbeat_at) VALUES (?, 'active', datetime('now'), datetime('now')) "
            "ON CONFLICT(pid) DO UPDATE SET status='active', heartbeat_at=datetime('now')",
            (pid,)
        )

    hb_thread = threading.Thread(target=heartbeat_worker, args=(conn, pid), daemon=True)
    hb_thread.start()

    try:
        while not shutdown_requested:
            recover_stale_jobs(conn, pid)

            # Attempt to claim a job atomically.
            with conn:
                cur = conn.cursor()
                cur.execute("""
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
                """, (str(pid),))
                
                job = cur.fetchone()
            
            if not job:
                time.sleep(1)
                continue

            job_id = job['id']
            command = job['command']
            attempts = job['attempts']
            max_retries = job['max_retries']

            # Run a job heartbeat thread to keep the job alive
            job_hb_stop = threading.Event()
            def job_hb():
                while not job_hb_stop.is_set():
                    heartbeat_job(conn, job_id)
                    job_hb_stop.wait(15)
            
            jhb = threading.Thread(target=job_hb, daemon=True)
            jhb.start()

            # Execute command
            # Using shell=True as per requirements
            proc = subprocess.Popen(command, shell=True)
            
            # Wait for it, checking for shutdown occasionally
            while proc.poll() is None:
                if shutdown_requested:
                    # Requirements say: "Graceful shutdown: on worker stop or Ctrl+C, finish the in-flight job."
                    # So we just wait for it to finish.
                    proc.wait()
                    break
                time.sleep(0.1)

            job_hb_stop.set()
            jhb.join()

            exit_code = proc.returncode

            with conn:
                if exit_code == 0:
                    conn.execute("""
                        UPDATE jobs 
                        SET state = 'completed', updated_at = datetime('now'), locked_by = NULL, heartbeat_at = NULL
                        WHERE id = ?
                    """, (job_id,))
                else:
                    new_attempts = attempts + 1
                    if new_attempts > max_retries:
                        conn.execute("""
                            UPDATE jobs 
                            SET state = 'dead', attempts = ?, updated_at = datetime('now'), locked_by = NULL, heartbeat_at = NULL
                            WHERE id = ?
                        """, (new_attempts, job_id))
                    else:
                        base = int(get_config('backoff-base', '2'))
                        delay = base ** attempts
                        conn.execute(f"""
                            UPDATE jobs 
                            SET state = 'failed', attempts = ?, updated_at = datetime('now'), 
                                run_after = datetime('now', '+{delay} seconds'), locked_by = NULL, heartbeat_at = NULL
                            WHERE id = ?
                        """, (new_attempts, job_id))
    finally:
        try:
            with conn:
                conn.execute("DELETE FROM workers WHERE pid = ?", (pid,))
        except Exception:
            pass
