"""
Salin parquet dari MinIO (sistem lama) ke SeaweedFS (service baru).

DuckDB menyambung ke KEDUA S3 sekaligus lewat secret ber-SCOPE, jadi
penyalinannya satu statement COPY tanpa file perantara di disk.
"""
import os, sys, time, duckdb
from pathlib import Path
from dotenv import load_dotenv

# .env dicari RELATIF terhadap berkas ini, bukan lewat path absolut.
#
# Sebelumnya di sini ada path mutlak ke mesin tertentu, dan itu sudah dua kali
# jadi masalah: berkasnya ikut berpindah tangan lalu berhenti jalan di mesin
# orang lain. Urutannya: variabel ENV_FILE kalau disetel, lalu .env di folder
# repo ini, lalu .env milik data-matching yang bersebelahan.
AKAR = Path(__file__).resolve().parents[1]
for _kandidat in (
    Path(os.getenv("ENV_FILE", "")) if os.getenv("ENV_FILE") else None,
    AKAR / ".env",
    AKAR.parent / "data-matching" / ".env",
):
    if _kandidat and _kandidat.is_file():
        load_dotenv(_kandidat)
        print(f"env dibaca dari: {_kandidat}")
        break
else:
    sys.exit(f"Tidak menemukan .env. Cari di {AKAR / '.env'} "
             f"atau setel ENV_FILE.")

SUMBER = sys.argv[1] if len(sys.argv) > 1 else sys.exit("pakai: salin_dari_minio.py <key-di-minio>")
BUCKET_LAMA = os.getenv("RAW_BUCKET_NAME", "raw")
TUJUAN = f"s3://synchrono/{SUMBER}"

con = duckdb.connect()
con.execute("INSTALL httpfs"); con.execute("LOAD httpfs")

# Dua secret, dibedakan lewat SCOPE. Tanpa SCOPE, yang kedua akan menimpa yang pertama.
con.execute(f"""
    CREATE SECRET minio (TYPE s3,
        KEY_ID '{os.getenv("MINIO_ACCESS_KEY")}', SECRET '{os.getenv("MINIO_SECRET_KEY")}',
        ENDPOINT '{os.getenv("MINIO_ENDPOINT")}', URL_STYLE 'path', USE_SSL false,
        SCOPE 's3://{BUCKET_LAMA}')
""")
con.execute("""
    CREATE SECRET seaweed (TYPE s3,
        KEY_ID 'synchrono', SECRET 'synchrono123',
        ENDPOINT 'localhost:8333', URL_STYLE 'path', USE_SSL false,
        SCOPE 's3://synchrono')
""")

asal = f"s3://{BUCKET_LAMA}/{SUMBER}"
n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{asal}')").fetchone()[0]
print(f"sumber : {asal}  ({n:,} baris)")

mulai = time.perf_counter()
con.execute(f"COPY (SELECT * FROM read_parquet('{asal}')) TO '{TUJUAN}' (FORMAT parquet)")
print(f"tujuan : {TUJUAN}")
print(f"selesai dalam {time.perf_counter() - mulai:.1f} detik")

cek = con.execute(f"SELECT COUNT(*) FROM read_parquet('{TUJUAN}')").fetchone()[0]
print(f"verifikasi: {cek:,} baris terbaca di SeaweedFS ->", "COCOK" if cek == n else "SELISIH!")
