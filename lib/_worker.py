"""
Pekerja grading latar belakang.

INILAH YANG MEMBUAT API-NYA TIDAK MEMBLOKIR

`/api/v1/run/...` milik Langflow bersifat sinkron: pemanggil menunggu sampai
node terakhir selesai. Grading berkas 200 ribu baris memakan puluhan detik
sampai menit, jauh melewati batas 5 detik yang dituntut spesifikasi.

Karena itu node dispatch hanya mencatat job lalu MELEPAS thread ini dan langsung
balas 202. Thread-lah yang menjalankan grading sesungguhnya dan memperbarui
`grading_jobs`; backend Synchrono mengetahui kemajuannya lewat polling atau
webhook.

TIGA HAL YANG DIJAGA DI SINI

  1. Thread punya KONEKSI DUCKDB SENDIRI. Koneksi DuckDB tidak aman dipakai
     lintas thread, dan koneksi milik node dispatch sudah ditutup begitu
     responsnya dikirim.

  2. DETAK tiap ganti tahap. Thread ini daemon — kalau container Langflow
     berhenti, ia mati tanpa sempat menulis apa pun. Detak yang berhenti itulah
     yang membuat job mangkraknya bisa dikenali, bukan menggantung di RUNNING
     selamanya.

  3. BATAS KONKURENSI. Tanpa ini, sepuluh unggahan beruntun menjadi sepuluh
     grading serentak yang saling berebut memori di dalam satu container.
     Job yang menunggu giliran tetap berdetak, supaya tidak keliru dipanen
     sebagai mangkrak.
"""

from __future__ import annotations

import os
import threading
import time
import traceback

import duckdb

from _grading import jalankan_penuh
from _jobs import detak, kirim_callback, ubah_status
from _konversi import konversi_dulu
from _shared import buka_koneksi

MAKS_PARALEL = int(os.getenv("GRADING_MAX_CONCURRENT", "2"))
_slot = threading.Semaphore(MAKS_PARALEL)

# Jeda detak selagi mengantre. Harus jauh lebih kecil dari GRADING_STALE_MINUTES.
JEDA_DETAK = 10


def lepas(job: dict) -> None:
    """Jalankan grading di thread terpisah. Kembali seketika."""
    t = threading.Thread(target=_kerjakan, args=(job,), daemon=True,
                         name=f"grading-{job['job_id'][-8:]}")
    t.start()


def _kerjakan(job: dict) -> None:
    job_id = job["job_id"]
    con = None
    try:
        con = buka_koneksi()
    except Exception:  # noqa: BLE001
        # Tanpa koneksi, status pun tak bisa ditulis. Cetak dan biarkan
        # pemanen mangkrak yang menutup job ini.
        print(f"[W] {job_id} gagal membuka koneksi:\n{traceback.format_exc()}")
        return

    try:
        _antre(con, job_id)
        try:
            _jalankan(con, job)
        finally:
            _slot.release()
    except Exception as e:  # noqa: BLE001
        print(f"[W] {job_id} GAGAL:\n{traceback.format_exc()}")
        _tutup_gagal(con, job, _pesan(e))
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def _antre(con: duckdb.DuckDBPyConnection, job_id: str) -> None:
    """Tunggu giliran, tetap berdetak supaya tidak dikira mangkrak."""
    menunggu = 0
    while not _slot.acquire(timeout=JEDA_DETAK):
        menunggu += JEDA_DETAK
        detak(con, job_id, f"menunggu antrean ({menunggu}s)")
    if menunggu:
        print(f"[W] {job_id} mengantre {menunggu}s sebelum mulai")


def _jalankan(con, job: dict) -> None:
    job_id = job["job_id"]
    ubah_status(con, job_id, "RUNNING", stage="G1 open session")
    mulai = time.perf_counter()

    # JALUR B, kalau berkasnya .sql/.dmp/.mdf.
    #
    # Dikerjakan DI SINI, di dalam job yang sama — bukan sebagai job kedua.
    # Portal memanggil endpoint yang sama untuk semua format dan tidak pernah
    # tahu ada pembagian jalur, jadi dua jobId untuk satu unggahan hanya akan
    # memaksa sisi portal menjahitnya kembali di UI.
    #
    # Sesudah ini `job["parquet_key"]` sudah menunjuk parquet hasil konversi,
    # dan grading di bawah tidak bisa membedakannya dari job parquet biasa.
    lapor = lambda tahap: detak(con, job_id, tahap)  # noqa: E731
    konversi = konversi_dulu(job, lapor=lapor)
    if konversi:
        ubah_status(con, job_id, "RUNNING", stage="G1 open session")

    keluaran = jalankan_penuh(job, lapor=lapor)
    hasil, sesi = keluaran["hasil"], keluaran["sesi"]
    durasi = int((time.perf_counter() - mulai) * 1000)
    hasil["gradingDurationMs"] = durasi

    ubah_status(
        con, job_id, "COMPLETED",
        stage="selesai",
        result=hasil,
        enriched_key=sesi["enriched_key"],
        parquet_size_bytes=sesi.get("parquet_size_bytes"),
        row_count=sesi["row_count"],
        grading_duration_ms=durasi,
        error=None,
    )
    print(f"[W] {job_id} SELESAI dalam {durasi} ms — "
          f"grade {hasil['summary']['gradeLetter']}, "
          f"skor {hasil['summary']['qualityScore']}")

    # Berkas enriched sudah pasti terunggah di tahap G5, jadi HeadObject di sisi
    # portal akan menemukannya. Callback baru boleh dikirim setelah titik ini.
    kirim_callback(con, job_id, job.get("callback_url"),
                   job.get("callback_token"), hasil)


def _pesan(e: BaseException) -> str:
    """
    Ringkas pengecualian jadi satu baris yang benar-benar menjelaskan.

    JANGAN memakai baris terakhir traceback: pesan galat DuckDB berformat
    banyak baris dan diakhiri penunjuk caret, sehingga yang tersimpan hanyalah
    sebuah "^". Nama kelas plus pesannya sendiri jauh lebih berguna.
    """
    isi = " ".join(str(e).split())
    return f"{type(e).__name__}: {isi}"[:500]


def _tutup_gagal(con, job: dict, ringkas: str) -> None:
    job_id = job["job_id"]
    try:
        ubah_status(con, job_id, "FAILED", stage="gagal", error=ringkas)
        kirim_callback(con, job_id, job.get("callback_url"),
                       job.get("callback_token"),
                       {"fileId": job["file_id"], "status": "FAILED",
                        "error": ringkas})
    except Exception:  # noqa: BLE001
        print(f"[W] {job_id} status gagal pun tidak bisa ditulis:\n"
              f"{traceback.format_exc()}")
