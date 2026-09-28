"""
Synchrono Service — grading, konfigurasi, dan matching TANPA Langflow.

DUA MUKA API, SATU LOGIKA

  Kontrak Langflow   /api/v1/login, /api/v1/api_key/, /api/v1/run/{flow}
                     Persis Langflow — autentikasi, payload `tweaks`, selubung
                     balasan, bahkan kode status galatnya. Inilah yang dipakai
                     PORTAL: ia bisa dipindah dari Langflow ke service ini
                     tanpa mengubah kode (lihat rute_langflow.py).

  REST service       /api/v1/grading/..., /api/v1/config/..., /api/v1/matching/run
                     Bentuk lama service pembanding: badan JSON langsung, kode
                     status bermakna. Dipertahankan untuk perkakas benchmark
                     (beban/, infra/uji_asap.py).

Keduanya menjalankan logika yang sama: `langflow-synchrono/lib/` dan kelas
komponen di `langflow-synchrono/components/`, dimuat langsung — tidak ada
salinan yang bisa tertinggal (lihat alur.py).
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from _kolam import MODE, KolamPenuh, kolam, pinjam

from . import akses, alur

MULAI = time.time()
PANASKAN = int(os.getenv("WARMUP_CONNECTIONS", "2"))


@asynccontextmanager
async def daur(app: FastAPI):
    print(f"[start] mode koneksi DuckDB: {MODE}")
    if not akses.AUTH_AKTIF:
        print("[start] PEMERIKSAAN x-api-key MATI (SERVICE_AUTH=off) — hanya untuk mesin lokal")
    else:
        print(f"[start] x-api-key: {len(akses.KUNCI_STATIS)} kunci dari SERVICE_API_KEY "
              f"+ kunci di tabel {akses.TABEL}")
        if not akses.SANDI:
            print("[start] SERVICE_SUPERUSER_PASSWORD kosong — /api/v1/login MATI; "
                  "hanya kunci dari SERVICE_API_KEY yang berlaku")

    for endpoint, keadaan in alur.periksa_semua().items():
        print(f"[start] flow {endpoint:22s} {keadaan}")

    try:
        kolam.panaskan(PANASKAN)
        print(f"[start] {PANASKAN} koneksi dipanaskan")
        akses.pastikan_tabel()
        print(f"[start] tabel {akses.TABEL} siap")
    except Exception as e:  # noqa: BLE001
        # Sengaja tidak menggagalkan start. PostgreSQL yang belum siap adalah
        # keadaan normal saat semuanya dinaikkan bersamaan; endpoint kesehatan
        # tetap harus bisa menjawab supaya keadaan itu TERLIHAT. Tabel kunci
        # dibuat nanti, pada pemakaian pertama.
        print(f"[start] pemanasan gagal, lanjut tanpa itu: {type(e).__name__}: {e}")
    akses.mulai_penyiram()
    yield
    try:
        akses.siram_pemakaian()
    except Exception:  # noqa: BLE001
        pass
    kolam.tutup()


app = FastAPI(
    title="Synchrono Service",
    description=__doc__,
    version="2.0.0",
    lifespan=daur,
)


# ── Penanganan galat ───────────────────────────────────────────────────────

@app.exception_handler(KolamPenuh)
async def kolam_penuh(request: Request, e: KolamPenuh):
    # 503 + Retry-After, bukan 500: keadaan sesaat, klien boleh mencoba lagi.
    return JSONResponse({"error": "SERVICE_BUSY", "detail": str(e)},
                        status_code=503, headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def galat_lain(request: Request, e: Exception):
    ringkas = " ".join(str(e).split())[:500]
    print(f"[500] {request.method} {request.url.path} -> {type(e).__name__}: {ringkas}")
    # `detail`, seperti Langflow — portal membaca kunci itu.
    return JSONResponse({"error": type(e).__name__, "detail": ringkas}, status_code=500)


# ── Kesehatan ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["kesehatan"], summary="Hidup atau tidak")
def health() -> dict:
    """
    TIDAK menyentuh PostgreSQL maupun S3 — probe container tidak boleh
    menyatakan service mati hanya karena basis data sedang tidak terjangkau.
    `status` sama dengan `/health` Langflow; kunci lainnya tambahan.
    """
    return {
        "status": "ok",
        "uptimeSeconds": round(time.time() - MULAI, 1),
        "duckdbMode": MODE,
        "idleConnections": kolam.menganggur,
        "python": sys.version.split()[0],
    }


@app.get("/health/db", tags=["kesehatan"], summary="PostgreSQL terjangkau atau tidak")
def health_db() -> dict:
    mulai = time.perf_counter()
    with pinjam() as con:
        n = con.execute("SELECT count(*) FROM pg.grading_jobs").fetchone()[0]
    return {"status": "ok", "gradingJobs": n,
            "roundtripMs": round((time.perf_counter() - mulai) * 1000, 2)}


# ── Rute ───────────────────────────────────────────────────────────────────

from .rute_config import rute as rute_config      # noqa: E402
from .rute_grading import rute as rute_grading    # noqa: E402
from .rute_langflow import rute as rute_langflow  # noqa: E402
from .rute_matching import rute as rute_matching  # noqa: E402

# Rute Langflow memasang pemeriksaannya sendiri per endpoint: login dan
# /health_check terbuka, api_key memakai bearer, run memakai x-api-key.
app.include_router(rute_langflow)

# REST: kunci diperiksa di tingkat router. Satu endpoint baru yang lupa diberi
# dependency akan terbuka tanpa ada yang menyadarinya.
for r in (rute_grading, rute_config, rute_matching):
    app.include_router(r, dependencies=[Depends(akses.dependensi_kunci_rest)])
