"""
Pengukur CPU & memori per container, lewat Docker API.

KENAPA BUKAN cADVISOR

cAdvisor adalah pilihan pertama dan sudah dicoba, tapi ia TIDAK BISA menghitung
container di Docker Desktop mesin ini. Gejalanya menyesatkan: container-nya
sehat, Prometheus melaporkan targetnya `up`, dan yang keluar hanya satu seri —
`container_memory_working_set_bytes{id="/"}` — cgroup akar, tanpa satu pun
container. Sebabnya terbaca di lognya:

    Failed to create existing container: /docker/<id>: failed to identify the
    read-write layer ID — open /rootfs/var/lib/docker/image/overlayfs/layerdb/
    mounts/<id>/mount-id: no such file or directory

Docker Desktop menyimpan image-nya lewat containerd, sehingga folder
`image/overlayfs/layerdb` yang dicari cAdvisor memang tidak ada. Dicoba tanpa
mount `/var/lib/docker` dan dengan `/sys/fs/cgroup` ditambahkan — hasilnya sama.

Yang dibutuhkan cuma dua angka untuk beberapa container, dan Docker API sudah
menyediakannya persis. Delapan puluh baris jauh lebih murah daripada melawan
cAdvisor di lingkungan yang tidak didukungnya.

NAMA METRIKNYA SENGAJA MENIRU cADVISOR

`container_memory_working_set_bytes` dan `container_cpu_usage_seconds_total`,
dengan label `name`. Jadi dasbor Grafana, contoh query yang beredar, dan
kebiasaan orang tetap berlaku — dan kalau suatu saat cAdvisor bisa dipakai
(mis. saat dipasang di server Linux sungguhan), ia tinggal menggantikan ini
tanpa satu pun panel yang perlu diubah.

Angkanya SAMA dengan yang ditampilkan `docker stats`, karena sumbernya memang
endpoint yang sama.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
PORT = int(os.getenv("PORT", "9200"))

# Container mana yang diukur. Regex, dicocokkan ke nama container.
POLA = re.compile(os.getenv("POLA_NAMA", r"^(synchrono-|bench-)"))


class KoneksiSoket(http.client.HTTPConnection):
    """HTTPConnection di atas unix socket — Docker API tidak dengar di TCP."""

    def __init__(self, jalur: str, timeout: float = 10.0):
        super().__init__("localhost", timeout=timeout)
        self.jalur = jalur

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.jalur)
        self.sock = s


def docker(jalur: str):
    con = KoneksiSoket(SOKET)
    try:
        con.request("GET", jalur)
        r = con.getresponse()
        isi = r.read()
        if r.status != 200:
            raise RuntimeError(f"docker {jalur} -> {r.status}: {isi[:200]!r}")
        return json.loads(isi)
    finally:
        con.close()


def container() -> list[tuple[str, str]]:
    hasil = []
    for c in docker("/containers/json"):
        for n in c.get("Names", []):
            nama = n.lstrip("/")
            if POLA.search(nama):
                hasil.append((c["Id"], nama))
                break
    return sorted(hasil, key=lambda x: x[1])


def ukur(cid: str) -> dict | None:
    """
    `one-shot=true` PENTING.

    Tanpanya, Docker menahan balasan selama satu detik penuh untuk menghitung
    delta CPU-nya sendiri. Dengan lima container dan scrape tiap lima detik,
    penundaan itu saja sudah membuat scrape tidak pernah selesai tepat waktu.
    Delta-nya tidak dibutuhkan di sini: yang diekspor adalah pencacah kumulatif,
    dan Prometheus yang menghitung laju lewat rate().
    """
    try:
        return docker(f"/containers/{cid}/stats?stream=false&one-shot=true")
    except Exception:  # noqa: BLE001 — container bisa berhenti di tengah scrape
        return None


def baris(nama: str, s: dict) -> list[str]:
    mem = s.get("memory_stats") or {}
    cpu = ((s.get("cpu_stats") or {}).get("cpu_usage") or {})
    stat = mem.get("stats") or {}

    pakai = mem.get("usage") or 0
    # working set = yang dipakai dikurangi page cache yang bisa dilepas kapan
    # saja. Inilah yang ditampilkan `docker stats`, dan inilah yang menentukan
    # berapa memori yang benar-benar dibutuhkan.
    tak_aktif = stat.get("inactive_file", stat.get("total_inactive_file", 0)) or 0
    ws = max(0, pakai - tak_aktif)

    # Docker melaporkan CPU dalam nanodetik. Prometheus mengharapkan detik.
    detik = (cpu.get("total_usage") or 0) / 1e9

    label = f'{{name="{nama}"}}'
    return [
        f"container_memory_working_set_bytes{label} {ws}",
        f"container_memory_usage_bytes{label} {pakai}",
        f"container_spec_memory_limit_bytes{label} {mem.get('limit') or 0}",
        f"container_cpu_usage_seconds_total{label} {detik:.6f}",
        f"container_last_seen{label} {int(time.time())}",
    ]


BANTUAN = [
    "# HELP container_memory_working_set_bytes Memori yang benar-benar dipegang container.",
    "# TYPE container_memory_working_set_bytes gauge",
    "# HELP container_memory_usage_bytes Memori terpakai termasuk page cache.",
    "# TYPE container_memory_usage_bytes gauge",
    "# HELP container_spec_memory_limit_bytes Batas memori container.",
    "# TYPE container_spec_memory_limit_bytes gauge",
    "# HELP container_cpu_usage_seconds_total Total waktu CPU kumulatif.",
    "# TYPE container_cpu_usage_seconds_total counter",
    "# HELP container_last_seen Kapan container terakhir terlihat.",
    "# TYPE container_last_seen gauge",
]


def kumpulkan() -> str:
    keluaran = list(BANTUAN)
    for cid, nama in container():
        s = ukur(cid)
        if s:
            keluaran += baris(nama, s)
    return "\n".join(keluaran) + "\n"


class Penangan(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — nama wajib dari BaseHTTPRequestHandler
        if self.path.rstrip("/") not in ("/metrics", ""):
            self.send_error(404)
            return
        try:
            isi = kumpulkan().encode()
        except Exception as e:  # noqa: BLE001
            self.send_error(500, str(e)[:200])
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(isi)))
        self.end_headers()
        self.wfile.write(isi)

    def log_message(self, *a):
        """Diam. Prometheus men-scrape tiap 5 detik; lognya tidak berguna."""


if __name__ == "__main__":
    print(f"pengukur di :{PORT}, soket {SOKET}, pola {POLA.pattern}", flush=True)
    for cid, nama in container():
        print(f"  mengukur {nama}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Penangan).serve_forever()
