"""
Endpoint dengan kontrak Langflow — yang dipanggil portal.

    POST   /api/v1/login                   form  -> token bearer
    GET    /api/v1/api_key/                bearer -> daftar kunci (tersamar)
    POST   /api/v1/api_key/                bearer -> kunci baru
    DELETE /api/v1/api_key/{id}            bearer
    POST   /api/v1/run/{endpoint|flow_id}  x-api-key -> selubung Langflow
    GET    /health_check

Enam flow tersedia, dengan node id yang sama persis dengan Langflow:

    grading-dispatch      GradingDispatch-a3967
    grading-status        GradingStatus-3cc03
    grading               OpenGradingSession-ab8d5   (pipeline sinkron)
    config-rules          GradingRuleGet-9c9c5
    config-rules-update   GradingRuleUpdate-ea0f7
    matching-dispatch     MatchingDispatch-b4819

KODE STATUS SENGAJA SAMA DENGAN LANGFLOW, TERMASUK YANG JANGGAL

Galat di dalam komponen dibalas 500 dengan `detail` berisi STRING JSON; kunci
yang tak ada 403, bukan 401; flow yang tak dikenal 404. Semuanya direkam dari
Langflow yang jalan pada 28 Sep 2026. Endpoint REST milik service ini
(`/api/v1/grading/...`) memakai kode yang lebih bermakna — tapi portal sudah
ditulis untuk perilaku Langflow, dan justru perilaku itulah yang harus sama.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from _kolam import pinjam

from . import akses, alur

rute = APIRouter()


# ── Autentikasi ────────────────────────────────────────────────────────────

@rute.post("/api/v1/login", tags=["auth"], summary="Login, dapat token bearer")
async def login(request: Request):
    """
    Form `application/x-www-form-urlencoded`: `username`, `password`.

    Diurai sendiri, bukan lewat `Form(...)` FastAPI yang menuntut paket
    python-multipart. Balasan galatnya ditiru dari Langflow: 422 untuk field
    yang hilang, 401 + `WWW-Authenticate: Bearer` untuk sandi salah.
    """
    mentah = (await request.body()).decode(errors="replace")
    form = parse_qs(mentah, keep_blank_values=True)
    kurang = [f for f in ("username", "password") if f not in form]
    if kurang:
        return JSONResponse(status_code=422, content={"detail": [
            {"type": "missing", "loc": ["body", f], "msg": "Field required", "input": None}
            for f in kurang]})
    hasil = akses.login(form["username"][0], form["password"][0])
    if hasil is None:
        return JSONResponse(status_code=401, content={"detail": "Incorrect username or password"},
                            headers={"WWW-Authenticate": "Bearer"})
    return hasil


@rute.get("/api/v1/api_key/", tags=["auth"], dependencies=[Depends(akses.dependensi_bearer)],
          summary="Daftar API key (nilainya tersamar)")
def daftar_kunci():
    return akses.daftar_kunci()


@rute.post("/api/v1/api_key/", tags=["auth"], dependencies=[Depends(akses.dependensi_bearer)],
           summary="Buat API key — nilainya hanya muncul di balasan ini")
def buat_kunci(badan: dict | None = Body(default=None)):
    nama = (badan or {}).get("name")
    return akses.buat_kunci(None if nama is None else str(nama))


@rute.delete("/api/v1/api_key/{api_key_id}", tags=["auth"],
             dependencies=[Depends(akses.dependensi_bearer)], summary="Hapus API key")
def hapus_kunci(api_key_id: str):
    if not akses.hapus_kunci(api_key_id):
        raise HTTPException(status_code=404, detail="API Key not found")
    return {"detail": "API Key deleted"}


# ── Menjalankan flow ───────────────────────────────────────────────────────

@rute.post("/api/v1/run/{flow_id_or_name}", tags=["langflow"],
           dependencies=[Depends(akses.dependensi_kunci)],
           summary="Jalankan flow — kontrak persis Langflow")
def run(flow_id_or_name: str,
        badan: dict | None = Body(default=None),
        stream: bool = Query(default=False,
                             description="Diterima demi kecocokan; balasan selalu utuh.")):
    """
    Badan: `{"output_type", "input_type", "input_value", "tweaks", "session_id"}`.
    Yang dipakai hanya `tweaks` (dikunci id node) — flow Synchrono tidak punya
    ChatInput, jadi `input_value` diabaikan, sama seperti di Langflow.
    """
    flow = alur.cari(flow_id_or_name)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow identifier {flow_id_or_name} not found")

    badan = badan or {}
    tweaks = badan.get("tweaks") or {}
    if not isinstance(tweaks, dict):
        raise HTTPException(status_code=422, detail="tweaks harus objek JSON {id_node: {...}}")

    mulai = time.perf_counter()
    try:
        teks = alur.jalankan(flow, tweaks)
    except alur.GalatKomponen as e:
        print(f"[run] {flow.endpoint} GAGAL ({(time.perf_counter() - mulai) * 1000:.0f} ms) "
              f"di {e.nama_tampilan}: {type(e.galat).__name__}: {e.galat}")
        return JSONResponse(status_code=500, content={"detail": e.detail()})

    print(f"[run] {flow.endpoint} ok ({(time.perf_counter() - mulai) * 1000:.0f} ms)")
    return alur.bungkus(flow, teks, badan.get("input_value"), badan.get("session_id"))


# ── Kesehatan, bentuk Langflow ─────────────────────────────────────────────

@rute.get("/health_check", tags=["kesehatan"], summary="Bentuk Langflow: status, chat, db")
def health_check():
    """
    `db` di sini PostgreSQL engine — padanan basis data Langflow, tempat
    service ini menyimpan job dan API key. Tidak sehat -> 500, seperti Langflow.
    """
    try:
        with pinjam() as con:
            con.execute("SELECT 1 FROM pg.grade_rules LIMIT 1").fetchall()
        db = "ok"
    except Exception as e:  # noqa: BLE001
        db = f"error: {type(e).__name__}: {' '.join(str(e).split())[:300]}"
    isi = {"status": "ok" if db == "ok" else "nok", "chat": "ok", "db": db}
    return isi if db == "ok" else JSONResponse(status_code=500, content=isi)
