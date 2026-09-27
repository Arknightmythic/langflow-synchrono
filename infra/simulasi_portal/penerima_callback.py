"""
Tiruan endpoint callback portal: POST /api/internal/matching/callback.

Mencatat setiap callback ke callback_diterima.jsonl dan membalas persis seperti
spesifikasi §7.2.
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CATATAN = "callback_diterima.jsonl"


class Penerima(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        badan = json.loads(self.rfile.read(n) or b"{}")
        with open(CATATAN, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "waktu": time.strftime("%H:%M:%S"),
                "jalur": self.path,
                "x-callback-source": self.headers.get("x-callback-source"),
                "badan": badan,
            }, ensure_ascii=False) + "\n")
        balas = json.dumps({
            "success": True, "jobId": badan.get("jobId"),
            "message": "Callback matching berhasil diterima. Status job telah diperbarui.",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(balas)))
        self.end_headers()
        self.wfile.write(balas)
        print(f"[portal] callback diterima: jobId={badan.get('jobId')} status={badan.get('status')}",
              flush=True)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("[portal] penerima callback di :3999", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 3999), Penerima).serve_forever()
