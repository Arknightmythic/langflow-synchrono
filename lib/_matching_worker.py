"""
Pekerja matching latar belakang — padanan `_worker.py` milik grading.

`/api/v1/run/...` Langflow bersifat sinkron, sedangkan matching berkas besar
memakan menit. Node dispatch hanya memeriksa muatan, melepas thread ini, dan
langsung membalas IN_PROGRESS (spesifikasi §3.3). Kemajuannya dibaca portal dari
tabelnya sendiri (`syncrono_matching_job`), dan hasil akhirnya lewat callback.

DUA HAL YANG DIJAGA

  1. BATAS KONKURENSI. Matching memegang incoming, hash table tiap pass, dan
     agregasi Pass 3 sekaligus. `MATCHING_MAX_CONCURRENT` bawaannya 1 — lebih
     rendah dari grading — karena dua job berbarengan berebut memori container
     yang sama, dan yang kalah mati karena OOM tanpa pesan.

  2. SETIAP KEGAGALAN SAMPAI KE PORTAL. Galat di thread tidak punya pemanggil
     yang menunggu; kalau tidak ditangkap di sini, job tertinggal IN_PROGRESS
     selamanya di tabel portal dan tidak ada callback. `tutup_gagal` menandai
     FAILED dan mengirim callback FAILED (§8.1).
"""

from __future__ import annotations

import os
import threading
import traceback

import _matching

MAKS_PARALEL = int(os.getenv("MATCHING_MAX_CONCURRENT", "1"))
_slot = threading.Semaphore(MAKS_PARALEL)


def lepas(job: dict) -> None:
    """Jalankan matching di thread terpisah. Kembali seketika."""
    t = threading.Thread(target=_kerjakan, args=(job,), daemon=True,
                         name=f"matching-{job['job_id'][-8:]}")
    t.start()


def _kerjakan(job: dict) -> None:
    with _slot:
        try:
            _matching.jalankan(job)
        except _matching.Dibatalkan:
            # Portal sudah menandai CANCELLED sendiri; tidak ada yang perlu
            # ditulis balik, dan tidak ada callback — spesifikasi §8.2 tidak
            # meminta konfirmasi pembatalan.
            print(f"[M] {job['job_id']} DIBATALKAN operator — berhenti.")
        except Exception as e:  # noqa: BLE001 — apa pun, harus sampai ke portal
            print(f"[M] {job['job_id']} GAGAL:\n{traceback.format_exc()}")
            _matching.tutup_gagal(job, f"{type(e).__name__}: {e}")
