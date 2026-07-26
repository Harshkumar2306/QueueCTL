import http.server
import json
import os
import sqlite3
from urllib.parse import urlparse
from .db import get_connection

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default logging to keep CLI clean
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path.startswith("/api/"):
            self.handle_api(path)
        else:
            self.serve_static(path)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/dlq/retry":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                job_id = data.get('id')
                if job_id:
                    self.retry_job(job_id)
                else:
                    self.send_error(400, "Missing job id")
            except Exception as e:
                self.send_error(400, str(e))
        elif path == "/api/dlq/purge":
            conn = get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM jobs WHERE state = 'dead'")
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
        elif path == "/api/enqueue":
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(body)
                command = data.get('command')
                if not command:
                    self.send_error(400, "Missing command")
                    return
                
                import uuid
                job_id = f"job-{uuid.uuid4().hex[:8]}"
                
                from .db import get_config
                max_retries = int(get_config('max-retries', '3'))
                
                conn = get_connection()
                with conn:
                    conn.execute(
                        "INSERT INTO jobs (id, command, state, created_at, updated_at, attempts, max_retries) VALUES (?, ?, 'pending', datetime('now'), datetime('now'), 0, ?)",
                        (job_id, command, max_retries)
                    )
                
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "id": job_id}).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    def retry_job(self, job_id):
        conn = get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE jobs SET state = 'pending', attempts = 0, run_after = NULL, locked_by = NULL WHERE id = ? AND state = 'dead'",
                    (job_id,)
                )
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
        except Exception as e:
            self.send_error(500, str(e))
            
    def handle_api(self, path):
        conn = get_connection()
        cur = conn.cursor()
        
        if path == "/api/stats":
            # Get job counts
            cur.execute("SELECT state, COUNT(*) as c FROM jobs GROUP BY state")
            counts = {row['state']: row['c'] for row in cur.fetchall()}
            for state in ['pending', 'processing', 'completed', 'failed', 'dead']:
                if state not in counts:
                    counts[state] = 0
            
            # Get workers
            cur.execute("SELECT pid, status, started_at, heartbeat_at FROM workers WHERE status = 'active'")
            workers = [dict(row) for row in cur.fetchall()]
            
            self.send_json({"counts": counts, "workers": workers})
            
        elif path == "/api/jobs":
            cur.execute("SELECT id, command, state, created_at, attempts, locked_by FROM jobs ORDER BY created_at DESC LIMIT 50")
            jobs = [dict(row) for row in cur.fetchall()]
            self.send_json({"jobs": jobs})
            
        elif path == "/api/dlq":
            cur.execute("SELECT id, command, created_at, updated_at, attempts FROM jobs WHERE state = 'dead' ORDER BY updated_at DESC")
            jobs = [dict(row) for row in cur.fetchall()]
            self.send_json({"dlq": jobs})
        else:
            self.send_error(404)

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
            
        # Security: only allow specific files
        allowed_files = {
            "/index.html": "text/html",
            "/style.css": "text/css",
            "/app.js": "application/javascript"
        }
        
        if path not in allowed_files:
            self.send_error(404)
            return
            
        web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')
        file_path = os.path.join(web_dir, path.lstrip('/'))
        
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-type", allowed_files[path])
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(404)

def run_server(port=8080):
    server_address = ('', port)
    httpd = http.server.ThreadingHTTPServer(server_address, DashboardHandler)
    print(f"QueueCTL Dashboard running at http://localhost:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard...")
        httpd.server_close()
