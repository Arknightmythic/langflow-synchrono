"""
Endpoint grading.

    POST /api/v1/grading/jobs             kirim job          -> 202
    GET  /api/v1/grading/jobs/{fileId}    polling status     -> 200
    GET  /api/v1/grading/jobs/by-id/{id}  status satu job    -> 200
    POST /api/v1/grading/run              pipeline sinkron   -> 200

LOGIKANYA TIDAK DITULIS ULANG. Modul `_grading`, `_jobs`, dan `_worker` yang
dipakai di sini adalah berkas yang SAMA PERSIS dengan yang dipakai node
Langflow — satu folder `lib/`, di-mount ke dua container. Itu bukan kebetulan
yang menyenangkan, melainkan syarat supaya perbandingannya berarti: kalau
logikanya dua salinan, selisih waktu yang terukur bisa saja datang dari
perbedaan logika, bukan dari perbedaan platform.

Yang berbeda hanyalah lapisan yang membungkusnya:

    Langflow   flow -> node -> tweaks -> Message(text=json.dumps(...))
               -> dibungkus outputs[0].outputs[0].results.message.text
    Di sini    fungsi -> dict -> JSONResponse

Bentuk badan balasannya sengaja dibuat IDENTIK dengan isi string di dalam
selubung Langflow, jadi portal cukup membuang satu baris `JSON.parse` dan
sisanya tetap jalan.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Query, Response, status

from _grading import jalankan_penuh
from _jobs import ambil_job, panen_mangkrak, susun_job
from _kolam import pinjam

from . import alur
from .skema import MuatanGrading

rute = APIRouter(prefix="/api/v1/grading", tags=["grading"])


@rute.post("/jobs", status_code=status.HTTP_202_ACCEPTED,
           summary="Kirim job grading (asinkron)")
def kirim(muatan: MuatanGrading, response: Response) -> dict:
    """
    Catat job, lepas pekerja latar belakang, langsung balas 202.

    Balasannya harus datang di bawah 5 detik menurut spesifikasi, sementara
    grading sendiri makan puluhan detik sampai menit. Karena itu di sini tidak
    ada grading sama sekali — hanya satu INSERT dan satu thread dilepas.

    Perbedaan nyata dari versi Langflow: 202, bukan 200. Langflow membalas 200
    untuk apa pun, termasuk untuk galat, jadi portal tidak bisa memutuskan
    apa-apa dari kode statusnya dan harus selalu mengurai badan balasan.

    Dijalankan lewat komponen `GradingDispatch` yang SAMA dengan Langflow,
    bukan disusun ulang di sini. Versi sebelumnya menyusun sendiri, dan
    tertinggal: format yang harus ditolak di depan (`.xls`, `.mdf` tanpa
    konverter, ...) lolos, lalu gagal beberapa detik kemudian di pekerja.
    """
    flow = alur.ALUR["grading-dispatch"]
    try:
        teks = alur.jalankan(flow, {flow.simpul[0].node_id: {
            "payload": json.dumps(muatan.model_dump(exclude_none=True), ensure_ascii=False)}})
    except alur.GalatKomponen as e:
        if isinstance(e.galat, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e.galat)) from e
        raise e.galat from e

    hasil = json.loads(teks)
    response.headers["Location"] = f"/api/v1/grading/jobs/{hasil['fileId']}"
    return hasil


def _jawab(job: dict | None, file_id: str | None, job_id: str | None) -> dict:
    if job is None:
        return {
            "found": False,
            "status": "NOT_FOUND",
            "fileId": file_id,
            "jobId": job_id,
            "message": "Belum ada job grading untuk berkas ini.",
        }
    return {
        "found": True,
        # QUEUED | RUNNING | COMPLETED | FAILED
        "status": job["status"],
        "jobId": job["job_id"],
        "fileId": job["file_id"],
        # Selesai atau tidak — satu boolean supaya portal tidak perlu menghafal
        # daftar status untuk memutuskan kapan berhenti polling.
        "done": job["status"] in ("COMPLETED", "FAILED"),
        "stage": job["stage"],
        "queuedAt": job["created_at"],
        "startedAt": job["started_at"],
        "finishedAt": job["finished_at"],
        "gradingDurationMs": job["grading_duration_ms"],
        "enrichedParquetKey": job["enriched_key"],
        "parquetSizeBytes": job["parquet_size_bytes"],
        "recordCount": job["row_count"],
        "callbackStatus": job["callback_status"],
        "callbackError": job["callback_error"],
        "error": job["error"],
        "result": job["result"],
    }


def _ambil(file_id: str | None, job_id: str | None, panen: bool) -> dict:
    with pinjam() as con:
        # Memanen di sini yang membuat job mangkrak ketahuan: pekerja adalah
        # thread di dalam proses ini, jadi proses yang restart di tengah jalan
        # meninggalkan job RUNNING tanpa ada yang mengerjakannya. Detak yang
        # berhenti terbaca pada polling berikutnya.
        if panen:
            panen_mangkrak(con)
        job = ambil_job(con, file_id=file_id, job_id=job_id)
    return _jawab(job, file_id, job_id)


@rute.get("/jobs/by-id/{job_id}", summary="Status satu job tertentu")
def status_job(job_id: str, panen: bool = Query(default=True)) -> dict:
    return _ambil(None, job_id, panen)


@rute.get("/jobs/{file_id}", summary="Status job terbaru untuk satu berkas")
def status_berkas(file_id: str, panen: bool = Query(
        default=True,
        description="Setel false untuk melewati pemanenan job mangkrak. "
                    "Mengubah polling jadi baca murni — berguna saat mengukur beban.",
)) -> dict:
    """
    Inilah yang dipanggil berulang-ulang oleh portal selama menunggu.

    Karena endpoint inilah yang paling sering dipanggil di antara semuanya,
    dialah yang paling menentukan beban service — dan karena itu dia yang jadi
    skenario utama di k6.

    `result` membawa muatan callback yang SAMA PERSIS dengan yang dikirim lewat
    webhook, jadi portal cukup punya satu jalur penanganan hasil apa pun cara
    ia mengetahuinya.
    """
    return _ambil(file_id, None, panen)


@rute.post("/run", summary="Jalankan pipeline grading dan tunggu (sinkron)")
def jalankan_sinkron(muatan: MuatanGrading) -> dict:
    """
    Setara endpoint `grading` di Langflow: enam tahap berurutan, MEMBLOKIR
    sampai selesai, tanpa menyentuh tabel `grading_jobs`.

    Ada untuk penelusuran dan untuk mengukur waktu grading murni tanpa
    tercampur antrean dan polling. Portal memakai `POST /jobs`, bukan ini.
    """
    try:
        job = susun_job(muatan.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    mulai = time.perf_counter()
    keluaran = jalankan_penuh(job)
    hasil = keluaran["hasil"]
    hasil["gradingDurationMs"] = int((time.perf_counter() - mulai) * 1000)
    return hasil
