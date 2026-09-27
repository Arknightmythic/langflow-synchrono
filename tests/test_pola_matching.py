"""
Uji pola review matching (`pattern_group`) dengan pasangan buatan.

Pada data uji 200 ribu baris, hanya GENERAL_REVIEW dan NIK_CONFLICT yang
muncul — SPELLING_NAME, TITLE_DEGREE, dan SWAPPED_DOB tidak pernah. Tanpa uji
ini, tiga cabang itu belum pernah terbukti benar sama sekali.

Menjalankan:
    docker exec synchrono-langflow python /synchrono/tests/test_pola_matching.py
"""
import sys

sys.path.insert(0, "/synchrono/lib")
sys.path.insert(0, "lib")

import duckdb  # noqa: E402

from _matching import sql_pola  # noqa: E402
from _shared import SQL_MACRO  # noqa: E402

# (harapan, nik_in, nama_in, tgl_in, nik_m, nama_m, tgl_m, nik_milik_lain)
KASUS = [
    ("NIK_CONFLICT",   "3171010101900001", "SITI AISYAH",               "1990-01-01",
                       "3171010101900001", "BUDI SANTOSO",              "1990-01-01", False),
    ("NIK_CONFLICT",   "3171010101900009", "BUDI SANTOSO",              "1990-01-01",
                       "3171010101900002", "BUDI SANTOSO",              "1990-01-01", True),
    ("TITLE_DEGREE",   None,               "DRS. H. BAMBANG UTOMO, M.SI", "1970-05-05",
                       "3171010505700001", "BAMBANG UTOMO",             "1970-05-05", False),
    ("SWAPPED_DOB",    None,               "RINA WIJAYANTI",            "1990-05-02",
                       "3171010202900001", "RINA WIJAYANTI",            "1990-02-05", False),
    ("SPELLING_NAME",  None,               "ACHMAD SYAHRUL",            "1992-02-02",
                       "3171010202920001", "AHMAD SYAHRUL",             "1992-02-02", False),
    ("GENERAL_REVIEW", None,               "SALWA NATSIR",              "1956-04-21",
                       "3171012104560001", "SALWA NATSIR",              "1956-04-21", False),
    # Tanggal SAMA hari dan bulannya (05-05) -> tertukar pun tetap sama, jadi
    # bukan SWAPPED_DOB. Tanpa baris ini, aturan hari=bulan bisa salah tuduh.
    ("GENERAL_REVIEW", None,               "DEWI LESTARI",              "1985-05-05",
                       "3171010505850001", "DEWI LESTARI",              "1985-05-05", False),
]


def main() -> int:
    con = duckdb.connect()
    con.execute(SQL_MACRO)
    gagal = 0
    for harapan, nik_i, nama_i, tgl_i, nik_m, nama_m, tgl_m, milik_lain in KASUS:
        q = lambda v: "NULL" if v is None else "'" + v.replace("'", "''") + "'"  # noqa: E731
        hasil = con.execute(f"""
            WITH k AS (SELECT {str(milik_lain).upper()} AS nik_milik_lain),
                 i AS (SELECT {q(nik_i)} AS nik, {q(nama_i)} AS nama,
                              lower(trim({q(nama_i)})) AS nama_clean,
                              CAST({q(tgl_i)} AS DATE) AS tanggal_lahir_clean),
                 m AS (SELECT {q(nik_m)} AS nik, {q(nama_m)} AS nama_lengkap,
                              lower(trim({q(nama_m)})) AS nama_master_clean,
                              CAST({q(tgl_m)} AS DATE) AS tanggal_lahir_master_clean)
            SELECT {sql_pola()} FROM k, i, m
        """).fetchone()[0]
        tanda = "ok " if hasil == harapan else "GAGAL"
        gagal += hasil != harapan
        print(f"  {tanda} {harapan:15s} -> {hasil:15s}  '{nama_i}' vs '{nama_m}'")
    print(f"\n{len(KASUS) - gagal}/{len(KASUS)} lulus")
    return 1 if gagal else 0


if __name__ == "__main__":
    raise SystemExit(main())
