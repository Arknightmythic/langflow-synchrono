"""Grow the 2-million bench master to a larger master for scale tests.

    python bench/make_master.py <master.parquet> <out.parquet> <rows>

The original people are kept as they are, so the A-E test files and their answer
keys stay valid. Every extra person is synthetic but drawn from the same
distributions: name and sex from one random original person, mother's name from
another, birth place and region from a third, a birth date in the same range, and
the same share of deceased. Their NIKs start with 99, a province code that does
not exist, so they never equal a NIK in an incoming file. Same input, same output.
"""
import sys
import time

import duckdb

source, target, rows = sys.argv[1], sys.argv[2], int(sys.argv[3])
started = time.perf_counter()
con = duckdb.connect()
con.execute("SET preserve_insertion_order = false")

con.execute(f"CREATE TABLE src AS SELECT row_number() OVER () - 1 AS rid, * FROM read_parquet('{source}')")
original = con.execute("SELECT count(*) FROM src").fetchone()[0]
low, days, dead = con.execute("""
    SELECT min(tanggal_lahir), datediff('day', min(tanggal_lahir), max(tanggal_lahir)) + 1,
           count(*) FILTER (WHERE status_kematian = 'MENINGGAL') * 1000 // count(*)
    FROM src""").fetchone()
extra = max(rows - original, 0)
print(f"[master] {original:,} original rows, adding {extra:,} synthetic", flush=True)

con.execute(f"""
COPY (
    SELECT nik, nama_lengkap, nama_ibu, tempat_lahir, tanggal_lahir, jenis_kelamin,
           provinsi, kabupaten, kecamatan, kelurahan, status_kematian
    FROM src
    UNION ALL
    SELECT '99' || lpad(CAST(r.i AS VARCHAR), 14, '0') AS nik,
           a.nama_lengkap, b.nama_ibu, c.tempat_lahir,
           DATE '{low}' + CAST(hash(r.i, 'tanggal') % {days} AS INTEGER) AS tanggal_lahir,
           a.jenis_kelamin, c.provinsi, c.kabupaten, c.kecamatan, c.kelurahan,
           CASE WHEN hash(r.i, 'status') % 1000 < {dead} THEN 'MENINGGAL' ELSE 'HIDUP' END
    FROM range({extra}) r(i)
    JOIN src a ON a.rid = hash(r.i, 'nama') % {original}
    JOIN src b ON b.rid = hash(r.i, 'ibu') % {original}
    JOIN src c ON c.rid = hash(r.i, 'wilayah') % {original}
) TO '{target}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1000000)
""")

total, niks = con.execute(f"SELECT count(*), count(DISTINCT nik) FROM read_parquet('{target}')").fetchone()
print(f"[master] wrote {total:,} rows ({niks:,} distinct NIK) to {target} "
      f"in {time.perf_counter() - started:.0f}s", flush=True)
