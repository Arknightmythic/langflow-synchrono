"""
Migrasi tabel `master` StarRocks -> PostgreSQL, sekali jalan.

Dua langkah, karena extension `mysql` DuckDB membungkus query katalognya dalam
transaksi eksplisit yang ditolak StarRocks ("Explicit transaction only support
begin/commit/rollback/insert/update/delete/set/select statements"). Jadi:

  1. tarik dari StarRocks lewat driver resminya (pymysql), simpan ke Parquet
  2. DuckDB membaca Parquet itu dan meng-INSERT-nya ke PostgreSQL

Langkah 2 tidak memuat data ke memori Python sama sekali.
"""
import os, sys, time, tempfile
from pathlib import Path

VENV_BE = Path(r"d:\ISGS\PROJECT\synchrono\data-matching")
PARQUET = Path(tempfile.gettempdir()) / "master_migrasi.parquet"
PG = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")


def langkah1_tarik():
    """Dijalankan dengan venv data-matching (punya pymysql + polars)."""
    import polars as pl
    from sqlalchemy import create_engine, text
    from dotenv import load_dotenv

    load_dotenv(VENV_BE / ".env")
    eng = create_engine(
        f"mysql+pymysql://{os.getenv('STARROCKS_USER')}:{os.getenv('STARROCKS_PASSWORD')}"
        f"@{os.getenv('STARROCKS_HOST')}:{os.getenv('STARROCKS_PORT')}/{os.getenv('STARROCKS_DATABASE')}"
    )
    mulai = time.perf_counter()
    with eng.connect() as c:
        rows = c.execute(text("""
            SELECT nik, nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin,
                   nama_ibu, status_kematian, provinsi, kabupaten, kecamatan, kelurahan
            FROM master
        """)).mappings().all()
    df = pl.DataFrame([dict(r) for r in rows])
    df.write_parquet(PARQUET)
    print(f"[1/2] ditarik {df.height:,} baris dari StarRocks "
          f"({time.perf_counter()-mulai:.1f} detik) -> {PARQUET.name}")


def langkah2_muat():
    """Dijalankan dengan venv langflow (punya duckdb)."""
    import duckdb

    if not PARQUET.exists():
        sys.exit(f"{PARQUET} belum ada — jalankan langkah 1 dulu")

    con = duckdb.connect()
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG}' AS pg (TYPE postgres)")

    n_awal = con.execute("SELECT COUNT(*) FROM pg.public.master").fetchone()[0]
    if n_awal:
        print(f"      tujuan berisi {n_awal:,} baris — dikosongkan dulu")
        con.execute("CALL postgres_execute('pg', 'TRUNCATE master')")

    mulai = time.perf_counter()
    con.execute(f"""
        INSERT INTO pg.public.master
            (nik, nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin,
             nama_ibu, status_kematian, provinsi, kabupaten, kecamatan, kelurahan)
        SELECT nik, nama_lengkap, tempat_lahir,
               TRY_CAST(tanggal_lahir AS DATE), jenis_kelamin,
               nama_ibu, status_kematian, provinsi, kabupaten, kecamatan, kelurahan
        FROM read_parquet('{PARQUET.as_posix()}')
    """)
    n = con.execute("SELECT COUNT(*) FROM pg.public.master").fetchone()[0]
    print(f"[2/2] dimuat {n:,} baris ke PostgreSQL ({time.perf_counter()-mulai:.1f} detik)")

    print("\ncontoh:")
    for r in con.execute(
        "SELECT nik, nama_lengkap, tanggal_lahir, jenis_kelamin, provinsi "
        "FROM pg.public.master LIMIT 3"
    ).fetchall():
        print("  ", r)


if __name__ == "__main__":
    if sys.argv[1:2] == ["tarik"]:
        langkah1_tarik()
    elif sys.argv[1:2] == ["muat"]:
        langkah2_muat()
    else:
        sys.exit("pakai: migrate_master.py tarik   |   migrate_master.py muat")
