"""
Bandingkan — dan bila perlu muat — cadangan Parquet `master` ke PostgreSQL.

TIDAK BUTUH STARROCKS MAUPUN VPN. Sumbernya berkas cadangan/master_terbaru.parquet
yang dibuat oleh `cadangkan_master.py` saat server masih hidup.

    python muat_cadangan.py            # hanya BANDINGKAN, tidak menulis apa pun
    python muat_cadangan.py --muat     # isi ulang tabel master dari cadangan

Perbandingannya per NILAI, bukan sekadar jumlah baris: dua tabel bisa sama-sama
berisi 299.088 baris tapi berbeda isinya.

`--muat` MENGOSONGKAN tabel master lebih dulu. Itu aman di sini justru karena
sumbernya berkas lokal — kalau isi ulang gagal di tengah jalan, berkasnya masih
ada dan perintahnya bisa diulang. Mengisi ulang langsung dari StarRocks tidak
punya jaminan itu: kalau servernya mati di tengah proses, tabelnya tinggal
kosong.
"""

import argparse
import os
from pathlib import Path

import duckdb

PG = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")
CADANGAN = Path(__file__).parent / "cadangan" / "master_terbaru.parquet"

# Kolom yang ada di SKEMA POSTGRESQL. `id` dan `nama` milik StarRocks sengaja
# tidak ikut: `id` di sini bigserial milik PostgreSQL sendiri, dan `nama`
# duplikat dari `nama_lengkap`.
KOLOM = ["nik", "nama_lengkap", "tempat_lahir", "tanggal_lahir", "jenis_kelamin",
         "nama_ibu", "status_kematian", "provinsi", "kabupaten", "kecamatan",
         "kelurahan"]


def sambung() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG}' AS pg (TYPE postgres)")
    return con


def banding(con) -> bool:
    """True bila PostgreSQL sudah sama persis dengan cadangan."""
    con.execute(f"""
        CREATE OR REPLACE VIEW cad AS
        SELECT {', '.join(KOLOM)} FROM read_parquet('{CADANGAN.as_posix()}')
    """)

    n_cad = con.execute("SELECT count(*) FROM cad").fetchone()[0]
    n_pg = con.execute("SELECT count(*) FROM pg.public.master").fetchone()[0]
    print(f"  cadangan   : {n_cad:,} baris")
    print(f"  PostgreSQL : {n_pg:,} baris")

    if n_pg == 0:
        print("\n  PostgreSQL kosong — perlu dimuat.")
        return False

    # EXCEPT dua arah menangkap selisih ISI, bukan hanya jumlah.
    pilih = ", ".join(f"CAST({k} AS VARCHAR)" for k in KOLOM)
    kurang = con.execute(f"""
        SELECT count(*) FROM (
            SELECT {pilih} FROM cad
            EXCEPT
            SELECT {pilih} FROM pg.public.master
        )
    """).fetchone()[0]
    lebih = con.execute(f"""
        SELECT count(*) FROM (
            SELECT {pilih} FROM pg.public.master
            EXCEPT
            SELECT {pilih} FROM cad
        )
    """).fetchone()[0]

    print(f"  ada di cadangan tapi tidak di PostgreSQL : {kurang:,}")
    print(f"  ada di PostgreSQL tapi tidak di cadangan : {lebih:,}")

    sama = kurang == 0 and lebih == 0 and n_cad == n_pg
    print("\n  => " + ("IDENTIK, tidak perlu dimuat ulang." if sama
                       else "BERBEDA — jalankan dengan --muat untuk menyamakan."))
    return sama


def muat(con) -> None:
    n_awal = con.execute("SELECT count(*) FROM pg.public.master").fetchone()[0]
    if n_awal:
        print(f"  mengosongkan master ({n_awal:,} baris)…")
        con.execute("CALL postgres_execute('pg', 'TRUNCATE master')")

    print("  memuat dari cadangan…")
    con.execute(f"""
        INSERT INTO pg.public.master ({', '.join(KOLOM)})
        SELECT {', '.join(KOLOM)} FROM cad
    """)
    n = con.execute("SELECT count(*) FROM pg.public.master").fetchone()[0]
    print(f"  selesai: {n:,} baris di PostgreSQL")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--muat", action="store_true",
                   help="isi ulang tabel master (MENGOSONGKAN lebih dulu)")
    args = p.parse_args()

    if not CADANGAN.exists():
        raise SystemExit(
            f"Cadangan tidak ada: {CADANGAN}\n"
            "Jalankan cadangkan_master.py saat VPN & StarRocks masih hidup."
        )

    print(f"cadangan: {CADANGAN}")
    print(f"          {CADANGAN.stat().st_size / 1_048_576:.1f} MB\n")

    con = sambung()
    sama = banding(con)

    if args.muat:
        if sama:
            print("\n(--muat diberikan, tapi isinya sudah identik. Tetap dimuat ulang.)")
        print()
        muat(con)
        print()
        banding(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
