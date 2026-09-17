"""
Status job grading — satu-satunya sumber kebenaran untuk "sudah selesai belum?".

KENAPA TABEL, BUKAN MEMORI PROSES

Grading berjalan di thread latar belakang di dalam container Langflow. Kalau
statusnya hanya disimpan di memori, container restart = job hilang tanpa jejak
dan backend Synchrono menunggu selamanya. Dengan tabel `grading_jobs` di
PostgreSQL, status bertahan melewati restart, dan job yang pekerjanya mati bisa
DIKENALI (lihat `panen_mangkrak`) alih-alih menggantung.

PENULISAN LEWAT postgres_execute, BUKAN INSERT DUCKDB

DuckDB menulis ke tabel PostgreSQL yang di-ATTACH memakai COPY, dan COPY
mengabaikan daftar kolom — kolom ber-DEFAULT akan terkirim NULL lalu ditolak.
Untuk penulisan baris tunggal seperti di sini, `postgres_execute()` menjalankan
SQL apa adanya di sisi PostgreSQL sehingga DEFAULT dan ON CONFLICT bekerja
normal. Pembacaan tetap lewat `SELECT ... FROM pg.grading_jobs` biasa.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

# Ambang job dianggap mangkrak: pekerja wajib berdetak tiap ~15 detik, jadi
# hening selama ini berarti threadnya benar-benar sudah tidak ada.
MENIT_MANGKRAK = int(os.getenv("GRADING_STALE_MINUTES", "15"))

CALLBACK_PERCOBAAN = int(os.getenv("GRADING_CALLBACK_RETRIES", "3"))
CALLBACK_TIMEOUT = int(os.getenv("GRADING_CALLBACK_TIMEOUT", "20"))

PESAN_MANGKRAK = (
    "Pekerja berhenti tanpa kabar (kemungkinan Langflow restart). "
    "Job ditandai gagal karena tidak berdetak lebih dari "
    f"{MENIT_MANGKRAK} menit."
)


# ── Penulisan SQL yang aman ────────────────────────────────────────────────

def q(nilai) -> str:
    """Literal SQL. Kutip tunggal digandakan — wajib, teks anomali memuat apostrof."""
    if nilai is None:
        return "NULL"
    if isinstance(nilai, bool):
        return "TRUE" if nilai else "FALSE"
    if isinstance(nilai, (int, float)):
        return str(nilai)
    if isinstance(nilai, (dict, list)):
        nilai = json.dumps(nilai, ensure_ascii=False)
    return "'" + str(nilai).replace("'", "''") + "'"


def jalankan_pg(con, sql: str) -> None:
    """Jalankan SQL di PostgreSQL apa adanya (bukan lewat lapisan COPY DuckDB)."""
    con.execute(f"CALL postgres_execute('pg', {q(sql)})")


def sekarang() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Siklus hidup job ───────────────────────────────────────────────────────

def buat_job_id() -> str:
    return f"ds-grade-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"


KOLOM_DAFTAR = [
    "job_id", "file_id", "status", "filename", "s3_bucket", "s3_endpoint",
    "csv_key", "parquet_key", "row_count", "institution_id",
    "institution_name", "callback_url", "callback_token", "callback_status",
    "created_at", "heartbeat_at",
]


def catat_job(con, job: dict) -> None:
    """Daftarkan job baru berstatus QUEUED. Dipanggil SEBELUM thread dilepas."""
    nilai = dict(job)
    nilai["status"] = "QUEUED"
    nilai["callback_status"] = "PENDING" if job.get("callback_url") else "SKIPPED"
    nilai["created_at"] = sekarang()
    nilai["heartbeat_at"] = sekarang()

    isi = ", ".join(q(nilai.get(k)) for k in KOLOM_DAFTAR)
    jalankan_pg(
        con,
        f"INSERT INTO grading_jobs ({', '.join(KOLOM_DAFTAR)}) VALUES ({isi})",
    )


def susun_job(muatan: dict, bawaan: dict | None = None) -> dict:
    """
    Terjemahkan OutboundGradingJobPayload (spesifikasi bagian 2.1) jadi baris job.

    Muatan resmi memakai camelCase. Bentuk snake_case juga diterima supaya
    pengujian lewat Postman tidak perlu menyusun JSON bersarang, dan `bawaan`
    mengisi field yang tidak ada di muatan (dipakai node dispatch untuk
    menggabungkan isian form dengan JSON).
    """
    p = {**(bawaan or {}), **(muatan or {})}

    def a(*nama, wajib=False):
        for n in nama:
            nilai = p.get(n)
            if nilai not in (None, ""):
                return nilai
        if wajib:
            raise ValueError(
                f"Field wajib tidak ada: {nama[0]}. Diterima: {sorted(k for k in p if p[k])}"
            )
        return None

    institusi = p.get("institution") or {}
    callback = p.get("callback") or {}

    baris = int(a("rowCount", "row_count") or 0) or None

    return {
        "job_id": buat_job_id(),
        "file_id": str(a("fileId", "file_id", wajib=True)),
        "filename": a("filename", "original_filename"),
        "s3_bucket": str(a("s3Bucket", "s3_bucket", wajib=True)),
        "s3_endpoint": a("s3Endpoint", "s3_endpoint"),
        "csv_key": a("csvKey", "csv_key"),
        # Portal mengirim `rawSourceKey` berdampingan dengan `csvKey`, isinya
        # sama: berkas unggahan asli. Dibawa juga supaya `_pilih_sumber` punya
        # dua kesempatan menemukan berkas masukan yang benar.
        "raw_source_key": a("rawSourceKey", "raw_source_key"),
        "parquet_key": str(a("parquetKey", "parquet_key", wajib=True)),
        "enriched_key": a("enrichedParquetKey", "enriched_key"),
        "row_count": baris,
        "institution_id": institusi.get("id") or a("institution_id"),
        "institution_name": institusi.get("name") or a("institution_name"),
        "callback_url": callback.get("url") or a("callback_url"),
        "callback_token": callback.get("secretToken") or a("callback_token"),
    }


def ubah_status(con, job_id: str, status: str, **kolom) -> None:
    """Pindahkan job ke status baru. `result` di-cast ke jsonb di sisi PostgreSQL."""
    set_ = [f"status = {q(status)}", f"heartbeat_at = {q(sekarang())}"]

    if status == "RUNNING":
        set_.append(f"started_at = COALESCE(started_at, {q(sekarang())})")
    if status in ("COMPLETED", "FAILED"):
        set_.append(f"finished_at = {q(sekarang())}")

    for k, v in kolom.items():
        set_.append(f"{k} = {q(v)}::jsonb" if k == "result" else f"{k} = {q(v)}")

    jalankan_pg(
        con,
        f"UPDATE grading_jobs SET {', '.join(set_)} WHERE job_id = {q(job_id)}",
    )


def detak(con, job_id: str, tahap: str | None = None) -> None:
    """Tanda pekerja masih hidup. Tanpa ini job tak terbedakan dari yang mati."""
    tambahan = f", stage = {q(tahap)}" if tahap else ""
    jalankan_pg(
        con,
        f"UPDATE grading_jobs SET heartbeat_at = {q(sekarang())}{tambahan} "
        f"WHERE job_id = {q(job_id)}",
    )


def panen_mangkrak(con) -> int:
    """
    Tandai GAGAL setiap job yang detaknya sudah lama hilang.

    Inilah penebus risiko arsitektur "thread di dalam Langflow": kalau container
    restart di tengah grading, job tidak menggantung di RUNNING selamanya — pada
    polling berikutnya ia dilaporkan FAILED, dan backend bisa mengirim ulang.
    """
    sebelum = con.execute(
        "SELECT count(*) FROM pg.grading_jobs WHERE status IN ('QUEUED', 'RUNNING')"
    ).fetchone()[0]

    jalankan_pg(con, f"""
        UPDATE grading_jobs
           SET status = 'FAILED',
               finished_at = {q(sekarang())},
               error = {q(PESAN_MANGKRAK)}
         WHERE status IN ('QUEUED', 'RUNNING')
           AND heartbeat_at < now() - interval '{MENIT_MANGKRAK} minutes'
    """)

    sesudah = con.execute(
        "SELECT count(*) FROM pg.grading_jobs WHERE status IN ('QUEUED', 'RUNNING')"
    ).fetchone()[0]
    return sebelum - sesudah


KOLOM_BACA = (
    "job_id, file_id, status, stage, filename, s3_bucket, parquet_key, "
    "enriched_key, parquet_size_bytes, row_count, institution_name, "
    "callback_status, callback_attempts, callback_error, error, "
    "grading_duration_ms, created_at, started_at, finished_at, heartbeat_at, "
    "CAST(result AS VARCHAR) AS result"
)


def ambil_job(con, file_id: str | None = None, job_id: str | None = None) -> dict | None:
    """Job terbaru untuk satu file_id, atau satu job_id tertentu."""
    if job_id:
        where = f"job_id = {q(job_id)}"
    elif file_id:
        where = f"file_id = {q(file_id)}"
    else:
        raise ValueError("Butuh salah satu dari file_id atau job_id")

    kur = con.execute(
        f"SELECT {KOLOM_BACA} FROM pg.grading_jobs WHERE {where} "
        f"ORDER BY created_at DESC LIMIT 1"
    )
    baris = kur.fetchall()
    if not baris:
        return None

    nama = [d[0] for d in kur.description]
    hasil = dict(zip(nama, baris[0]))
    for k in ("created_at", "started_at", "finished_at", "heartbeat_at"):
        if hasil.get(k) is not None:
            hasil[k] = hasil[k].isoformat()
    if hasil.get("result"):
        hasil["result"] = json.loads(hasil["result"])
    return hasil


# ── Webhook callback ───────────────────────────────────────────────────────

def ubah_status_callback(con, job_id: str, status: str, percobaan: int,
                         galat: str | None) -> None:
    jalankan_pg(con, f"""
        UPDATE grading_jobs
           SET callback_status = {q(status)},
               callback_attempts = {percobaan},
               callback_error = {q(galat)}
         WHERE job_id = {q(job_id)}
    """)


def kirim_callback(con, job_id: str, url: str, token: str | None, muatan: dict) -> None:
    """
    Kirim hasil ke portal Synchrono sesuai spesifikasi integrasi (bagian 4).

    Dipanggil SETELAH enriched.parquet selesai diunggah — portal memverifikasi
    keberadaan berkas lewat HeadObject dan membalas 400 kalau belum ada.

    Kegagalan di sini TIDAK menggagalkan grading. Hasilnya sudah tersimpan di
    `grading_jobs` dan tetap terbaca lewat API status, jadi backend punya jalur
    kedua kalau webhook tak sampai.
    """
    if not url:
        return

    badan = json.dumps(muatan, ensure_ascii=False).encode()
    header = {"Content-Type": "application/json"}
    if token:
        header["X-Grading-Signature"] = token
        header["Authorization"] = f"Bearer {token}"

    galat = None
    for percobaan in range(1, CALLBACK_PERCOBAAN + 1):
        try:
            req = urllib.request.Request(url, data=badan, headers=header, method="POST")
            with urllib.request.urlopen(req, timeout=CALLBACK_TIMEOUT) as r:
                print(f"[CB] {job_id} -> {r.status} (percobaan {percobaan})")
                ubah_status_callback(con, job_id, "SENT", percobaan, None)
                return
        except urllib.error.HTTPError as e:
            isi = e.read().decode(errors="replace")[:400]
            galat = f"HTTP {e.code}: {isi}"
            # 4xx selain 429 berarti muatannya yang salah — mengulang tidak menolong.
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001 — jaringan, DNS, timeout, apa pun
            galat = f"{type(e).__name__}: {e}"

        if percobaan < CALLBACK_PERCOBAAN:
            time.sleep(2 ** percobaan)

    print(f"[CB] {job_id} GAGAL: {galat}")
    ubah_status_callback(con, job_id, "FAILED", CALLBACK_PERCOBAAN, galat)
