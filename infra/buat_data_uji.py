"""
Bangkitkan parquet uji di SeaweedFS dari tabel master.

Komposisinya dirancang supaya KETIGA vonis muncul, termasuk MANUAL_REVIEW —
yang untuk grade 4 hanya terjadi bila `missing_count` TEPAT 2 (lihat
grade_rules.review_missing_count) DAN skor ada di rentang 85-90.

  50%  disalin persis                        -> AUTO_MATCH
  20%  nama diubah sedikit (1 huruf)         -> skor turun, umumnya masih AUTO
  15%  tempat_lahir & nama_ibu DIKOSONGKAN
       + nama diubah 1 huruf                 -> missing=2, skor ~88 -> MANUAL_REVIEW
  15%  nama diacak berat                     -> AUTO_UNMATCH
"""
import sys, pathlib, duckdb

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "components"))
from _shared import buka_koneksi  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
TUJUAN = sys.argv[2] if len(sys.argv) > 2 else "s3://synchrono/curated/uji_campur.parquet"

con = buka_koneksi()
con.execute(f"""
    COPY (
        WITH sampel AS (
            SELECT row_number() OVER () AS rn, nik, nama_lengkap,
                   tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu
            FROM pg.public.master WHERE nik IS NOT NULL LIMIT {N}
        ), ember AS (
            SELECT *, rn % 20 AS b FROM sampel
        )
        SELECT
            rn AS id,
            nik,
            CASE
                WHEN b < 10 THEN nama_lengkap                                  -- utuh
                WHEN b < 14 THEN (left(nama_lengkap,2) || 'x' || substr(nama_lengkap,4))      -- 1 huruf
                WHEN b < 17 THEN (left(nama_lengkap,2) || 'x' || substr(nama_lengkap,4))      -- 1 huruf + kosong
                ELSE regexp_replace(nama_lengkap, '[aiou]', 'e', 'g')          -- berat
            END AS nama,
            CASE WHEN b BETWEEN 14 AND 16 THEN NULL ELSE tempat_lahir END AS tempat_lahir,
            strftime(tanggal_lahir, '%d-%m-%Y') AS tanggal_lahir,
            CASE jenis_kelamin WHEN 'L' THEN 'LAKI-LAKI' ELSE 'PEREMPUAN' END AS jenis_kelamin,
            CASE WHEN b BETWEEN 14 AND 16 THEN NULL ELSE nama_ibu END AS nama_ibu
        FROM ember
    ) TO '{TUJUAN}' (FORMAT parquet)
""")
n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{TUJUAN}')").fetchone()[0]
kosong = con.execute(
    f"SELECT COUNT(*) FROM read_parquet('{TUJUAN}') WHERE tempat_lahir IS NULL"
).fetchone()[0]
print(f"{n:,} baris -> {TUJUAN}")
print(f"  di antaranya {kosong:,} baris dengan tempat_lahir & nama_ibu kosong (target MANUAL_REVIEW)")
