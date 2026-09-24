"""
Layanan konversi jalur B — HTTP tipis di depan `_pulih.py`.

    POST /konversi   { jobId, s3Bucket, sourceKey, targetKey, ... }  -> { ok, rowCount, ... }
    GET  /sehat                                                      -> { ok, antre }

KENAPA HTTP, DAN KENAPA SESEDERHANA INI

Pemanggilnya cuma satu: pekerja grading di Langflow. Tidak ada pemakai luar,
tidak ada browser, tidak ada autentikasi yang perlu dirundingkan — layanan ini
hanya bisa dihubungi dari jaringan internal compose. Jadi `http.server` bawaan
Python sudah cukup, dan itu berarti NOL dependensi tambahan di luar duckdb.

S3 TANPA KLIEN S3

Parquetnya ditulis dengan `COPY ... TO 's3://...'` milik DuckDB, yang memang
mengalir. Dumpnya TIDAK dibaca dengan `read_blob()` — fungsi itu mengembalikan
satu objek bytes utuh, sehingga batas ukuran berkas jadi ditentukan RAM padahal
disknya menganggur ratusan gigabita. Unduhannya karena itu ditandatangani
sendiri dan mengalir ke disk; lihat `_s3.py`.

Keduanya memakai pustaka standar dan DuckDB saja, jadi image ini tetap kecil dan
daftar dependensinya tetap sependek engine grading.

ANTRE SATU DEMI SATU

`BATAS_PARALEL` bawaannya 1. Konversi memegang basis data, bukan sekadar kueri,
dan dua pemulihan berbarengan berebut memori yang sama. Yang menunggu tidak
digantung diam-diam: `/sehat` melaporkan panjang antreannya supaya sisi portal
bisa memberi tahu penggunanya, bukan menampilkan layar diam yang akan dilaporkan
sebagai kerusakan.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

from _s3 import unduh

# Mesin yang dijalankan container INI. Satu container per mesin basis data,
# bukan satu container berisi ketiganya: tiap mesin datang dengan image, cara
# inisialisasi, dan lisensinya sendiri, dan menggabungkannya berarti satu
# kegagalan menghentikan ketiga formatnya sekaligus.
MESIN = os.getenv("KONV_MESIN", "postgresql").strip().lower()

if MESIN == "sqlserver":
    from _pulih_mssql import konversi
elif MESIN == "oracle":
    from _pulih_oracle import konversi
else:
    from _pulih import konversi

PORT = int(os.getenv("KONV_PORT", "8390"))
BATAS_PARALEL = int(os.getenv("KONV_MAX_CONCURRENT", "1"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "seaweedfs:8333")
S3_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET = os.getenv("S3_SECRET_KEY", "")
S3_SSL = os.getenv("S3_USE_SSL", "false").lower() in ("1", "true", "yes")

# Batas ukuran dump. Sejak unduhannya MENGALIR ke disk (lihat `_s3.py`), batas
# ini bukan lagi soal RAM melainkan soal disk dan waktu pemulihan — keduanya
# jauh lebih longgar.
#
# Diukur pada data uji: 15,66 MB memuat 200.000 baris, jadi sekitar 82 bita per
# baris. Bawaan 4096 MB berarti kira-kira 52 juta baris; yang lebih besar dari
# itu hampir pasti salah kirim, bukan berkas kependudukan sungguhan.
#
# Diperiksa dua kali: dari `Content-Length` sebelum mengunduh, dan selagi
# mengalir untuk berjaga kalau panjangnya tidak dilaporkan dengan benar.
BATAS_MB = int(os.getenv("KONV_BATAS_MB", "4096"))

# Tempat berkas diunduh. Bawaannya /tmp, TAPI untuk SQL Server ia harus berada
# di filesystem yang sama dengan folder data — berkas .mdf dipindahkan ke sana
# untuk dilampirkan, dan `os.replace` lintas filesystem gagal dengan
# "Invalid cross-device link". Menyalinnya juga bisa, tapi itu berarti menyalin
# berkas basis data utuh tanpa alasan; mengunduh langsung ke tempatnya lebih
# murah dan lebih sedikit yang bisa salah.
KERJA_UNDUH = os.getenv("KONV_KERJA_UNDUH", "").strip() or None
if KERJA_UNDUH:
    os.makedirs(KERJA_UNDUH, exist_ok=True)

_slot = threading.Semaphore(BATAS_PARALEL)
_menunggu = 0
_kunci = threading.Lock()


def _koneksi_s3() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    # URL_STYLE 'path' wajib untuk SeaweedFS — ia tidak memakai virtual-host style.
    con.execute(f"""
        CREATE OR REPLACE SECRET s3 (
            TYPE s3, KEY_ID '{S3_KEY}', SECRET '{S3_SECRET}',
            ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path',
            USE_SSL {str(S3_SSL).lower()}
        )
    """)
    return con


def _kerjakan(m: dict) -> dict:
    wajib = ("jobId", "s3Bucket", "sourceKey", "targetKey")
    kurang = [k for k in wajib if not str(m.get(k) or "").strip()]
    if kurang:
        raise ValueError(f"Field wajib tidak ada: {', '.join(kurang)}")

    bucket = m["s3Bucket"].strip("/")
    sumber = f"s3://{bucket}/{m['sourceKey'].lstrip('/')}"
    tujuan = f"s3://{bucket}/{m['targetKey'].lstrip('/')}"

    con = _koneksi_s3()
    kerja = Path(tempfile.mkdtemp(prefix="konv-", dir=KERJA_UNDUH))
    lokal = kerja / {"sqlserver": "data.mdf",
                 "oracle": "data.dmp"}.get(MESIN, "dump.sql")
    pendamping = kerja / "data.ldf"
    parquet = kerja / "hasil.parquet"
    lapor = lambda t: print(f"[K] {m['jobId']} {t}", flush=True)  # noqa: E731
    try:
        # MENGALIR ke disk, bukan lewat memori. `read_blob()` DuckDB akan
        # mengembalikan satu objek bytes utuh, dan itu membuat batas ukuran
        # berkas ditentukan RAM (3,5 GB tersedia) alih-alih disk (900 GB
        # kosong) — sumber daya yang paling sedikit, untuk alasan yang tidak
        # ada. Lihat _s3.py.
        lapor("K1 unduh berkas")
        bita = unduh(S3_ENDPOINT, bucket, m["sourceKey"], str(lokal),
                     S3_KEY, S3_SECRET, ssl=S3_SSL,
                     batas_bita=BATAS_MB * 1024 * 1024)
        print(f"[K] {m['jobId']} diunduh {bita / 2**20:.1f} MB dari {sumber}",
              flush=True)

        if MESIN == "sqlserver":
            # `.ldf` OPSIONAL. Kalau portal mengirimkannya, ia dipakai dan
            # database dipulihkan ke keadaan yang konsisten. Kalau tidak,
            # lognya dicoba dibangun ulang — yang hanya berhasil bila database
            # itu dulu ditutup dengan bersih. Lihat `_pulih_mssql.py`.
            kunci_ldf = (m.get("logKey") or "").strip()
            punya_ldf = False
            if kunci_ldf:
                unduh(S3_ENDPOINT, bucket, kunci_ldf, str(pendamping),
                      S3_KEY, S3_SECRET, ssl=S3_SSL,
                      batas_bita=BATAS_MB * 1024 * 1024)
                punya_ldf = True
                print(f"[K] {m['jobId']} .ldf pendamping ikut diunduh", flush=True)
            hasil = konversi(
                str(lokal), m["jobId"], str(parquet),
                jalur_ldf=str(pendamping) if punya_ldf else None,
                tabel=(m.get("table") or None), lapor=lapor)
        else:
            hasil = konversi(
                str(lokal), m["jobId"], str(parquet),
                dialek=(m.get("dialect") or None),
                tabel=(m.get("table") or None), lapor=lapor)

        con.execute(f"""COPY (SELECT * FROM read_parquet('{parquet.as_posix()}'))
                        TO '{tujuan}' (FORMAT parquet)""")
        hasil["targetKey"] = m["targetKey"]
        print(f"[K] {m['jobId']} SELESAI — {hasil['row_count']:,} baris dari "
              f"tabel '{hasil['tabel']}' dalam {hasil['durasi_detik']}s", flush=True)
        return hasil
    finally:
        con.close()
        for p in (lokal, pendamping, parquet):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            kerja.rmdir()
        except OSError:
            pass


class Penangan(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _balas(self, kode: int, isi: dict) -> None:
        tubuh = json.dumps(isi, ensure_ascii=False).encode()
        self.send_response(kode)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(tubuh)))
        self.end_headers()
        self.wfile.write(tubuh)

    def log_message(self, *a) -> None:
        """Bisukan log akses bawaan; yang berarti sudah dicetak `_kerjakan`."""

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/sehat", ""):
            self._balas(200, {"ok": True, "mesin": MESIN, "antre": _menunggu,
                              "maxConcurrent": BATAS_PARALEL})
        else:
            self._balas(404, {"ok": False, "error": "tidak ada"})

    def do_POST(self) -> None:
        global _menunggu
        if self.path.rstrip("/") != "/konversi":
            self._balas(404, {"ok": False, "error": "tidak ada"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            m = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._balas(400, {"ok": False, "error": f"muatan bukan JSON sah: {e}"})
            return

        # Dihitung SEBELUM menunggu slot dan dikurangi TEPAT setelah dapat,
        # di `finally`-nya sendiri. Kalau pengurangannya menumpang pada jalur
        # sukses, permintaan yang gagal meninggalkan antrean yang terlihat
        # penuh selamanya — dan `/sehat` jadi berbohong.
        with _kunci:
            _menunggu += 1
        try:
            _slot.acquire()
        finally:
            with _kunci:
                _menunggu -= 1
        try:
            hasil = _kerjakan(m)
            self._balas(200, {"ok": True, **hasil})
        except Exception as e:  # noqa: BLE001
            print("[K] GAGAL:", traceback.format_exc(), flush=True)
            self._balas(422, {"ok": False, "error": str(e)})
        finally:
            _slot.release()


def main() -> None:
    print(f"[K] layanan konversi siap di :{PORT} — mesin {MESIN} "
          f"(paralel {BATAS_PARALEL}, batas {BATAS_MB} MB)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Penangan).serve_forever()


if __name__ == "__main__":
    main()
