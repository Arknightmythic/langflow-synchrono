"""Benchmark harness: callback receiver + portal job preparation for the DuckDB service."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import duckdb

PORTAL_DSN = os.getenv("PORTAL_PG_DSN", "")
CALLBACKS: dict[str, dict] = {}
LOCK = threading.Lock()


def portal():
    con = duckdb.connect()
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PORTAL_DSN}' AS portal (TYPE postgres)")
    return con


def literal(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def prepare(body: dict) -> dict:
    sql = (f"INSERT INTO syncrono_matching_job (id, file_id, master_file_id, status, created_at, "
           f"created_by) VALUES ({literal(body['jobId'])}, {literal(body['fileId'])}, "
           f"{literal(body['masterFileId'])}, 'PENDING', now(), 'bench') "
           f"ON CONFLICT (id) DO NOTHING")
    con = portal()
    try:
        con.execute(f"CALL postgres_execute('portal', {literal(sql)})")
    finally:
        con.close()
    return {"prepared": body["jobId"]}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_POST(self):  # noqa: N802
        body = self._body()
        if self.path.startswith("/prepare"):
            try:
                return self._send(200, prepare(body))
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
        key = body.get("jobId") or body.get("fileId") or self.path
        with LOCK:
            CALLBACKS[key] = {"receivedAt": time.time(), "path": self.path, "body": body}
        print(f"[cb] {self.path} {key} {body.get('status')}", flush=True)
        self._send(200, {"ok": True})

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, {"ok": True, "callbacks": len(CALLBACKS)})
        key = self.path.rsplit("/", 1)[-1]
        with LOCK:
            item = CALLBACKS.get(key)
        if item is None:
            return self._send(404, {"found": False})
        self._send(200, {"found": True, **item})

    def log_message(self, *args):  # noqa: D102
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
