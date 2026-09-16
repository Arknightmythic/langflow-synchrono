"""
Tarik `master` dari StarRocks ke berkas Parquet yang PERMANEN.

Bedanya dari `migrate_master.py`: skrip ini TIDAK menyentuh PostgreSQL sama
sekali. Ia hanya membaca StarRocks dan menulis satu berkas.

Gunanya untuk lembur di luar jam kantor, saat server StarRocks dimatikan.
Dengan berkas ini, `muat_cadangan.py` bisa mengisi ulang PostgreSQL kapan saja
tanpa VPN dan tanpa StarRocks.

Dijalankan dengan venv data-matching (yang punya pymysql + polars):

    cd data-matching
    ./.venv/Scripts/python.exe ../langflow-synchrono/infra/cadangkan_master.py

SELURUH kolom ikut ditarik, termasuk `id` dan `nama` yang tidak dipakai skema
PostgreSQL sekarang. Cadangan yang kehilangan kolom bukan cadangan — kalau
suatu saat kolom itu dibutuhkan, servernya mungkin sedang mati.
"""

import os
import time
from datetime import datetime
from pathlib import Path

import polars as pl
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BACKEND = Path(r"d:\ISGS\PROJECT\synchrono\data-matching")
TUJUAN = Path(__file__).parent / "cadangan"


def main() -> int:
    load_dotenv(BACKEND / ".env")
    TUJUAN.mkdir(exist_ok=True)

    eng = create_engine(
        f"mysql+pymysql://{os.getenv('STARROCKS_USER')}:{os.getenv('STARROCKS_PASSWORD')}"
        f"@{os.getenv('STARROCKS_HOST')}:{os.getenv('STARROCKS_PORT')}"
        f"/{os.getenv('STARROCKS_DATABASE')}",
        connect_args={"connect_timeout": 15},
    )

    mulai = time.perf_counter()
    with eng.connect() as c:
        kolom = [r[0] for r in c.execute(text("DESCRIBE master")).fetchall()]
        print(f"kolom di StarRocks ({len(kolom)}): {', '.join(kolom)}")
        baris = c.execute(text(f"SELECT {', '.join(kolom)} FROM master")).mappings().all()

    df = pl.DataFrame([dict(r) for r in baris])
    tarik = time.perf_counter() - mulai

    stempel = datetime.now().strftime("%Y%m%d")
    berkas = TUJUAN / f"master_{stempel}.parquet"
    df.write_parquet(berkas, compression="zstd")

    # Tautan tetap, supaya skrip pemuat tidak perlu menebak tanggal.
    terbaru = TUJUAN / "master_terbaru.parquet"
    terbaru.write_bytes(berkas.read_bytes())

    ukuran = berkas.stat().st_size
    print(f"\n{df.height:,} baris x {df.width} kolom ditarik dalam {tarik:.1f} detik")
    print(f"  {berkas}")
    print(f"  {terbaru}   (salinan, dipakai muat_cadangan.py)")
    print(f"  ukuran: {ukuran / 1_048_576:.1f} MB")

    print("\nringkasan isi:")
    print(f"  nik unik      : {df['nik'].n_unique():,}")
    if "tanggal_lahir" in df.columns:
        print(f"  tanggal lahir : {df['tanggal_lahir'].min()} .. {df['tanggal_lahir'].max()}")
    if "provinsi" in df.columns:
        print(f"  provinsi unik : {df['provinsi'].n_unique()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
