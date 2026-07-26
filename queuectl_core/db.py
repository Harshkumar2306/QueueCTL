import sqlite3
import os
import json
from datetime import datetime

# Default db path
DEFAULT_DIR = os.path.expanduser("~/.queuectl")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DIR, "queue.db")

def get_db_path():
    return os.environ.get("QUEUECTL_DB_PATH", DEFAULT_DB_PATH)

def get_connection(db_path=None):
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # timeout=10 is for busy waiting when locked.
    # isolation_level=None enables autocommit, but we will explicitly control transactions using 'BEGIN EXCLUSIVE'
    # when we need atomicity for claiming jobs.
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=None):
    conn = get_connection(db_path)
    # Check if initialized to avoid write locks on every CLI call
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'")
    if cur.fetchone():
        conn.close()
        return

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', -- pending, processing, completed, failed, dead
                attempts INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                run_after TIMESTAMP,
                locked_by TEXT,
                heartbeat_at TIMESTAMP
            )
        """)
        # Index to make job polling fast
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_state_run_after 
            ON jobs (state, run_after)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # Populate default configs if not exists
        conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('max-retries', '3')")
        conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('backoff-base', '2')")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                pid INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                heartbeat_at TIMESTAMP NOT NULL
            )
        """)
    conn.close()

def get_config(key, default, db_path=None):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default

def set_config(key, value, db_path=None):
    conn = get_connection(db_path)
    with conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
