"""
Seeder data awal — MENGISI YANG KOSONG, tidak pernah menimpa.

    python seed.py                 jalankan semua seeder
    python seed.py --daftar        lihat daftarnya saja
    python seed.py --hanya 002     jalankan satu seeder

JANJI UTAMA BERKAS INI: menjalankannya berulang kali tidak mengubah apa pun
yang sudah ada. Setiap seeder memakai ON CONFLICT DO NOTHING, jadi ambang yang
sudah disetel operator lewat API config TIDAK akan dikembalikan ke nilai bawaan
saat deploy berikutnya.

Itulah kenapa seeder dipisah dari migrasi. Migrasi mengubah BENTUK basis data
dan dicatat supaya jalan sekali; seeder mengisi DATA awal dan aman diulang.
Menggabungkan keduanya berarti setiap deploy diam-diam menimpa konfigurasi.

Kalau memang ingin mengembalikan sebuah tabel ke nilai bawaan, hapus dulu
barisnya lalu seed lagi — sengaja dibuat sebagai dua langkah sadar, bukan
efek samping dari `docker compose up`.

Seeder boleh berupa .sql, atau .py yang mengekspos `jalankan(sql)` untuk hal
yang tidak bisa ditulis sebagai SQL statis (mis. membaca berkas ekspor).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path

import duckdb

from migrate import pecah_statement  # pemisah statement yang sadar string

PG_DSN = os.getenv(
    "PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres"
)
DISINI = Path(__file__).parent
SEEDER = DISINI / "db" / "seeder"


def berkas_seeder() -> list[Path]:
    if not SEEDER.is_dir():
        sys.exit(f"Folder seeder tidak ada: {SEEDER}")
    return sorted(p for p in SEEDER.iterdir() if p.suffix in (".sql", ".py"))


def jalankan_py(p: Path, sql) -> None:
    spec = importlib.util.spec_from_file_location(p.stem, p)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    if not hasattr(modul, "jalankan"):
        raise RuntimeError(f"{p.name} tidak punya fungsi jalankan(sql)")
    modul.jalankan(sql)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daftar", action="store_true", help="Tampilkan daftar seeder.")
    ap.add_argument("--hanya", help="Jalankan seeder yang namanya memuat teks ini.")
    args = ap.parse_args()

    berkas = berkas_seeder()

    if args.daftar:
        print(f"{len(berkas)} seeder di {SEEDER}:")
        for p in berkas:
            print(f"   {p.name}")
        return 0

    if args.hanya:
        berkas = [p for p in berkas if args.hanya in p.name]
        if not berkas:
            sys.exit(f"Tidak ada seeder yang cocok dengan {args.hanya!r}")

    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE postgres)")

    def sql(stmt: str) -> None:
        con.execute("CALL postgres_execute('pg', ?)", [stmt])

    for p in berkas:
        print(f"== {p.name} ==")
        if p.suffix == ".py":
            jalankan_py(p, sql)
        else:
            for stmt in pecah_statement(p.read_text(encoding="utf-8")):
                sql(stmt)
                baris = stmt.split("\n")[0][:56]
                print(f"   ok  {baris}")
        print()

    # Laporan isi — supaya langsung terlihat apakah seeding berhasil, tanpa
    # perlu membuka psql.
    print("== isi sesudah seeding ==")
    for tabel in ("ref_grades", "ref_process", "ref_sync_statuses",
                  "ref_match_results", "grade_rules", "grade_bands",
                  "grade_criteria", "matching_queries"):
        try:
            n = con.execute(f"SELECT count(*) FROM pg.public.{tabel}").fetchone()[0]
            print(f"   {tabel:<20s} {n:>4} baris")
        except Exception:
            print(f"   {tabel:<20s}  (belum ada — jalankan migrate.py dulu)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
