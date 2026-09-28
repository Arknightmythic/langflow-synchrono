"""
Endpoint matching.

    POST /api/v1/matching/run    -> jalankan N1..N7, balas ringkasan

SINKRON, dan memang seharusnya begitu: 200 ribu baris selesai dalam hitungan
detik, jauh di bawah batas yang membuat antrean sepadan. Kalau suatu saat
berkasnya jadi jauh lebih besar, polanya sudah tersedia — tinggal ikuti yang
dipakai grading.

KENAPA IMPOR-NYA DI DALAM FUNGSI

Node matching ada di `/components/matching`, folder terpisah yang di-mount.
Kalau diimpor di tingkat modul, service ini GAGAL START sama sekali ketika
folder itu tidak ada — padahal grading dan config tidak membutuhkannya sedikit
pun. Diimpor saat dipakai, ketidakhadirannya jadi 503 pada satu endpoint, bukan
container yang mati.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, status

from .skema import MuatanMatching

rute = APIRouter(prefix="/api/v1/matching", tags=["matching"])


def _muat_node():
    try:
        import n1_open_session as n1
        import n2_prepare_incoming as n2
        import n3_prepare_master as n3
        import n4_load_config as n4
        import n5_run_join as n5
        import n6_score_classify as n6
        import n7_persist as n7
    except ImportError as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Node matching tidak termuat ({e}). Pastikan folder "
            f"components/matching ikut di-mount dan ada di PYTHONPATH.",
        ) from e
    return n1, n2, n3, n4, n5, n6, n7


@rute.post("/run", summary="Cocokkan satu berkas dengan master")
def jalankan(muatan: MuatanMatching) -> dict:
    """
    `pakaiEnriched: true` (bawaan) membuat matching memakai hasil grading yang
    kolomnya sudah ternormalisasi, dicari dari `grading_jobs` berdasarkan
    `fileId`. Setel false untuk memaksa memakai parquet di `parquetPath`.

    `dryRun` BAWAANNYA TRUE di sini, berbeda dari versi Langflow. Di Langflow
    parameter itu milik node ketujuh, dan mengirimkannya ke node pertama tidak
    menimbulkan galat apa pun — nilainya diabaikan diam-diam dan 200 ribu baris
    tetap tertulis. Kesalahan itu sudah pernah terjadi. Di sini muatannya satu,
    tidak ada node yang keliru dituju, dan yang tidak diminta tidak ditulis.
    """
    n1, n2, n3, n4, n5, n6, n7 = _muat_node()

    mulai = time.perf_counter()
    s = n1.jalankan(muatan.fileId, muatan.parquetPath, muatan.grade,
                    pakai_enriched=muatan.pakaiEnriched)
    for node in (n2, n3, n4, n5, n6):
        s = node.jalankan(s)
    ringkas = n7.jalankan(s, dry_run=muatan.dryRun)

    hasil = dict(ringkas.data)
    hasil["total_seconds"] = round(time.perf_counter() - mulai, 3)
    return hasil
