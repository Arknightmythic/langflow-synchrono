"""
Jalankan ketujuh node berurutan TANPA Langflow.

Buktikan logikanya dulu di sini. Kalau gagal di tahap ini, mencari penyebabnya
di kanvas Langflow jauh lebih sulit.

DEFAULTNYA DRY RUN — node 7 tidak menulis ke PostgreSQL kecuali diberi --write.
(Berbeda dari versi StarRocks, menulis dua kali di sini AMAN: penulisannya
upsert, bukan append.)

Contoh:
    python run_local.py --file-id uji01 \
        --parquet s3://synchrono/curated/uji.parquet --grade 1
"""

import argparse
import sys
import time
from pathlib import Path

# Lokasi node berbeda antara host dan container: di host ia bersebelahan dengan
# berkas ini, di container ia di-mount ke /components. Dicari, bukan ditebak —
# di mesin Windows ini extension httpfs DuckDB diblokir Application Control
# policy, sehingga skrip ini pada praktiknya HANYA bisa jalan di container.
_DISINI = Path(__file__).resolve().parent
for _kandidat in (_DISINI.parent / "lib", _DISINI / "lib", Path("/synchrono/lib")):
    if _kandidat.is_dir():
        sys.path.insert(0, str(_kandidat))
        break
for _kandidat in (_DISINI.parent / "components" / "matching", _DISINI / "components" / "matching", Path("/components/matching")):
    if _kandidat.is_dir():
        sys.path.insert(0, str(_kandidat))
        break
else:
    raise SystemExit("Folder node matching tidak ditemukan (components/matching)")

import n1_open_session as n1        # noqa: E402
import n2_prepare_incoming as n2    # noqa: E402
import n3_prepare_master as n3      # noqa: E402
import n4_load_config as n4         # noqa: E402
import n5_run_join as n5            # noqa: E402
import n6_score_classify as n6      # noqa: E402
import n7_persist as n7             # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Uji pipeline matching per node.")
    p.add_argument("--file-id", required=True)
    p.add_argument("--parquet", required=True, help="s3://bucket/key.parquet")
    p.add_argument("--grade", type=int, required=True, choices=[1, 2, 3, 4, 5])
    p.add_argument("--write", action="store_true",
                   help="Upsert hasil ke PostgreSQL (tanpa ini hanya dry run).")
    p.add_argument("--parquet-mentah", action="store_true",
                   help="Paksa memakai parquet dari --parquet, bukan hasil grading.")
    args = p.parse_args()

    print(f"\n(mode: {'TULIS ke PostgreSQL' if args.write else 'dry run'})")
    print("=" * 70)
    print(f"file_id : {args.file_id}\ngrade   : {args.grade}\nparquet : {args.parquet}")
    print("=" * 70)

    mulai = time.perf_counter()

    print("\n--- N1 open session ---")
    s = n1.jalankan(args.file_id, args.parquet, args.grade,
                    pakai_enriched=not args.parquet_mentah)
    print("\n--- N2 prepare incoming ---")
    s = n2.jalankan(s)
    print("\n--- N3 prepare master ---")
    s = n3.jalankan(s)
    print("\n--- N4 load config ---")
    s = n4.jalankan(s)
    print("\n--- N5 run join ---")
    s = n5.jalankan(s)
    print("\n--- N6 score & classify ---")
    s = n6.jalankan(s)
    print("\n--- N7 persist ---")
    ringkas = n7.jalankan(s, dry_run=not args.write)

    total = time.perf_counter() - mulai
    print("\n" + "=" * 70)
    print("RINGKASAN (ini yang dibalas API ke backend Synchrono)")
    print("=" * 70)
    for k, v in ringkas.data.items():
        print(f"  {k:22s}: {v}")
    print(f"  {'total_seconds':22s}: {total:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
