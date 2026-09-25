"""
Standalone Matching & Manual Review Flagger (CSV incoming x 100M Parquet Master).

Matches an incoming CSV against the 100M master Parquet file, classifies records
into Auto-Match (1), Manual Review (2), or Unmatch (3), and persists the results
into PostgreSQL `institution` and `manual_matches` (PENDING) tables.

Usage:
    python3 run_matching_csv_to_db.py \
        --file-id test_grade_b_01 \
        --csv /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji_gradeB.csv \
        --master-parquet /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet
"""

import argparse
import sys
import time
from pathlib import Path

_DISINI = Path(__file__).resolve().parent
for _kandidat in (_DISINI.parent / "lib", _DISINI / "lib", Path("/synchrono/lib")):
    if _kandidat.is_dir():
        sys.path.insert(0, str(_kandidat))
        break

from _reasoning_jobs import execute_pg, q
from _shared import buka_koneksi


def run_standalone_matching(file_id: str, csv_path: str, master_parquet_path: str, grade: int = 2) -> dict:
    start_time = time.perf_counter()
    con = buka_koneksi()

    print(f"\n{'='*72}")
    print(f"STANDALONE MATCHING & MANUAL REVIEW FLAGGING")
    print(f"{'='*72}")
    print(f"File ID        : {file_id}")
    print(f"Incoming CSV   : {csv_path}")
    print(f"Master Parquet : {master_parquet_path}")
    print(f"Grade Code     : {grade}")
    print(f"{'='*72}\n")

    try:
        # 1. Clean existing records for this file_id in PG
        print(f"[1/4] Preparing PostgreSQL tables for file_id='{file_id}'...")
        execute_pg(con, f"DELETE FROM manual_matches WHERE file_id = {q(file_id)};")
        execute_pg(con, f"DELETE FROM institution WHERE file_id = {q(file_id)};")

        # 2. Run high-performance DuckDB match & score
        print(f"[2/4] Matching incoming CSV against 100M Master Parquet...")
        t0 = time.perf_counter()

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE matched_results AS
            WITH incoming AS (
                SELECT 
                    'inc_' || lpad(row_number() over ()::varchar, 5, '0') AS id_incoming,
                    printf('%016d', CAST(nik AS BIGINT)) AS nik,
                    TRIM(COALESCE(nama, '')) AS nama,
                    lower(trim(COALESCE(nama, ''))) AS nama_clean,
                    TRIM(COALESCE(tempat_lahir, '')) AS tempat_lahir,
                    lower(trim(COALESCE(tempat_lahir, ''))) AS tempat_lahir_clean,
                    TRIM(COALESCE(CAST(tanggal_lahir AS VARCHAR), '')) AS tanggal_lahir,
                    TRIM(COALESCE(jenis_kelamin, '')) AS jenis_kelamin,
                    TRIM(COALESCE(nama_ibu, '')) AS nama_ibu,
                    lower(trim(COALESCE(nama_ibu, ''))) AS nama_ibu_clean
                FROM read_csv_auto({q(csv_path)})
            ),
            target_master AS (
                SELECT 
                    CAST(m.nik AS VARCHAR) AS nik_master,
                    TRIM(COALESCE(m.nama_lengkap, '')) AS nama_lengkap,
                    lower(trim(COALESCE(m.nama_lengkap, ''))) AS nama_master_clean,
                    TRIM(COALESCE(m.tempat_lahir, '')) AS tempat_lahir_master,
                    lower(trim(COALESCE(m.tempat_lahir, ''))) AS tempat_lahir_master_clean,
                    TRIM(COALESCE(m.nama_ibu, '')) AS nama_ibu_master,
                    lower(trim(COALESCE(m.nama_ibu, ''))) AS nama_ibu_master_clean
                FROM read_parquet({q(master_parquet_path)}) m
                WHERE m.nik IN (SELECT DISTINCT nik FROM incoming)
            ),
            scored AS (
                SELECT
                    i.id_incoming,
                    i.nik,
                    i.nama,
                    i.tempat_lahir,
                    i.tanggal_lahir,
                    i.jenis_kelamin,
                    i.nama_ibu,
                    m.nik_master,
                    m.nama_lengkap,
                    m.tempat_lahir_master,
                    m.nama_ibu_master,
                    ROUND((
                        0.8 * j(i.nama_clean, m.nama_master_clean) +
                        0.1 * j(i.tempat_lahir_clean, m.tempat_lahir_master_clean) +
                        0.1 * j(i.nama_ibu_clean, m.nama_ibu_master_clean)
                    ) * 100, 2) AS score
                FROM incoming i
                LEFT JOIN target_master m ON i.nik = m.nik_master
            ),
            ranked AS (
                SELECT 
                    *,
                    CASE
                        WHEN nik_master IS NULL THEN 3
                        WHEN score >= 85.0 THEN 1
                        WHEN score >= 75.0 THEN 2
                        ELSE 3
                    END AS match_result
                FROM scored
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY id_incoming 
                    ORDER BY score DESC, nik_master NULLS LAST
                ) = 1
            )
            SELECT * FROM ranked;
        """)
        durasi_match = time.perf_counter() - t0
        print(f"      Matched and classified in {durasi_match:.2f}s")

        # 3. Summary breakdown
        summary_counts = con.execute("""
            SELECT match_result, count(*), round(avg(score), 2)
            FROM matched_results
            GROUP BY 1
            ORDER BY 1
        """).fetchall()

        hasil = {1: 0, 2: 0, 3: 0}
        avg_scores = {}
        for mr, cnt, avg_s in summary_counts:
            hasil[mr] = cnt
            avg_scores[mr] = avg_s

        total_rows = sum(hasil.values())
        print(f"\n[3/4] MATCHING CLASSIFICATION RESULTS:")
        print(f"      • Auto-Match    (Score >= 85) : {hasil[1]:,} rows (Avg score: {avg_scores.get(1, 0.0)})")
        print(f"      • Manual Review (Score 75-85) : {hasil[2]:,} rows (Avg score: {avg_scores.get(2, 0.0)})  <-- FLAGGED")
        print(f"      • Unmatch       (Score < 75)  : {hasil[3]:,} rows")
        print(f"      • Total Processed             : {total_rows:,} rows")

        # 4. Persist to PostgreSQL (institution & manual_matches)
        print(f"\n[4/4] Writing records to PostgreSQL...")
        t0 = time.perf_counter()

        # A. Insert all into institution
        con.execute(f"""
            INSERT INTO pg.public.institution (
                file_id, id_incoming, nik_master, match_score, match_result, upload_date, inserted_date
            )
            SELECT 
                {q(file_id)},
                id_incoming,
                nik_master,
                score,
                match_result,
                now(),
                now()
            FROM matched_results;
        """)

        # B. Insert only Manual Review (match_result = 2) into manual_matches with PENDING
        if hasil[2] > 0:
            con.execute(f"""
                INSERT INTO pg.public.manual_matches (
                    file_id, id_incoming,
                    nama_incoming, tempat_lahir_incoming, tanggal_lahir_incoming,
                    jenis_kelamin_incoming, nama_ibu_incoming,
                    reason, pattern_name, reasoning_source, reasoning_status
                )
                SELECT
                    {q(file_id)},
                    id_incoming,
                    nama,
                    tempat_lahir,
                    tanggal_lahir,
                    jenis_kelamin,
                    nama_ibu,
                    NULL, NULL, NULL,
                    'PENDING'
                FROM matched_results
                WHERE match_result = 2;
            """)

        durasi_write = time.perf_counter() - t0
        print(f"      Persisted {total_rows:,} institution and {hasil[2]:,} manual_matches rows in {durasi_write:.2f}s")

        total_elapsed = time.perf_counter() - start_time
        print(f"\n{'='*72}")
        print(f"SUCCESS: Matching completed in {total_elapsed:.2f}s")
        print(f"{hasil[2]:,} rows are now PENDING in manual_matches table for AI Reasoning!")
        print(f"{'='*72}\n")

        return {
            "file_id": file_id,
            "total_rows": total_rows,
            "auto_match": hasil[1],
            "manual_review": hasil[2],
            "unmatch": hasil[3],
            "elapsed_seconds": round(total_elapsed, 2),
        }
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description="Standalone Matching & Manual Review Flagger")
    parser.add_argument("--file-id", required=True, help="Batch or File Identifier")
    parser.add_argument("--csv", required=True, help="Path to incoming CSV file")
    parser.add_argument("--master-parquet", default="/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet",
                        help="Path to 100M master Parquet file")
    parser.add_argument("--grade", type=int, default=2, help="Grade code (default: 2)")

    args = parser.parse_args()
    run_standalone_matching(
        file_id=args.file_id,
        csv_path=args.csv,
        master_parquet_path=args.master_parquet,
        grade=args.grade,
    )


if __name__ == "__main__":
    main()
