"""
Tarik `uploaded_files` dari StarRocks -> Parquet cadangan, lalu isi tabel
`uploaded_files` di PostgreSQL lokal.

Tabel ini bukan syarat jalannya matching — kontrak dengan backend Synchrono
mengirim `{file_id, parquet_path, grade}` lengkap di dalam muatan, jadi service
tidak pernah membacanya. Gunanya untuk MANUSIA: saat menguji secara lokal,
inilah yang memberi tahu file_id mana ber-grade berapa dan parquet-nya yang
mana.

Dijalankan dengan venv data-matching (punya pymysql + polars):

    cd data-matching
    ./.venv/Scripts/python.exe ../langflow-synchrono/infra/cadangkan_uploaded_files.py
    ./.venv/Scripts/python.exe ../langflow-synchrono/infra/cadangkan_uploaded_files.py --muat
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

import polars as pl
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BACKEND = Path(r"d:\ISGS\PROJECT\synchrono\data-matching")
TUJUAN = Path(__file__).parent / "cadangan"
BERKAS = TUJUAN / "uploaded_files_terbaru.parquet"

# StarRocks -> PostgreSQL. `minio_path` berganti nama jadi `parquet_path`, dan
# nilainya diarahkan ulang ke SeaweedFS karena di situlah berkasnya sekarang.
KOLOM = ["file_id", "original_filename", "institution_name", "minio_path",
         "upload_timestamp", "row_count", "grade", "processing_status",
         "sync_status", "is_sync", "matching_task_status", "matching_time_ms",
         "is_custom_ready"]


def tarik() -> pl.DataFrame:
    load_dotenv(BACKEND / ".env")
    eng = create_engine(
        f"mysql+pymysql://{os.getenv('STARROCKS_USER')}:{os.getenv('STARROCKS_PASSWORD')}"
        f"@{os.getenv('STARROCKS_HOST')}:{os.getenv('STARROCKS_PORT')}"
        f"/{os.getenv('STARROCKS_DATABASE')}",
        connect_args={"connect_timeout": 15},
    )
    with eng.connect() as c:
        baris = c.execute(text(
            f"SELECT {', '.join(KOLOM)} FROM uploaded_files ORDER BY upload_timestamp DESC"
        )).mappings().all()

    df = pl.DataFrame([dict(r) for r in baris])
    TUJUAN.mkdir(exist_ok=True)
    stempel = datetime.now().strftime("%Y%m%d")
    df.write_parquet(TUJUAN / f"uploaded_files_{stempel}.parquet", compression="zstd")
    df.write_parquet(BERKAS, compression="zstd")
    print(f"{df.height:,} baris ditarik -> {BERKAS.name}")
    return df


def muat() -> None:
    import duckdb

    if not BERKAS.exists():
        raise SystemExit(f"{BERKAS} belum ada — jalankan tanpa --muat dulu.")

    pg = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")
    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{pg}' AS pg (TYPE postgres)")

    n_awal = con.execute("SELECT count(*) FROM pg.public.uploaded_files").fetchone()[0]
    if n_awal:
        print(f"  mengosongkan uploaded_files ({n_awal:,} baris)…")
        con.execute("CALL postgres_execute('pg', 'TRUNCATE uploaded_files')")

    # minio_path berbentuk "curated/xxx.parquet"; berkasnya sekarang ada di
    # SeaweedFS bucket `synchrono` dengan kunci yang sama persis.
    con.execute(f"""
        INSERT INTO pg.public.uploaded_files
            (file_id, original_filename, institution_name, parquet_path,
             upload_timestamp, row_count, grade, processing_status, sync_status,
             is_sync, matching_task_status, matching_time_ms, is_custom_ready)
        SELECT file_id, original_filename, institution_name,
               's3://synchrono/' || minio_path,
               upload_timestamp, row_count, grade, processing_status, sync_status,
               CAST(is_sync AS BOOLEAN), matching_task_status, matching_time_ms,
               CAST(is_custom_ready AS BOOLEAN)
          FROM read_parquet('{BERKAS.as_posix()}')
    """)
    n = con.execute("SELECT count(*) FROM pg.public.uploaded_files").fetchone()[0]
    print(f"  {n:,} baris dimuat ke PostgreSQL\n")

    # Sekadar metadata. Sebagian besar parquet-nya sudah TIDAK ada lagi di
    # penyimpanan — StarRocks mencatat 154 unggahan, sementara MinIO hanya
    # menyimpan 14 berkas terakhir. Untuk tahu mana yang benar-benar bisa
    # dipakai, jalankan `cek_ketersediaan.py` di dalam container.
    print("  metadata per grade (BELUM tentu parquet-nya ada):")
    for r in con.execute("""
        SELECT grade, count(*) FROM pg.public.uploaded_files
         GROUP BY grade ORDER BY grade
    """).fetchall():
        print(f"    grade {r[0]}: {r[1]:>3} baris")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--muat", action="store_true",
                   help="muat cadangan ke PostgreSQL (MENGOSONGKAN lebih dulu)")
    args = p.parse_args()

    if args.muat:
        muat()
    else:
        tarik()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
