"""
Migrasi skema PostgreSQL — dijalankan sekali per berkas, lalu dicatat.

    python migrate.py              terapkan yang belum pernah diterapkan
    python migrate.py --status     daftar migrasi & mana yang sudah jalan
    python migrate.py --kering     tampilkan yang AKAN dijalankan, tanpa menjalankan

Bedanya dengan pendekatan lama (`apply_schema.py`): berkas yang sudah pernah
diterapkan TIDAK dijalankan lagi. Riwayatnya disimpan di tabel
`schema_migrations`, jadi menjalankan ulang aman dan cepat — dan yang lebih
penting, tidak ada lagi DDL maupun data yang diam-diam dijalankan berulang.

DATA AWAL BUKAN URUSAN BERKAS INI. Migrasi hanya mengubah BENTUK basis data.
Isi awalnya (tabel referensi, pita skor, kriteria grade) ada di db/seeder/ dan
dijalankan `seed.py`. Pemisahan itu yang membuat setelan operator tidak pernah
tertimpa saat deploy.

Hanya butuh `duckdb` — DDL dijalankan lewat postgres_execute() dari extension
postgres_scanner, jadi tidak perlu psycopg maupun sqlalchemy.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import duckdb

PG_DSN = os.getenv(
    "PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres"
)
DISINI = Path(__file__).parent
MIGRASI = DISINI / "db" / "migrasi"

TABEL_RIWAYAT = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    versi       text PRIMARY KEY,
    checksum    text        NOT NULL,
    diterapkan  timestamptz NOT NULL DEFAULT now()
)
"""


def q(nilai) -> str:
    """Literal SQL. Kutip tunggal digandakan — deskripsi grade memuat apostrof."""
    if nilai is None:
        return "NULL"
    return "'" + str(nilai).replace("'", "''") + "'"


def pecah_statement(sql: str) -> list[str]:
    """
    Pisah file SQL jadi statement, abaikan komentar dan baris kosong.

    KOMENTAR DIBUANG LEBIH DULU, baru dipecah — urutannya penting. Percobaan
    pertama membuang statement yang DIAWALI `--`, dan itu ikut membuang setiap
    CREATE TABLE yang didahului komentar penjelas: `master` tidak pernah dibuat,
    lalu `CREATE INDEX idx_master_nik` gagal dengan "relation master does not
    exist" — galat yang menunjuk ke tempat yang salah.

    Titik koma DI DALAM string literal tidak dianggap pemisah. Memecah dengan
    `sql.split(";")` tampak cukup sampai ada satu deskripsi yang memuat titik
    koma — statement-nya lalu terbelah diam-diam di tengah kalimat. Itu sempat
    terjadi pada teks criteria_description grade D.
    """
    tanpa_komentar = "\n".join(
        b for b in sql.splitlines() if not b.strip().startswith("--")
    )

    hasil, sedang, dalam_kutip = [], [], False
    i = 0
    while i < len(tanpa_komentar):
        huruf = tanpa_komentar[i]
        if huruf == "'":
            # '' di dalam string adalah kutip ter-escape, bukan penutup.
            if dalam_kutip and tanpa_komentar[i + 1:i + 2] == "'":
                sedang.append("''")
                i += 2
                continue
            dalam_kutip = not dalam_kutip
            sedang.append(huruf)
        elif huruf == ";" and not dalam_kutip:
            hasil.append("".join(sedang))
            sedang = []
        else:
            sedang.append(huruf)
        i += 1
    hasil.append("".join(sedang))

    return [s.strip() for s in hasil if s.strip()]


def berkas_migrasi() -> list[Path]:
    if not MIGRASI.is_dir():
        sys.exit(f"Folder migrasi tidak ada: {MIGRASI}")
    return sorted(MIGRASI.glob("*.sql"))


def sidik(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true",
                    help="Daftar migrasi dan status penerapannya.")
    ap.add_argument("--kering", action="store_true",
                    help="Tampilkan yang akan dijalankan, tanpa menjalankan.")
    args = ap.parse_args()

    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE postgres)")

    def jalankan(sql: str) -> None:
        con.execute("CALL postgres_execute('pg', ?)", [sql])

    jalankan(TABEL_RIWAYAT)

    sudah = {
        r[0]: r[1] for r in con.execute(
            "SELECT versi, checksum FROM pg.public.schema_migrations"
        ).fetchall()
    }

    berkas = berkas_migrasi()

    if args.status:
        print(f"{len(berkas)} migrasi, {len(sudah)} sudah diterapkan\n")
        for p in berkas:
            tanda = "sudah" if p.stem in sudah else "BELUM"
            catatan = ""
            if p.stem in sudah and sudah[p.stem] != sidik(p):
                catatan = "  <- BERKAS BERUBAH setelah diterapkan"
            print(f"  [{tanda}] {p.name}{catatan}")
        return 0

    tertunda = [p for p in berkas if p.stem not in sudah]

    # Berkas yang sudah diterapkan lalu disunting adalah masalah nyata: apa yang
    # ada di basis data tidak lagi sama dengan apa yang ada di repositori, dan
    # tidak ada yang akan memberi tahu. Diperingatkan, tidak dijalankan ulang.
    for p in berkas:
        if p.stem in sudah and sudah[p.stem] != sidik(p):
            print(f"  PERINGATAN: {p.name} berubah SETELAH diterapkan.")
            print("              Isi basis data tidak lagi sama dengan berkas ini.")
            print("              Tulis migrasi BARU untuk perubahannya.\n")

    if not tertunda:
        print(f"Tidak ada migrasi tertunda ({len(sudah)} sudah diterapkan).")
        return 0

    if args.kering:
        print(f"{len(tertunda)} migrasi AKAN dijalankan:")
        for p in tertunda:
            print(f"   {p.name}")
        return 0

    for p in tertunda:
        print(f"== {p.name} ==")
        for stmt in pecah_statement(p.read_text(encoding="utf-8")):
            jalankan(stmt)
            nama = re.search(r"(TABLE|INDEX)\s+(IF NOT EXISTS\s+)?(\S+)", stmt, re.I)
            print(f"   ok  {nama.group(3) if nama else stmt.split(chr(10))[0][:52]}")
        jalankan(
            "INSERT INTO schema_migrations (versi, checksum) "
            f"VALUES ({q(p.stem)}, {q(sidik(p))}) "
            "ON CONFLICT (versi) DO UPDATE SET checksum = EXCLUDED.checksum"
        )
        print()

    print(f"{len(tertunda)} migrasi diterapkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
