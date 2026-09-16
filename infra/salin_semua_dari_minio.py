"""
Salin SELURUH parquet curated dari MinIO produksi ke SeaweedFS lokal.

Penerus `salin_dari_minio.py` yang hanya menangani satu berkas. Gunanya untuk
bekerja di luar jam kantor: setelah ini, matching tidak lagi menyentuh MinIO
maupun VPN sama sekali.

WAJIB dijalankan DI DALAM container — di mesin Windows ini extension `httpfs`
DuckDB diblokir Application Control policy, jadi versi host akan gagal:

    docker cp <langflow-synchrono>/.env synchrono-langflow:/tmp/.env
    docker exec synchrono-langflow python /synchrono/infra/salin_semua_dari_minio.py

Berkas yang sudah ada di tujuan DILEWATI, jadi menjalankannya ulang setelah
koneksi putus akan melanjutkan, bukan mengulang dari awal.
"""

import os
import sys
import time

import duckdb
from dotenv import load_dotenv

load_dotenv(os.getenv("ENV_FILE", "/tmp/.env"))

BUCKET_LAMA = os.getenv("RAW_BUCKET_NAME", "raw")
BUCKET_BARU = os.getenv("SEAWEED_BUCKET", "synchrono")
PREFIX = "curated/"

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET = os.getenv("MINIO_SECRET_KEY")
SEAWEED_ENDPOINT = os.getenv("S3_ENDPOINT", "seaweedfs:8333")


def sambung() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")

    # Dua secret dibedakan lewat SCOPE. Tanpa SCOPE, yang kedua menimpa yang
    # pertama dan salah satu bucket jadi tidak terjangkau.
    con.execute(f"""
        CREATE OR REPLACE SECRET minio (TYPE s3,
            KEY_ID '{MINIO_KEY}', SECRET '{MINIO_SECRET}',
            ENDPOINT '{MINIO_ENDPOINT}', URL_STYLE 'path', USE_SSL false,
            SCOPE 's3://{BUCKET_LAMA}')
    """)
    con.execute(f"""
        CREATE OR REPLACE SECRET seaweed (TYPE s3,
            KEY_ID '{os.getenv("S3_ACCESS_KEY", "synchrono")}',
            SECRET '{os.getenv("S3_SECRET_KEY", "synchrono123")}',
            ENDPOINT '{SEAWEED_ENDPOINT}', URL_STYLE 'path', USE_SSL false,
            SCOPE 's3://{BUCKET_BARU}')
    """)
    return con


def main() -> int:
    if not MINIO_KEY:
        raise SystemExit(
            "Kredensial MinIO tidak terbaca. Salin .env ke container dulu:\n"
            "  docker cp langflow-synchrono/.env synchrono-langflow:/tmp/.env"
        )

    con = sambung()

    sumber = [r[0] for r in con.execute(
        f"SELECT file FROM glob('s3://{BUCKET_LAMA}/{PREFIX}**')"
    ).fetchall()]
    print(f"{len(sumber)} berkas di s3://{BUCKET_LAMA}/{PREFIX}\n")

    sudah = {r[0] for r in con.execute(
        f"SELECT file FROM glob('s3://{BUCKET_BARU}/{PREFIX}**')"
    ).fetchall()}

    ok = lewat = gagal = 0
    mulai = time.perf_counter()

    for asal in sorted(sumber):
        kunci = asal.split(f"{BUCKET_LAMA}/", 1)[1]
        tujuan = f"s3://{BUCKET_BARU}/{kunci}"
        nama = kunci.split("/")[-1]

        if tujuan in sudah:
            print(f"  lewat  {nama}")
            lewat += 1
            continue

        try:
            n = con.execute(f"SELECT count(*) FROM read_parquet('{asal}')").fetchone()[0]
            con.execute(
                f"COPY (SELECT * FROM read_parquet('{asal}')) TO '{tujuan}' (FORMAT parquet)"
            )
            cek = con.execute(f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]
            tanda = "ok    " if cek == n else "SELISIH"
            print(f"  {tanda} {nama}  ({n:,} baris)")
            ok += 1
        except Exception as e:  # noqa: BLE001
            # Berkas rusak atau kosong tidak boleh menghentikan sisanya.
            print(f"  GAGAL  {nama}: {' '.join(str(e).split())[:110]}")
            gagal += 1

    print(f"\n{ok} disalin, {lewat} dilewati, {gagal} gagal "
          f"({time.perf_counter() - mulai:.1f} detik)")
    return 1 if gagal else 0


if __name__ == "__main__":
    sys.exit(main())
