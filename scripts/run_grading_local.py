"""
Jalankan pipeline grading TANPA Langflow.

Buktikan logikanya dulu di sini. Kalau gagal di tahap ini, mencari penyebabnya
di kanvas Langflow jauh lebih sulit.

    python run_grading_local.py --file-id uji01 \
        --bucket bucket-test --key uploads/uji01/data.parquet

Tambahkan --job untuk ikut menguji jalur asinkronnya (catat job, lepas pekerja,
polling status) — persis yang dilakukan kedua API di Langflow.
"""

import argparse
import json
import sys
import time
from pathlib import Path

_DISINI = Path(__file__).resolve().parent
for _kandidat in (_DISINI.parent / "lib", _DISINI / "lib", Path("/synchrono/lib")):
    if _kandidat.is_dir():
        sys.path.insert(0, str(_kandidat))
        break

from _grading import (  # noqa: E402
    bersihkan_dan_tandai, buka, muat_raw, skor_dan_grade, susun_hasil,
    tulis_enriched,
)
from _jobs import ambil_job, catat_job, susun_job  # noqa: E402
from _shared import buka_koneksi  # noqa: E402


def langsung(args) -> int:
    """Enam tahap berurutan, sama seperti node G1-G6 di kanvas."""
    mulai = time.perf_counter()

    print("\n--- G1 open session ---")
    s = buka({
        "file_id": args.file_id, "s3_bucket": args.bucket,
        "parquet_key": args.key, "s3_endpoint": args.endpoint,
        "enriched_key": args.enriched_key,
    })
    print("\n--- G2 load raw parquet ---")
    s = muat_raw(s)
    print("\n--- G3 clean NIK & flag anomalies ---")
    s = bersihkan_dan_tandai(s)
    print("\n--- G4 score & grade ---")
    s = skor_dan_grade(s)

    if args.skip_write:
        print("\n--- G5 write enriched parquet — DILEWATI (--skip-write) ---")
        s = {**s, "enriched_path": None, "parquet_size_bytes": None}
    else:
        print("\n--- G5 write enriched parquet ---")
        s = tulis_enriched(s)

    print("\n--- G6 build callback payload ---")
    hasil = susun_hasil(s)

    print("\n" + "=" * 72)
    print("MUATAN CALLBACK (ini yang dikirim ke portal / dibalas API status)")
    print("=" * 72)
    print(json.dumps(hasil, ensure_ascii=False, indent=2))
    print(f"\ntotal: {time.perf_counter() - mulai:.1f} detik")

    s["con"].close()
    return 0


def lewat_job(args) -> int:
    """Jalur asinkronnya: catat job, lepas pekerja, lalu polling sampai selesai."""
    from _worker import lepas

    job = susun_job({}, bawaan={
        "file_id": args.file_id, "s3_bucket": args.bucket,
        "parquet_key": args.key, "s3_endpoint": args.endpoint,
        "callback_url": args.callback_url,
        "callback_token": args.callback_token,
    })

    con = buka_koneksi()
    catat_job(con, job)
    print(f"job dicatat: {job['job_id']}  (status QUEUED)")

    lepas(job)
    print("pekerja dilepas; mulai polling tiap 2 detik\n")

    while True:
        time.sleep(2)
        kini = ambil_job(con, job_id=job["job_id"])
        print(f"  [{kini['status']:9s}] {kini['stage'] or ''}")
        if kini["status"] in ("COMPLETED", "FAILED"):
            break

    print("\n" + "=" * 72)
    print(f"STATUS AKHIR: {kini['status']}")
    print("=" * 72)
    if kini["error"]:
        print(f"error   : {kini['error']}")
    if kini["callback_status"] not in (None, "SKIPPED"):
        print(f"callback: {kini['callback_status']} {kini['callback_error'] or ''}")
    if kini["result"]:
        print(json.dumps(kini["result"], ensure_ascii=False, indent=2))
    con.close()
    return 0 if kini["status"] == "COMPLETED" else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Uji pipeline grading per tahap.")
    p.add_argument("--file-id", required=True)
    p.add_argument("--bucket", default="bucket-test")
    p.add_argument("--key", required=True, help="uploads/{fileId}/data.parquet")
    p.add_argument("--endpoint", default=None, help="timpa S3_ENDPOINT")
    p.add_argument("--enriched-key", default=None)
    p.add_argument("--skip-write", action="store_true",
                   help="jangan tulis enriched.parquet (hanya menilai)")
    p.add_argument("--job", action="store_true",
                   help="lewat tabel grading_jobs + pekerja latar belakang")
    p.add_argument("--callback-url", default=None)
    p.add_argument("--callback-token", default=None)
    args = p.parse_args()

    return lewat_job(args) if args.job else langsung(args)


if __name__ == "__main__":
    raise SystemExit(main())
