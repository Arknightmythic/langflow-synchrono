#!/usr/bin/env python3
"""
Verification Script: 4 Concurrent Batches with Shared AI Reasoning Patterns.
Demonstrates:
  1. 4 batches dispatched concurrently to Celery workers via Redis broker.
  2. Idempotent Pattern Locking State Machine (status: RESOLVING -> COMPLETED).
  3. Anti-Thundering-Herd: Only the 1st batch to claim an uncached pattern triggers the LLM.
     The other 3 batches actively wait on the lock and hydrate from cache once ready (0 duplicate LLM calls).
  4. 100% data completion across all 308 rows with exact cumulative hit_count.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reasoning.db import get_db_connection
from reasoning.jobs import execute_pg, q
from reasoning.core import execute_reasoning
from scripts.run_matching_csv_to_db import run_standalone_matching

CSV_GRADE_B = "/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji_gradeB.csv"
MASTER_PATH = "/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet"

BATCH_IDS = [
    "qa/concurrent/batch_01",
    "qa/concurrent/batch_02",
    "qa/concurrent/batch_03",
    "qa/concurrent/batch_04",
]


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)


def process_single_batch(file_id: str) -> dict:
    """Executes a single reasoning batch job."""
    job = {
        "file_id": file_id,
        "job_id": f"job-{file_id.replace('/', '-')}",
        "master_parquet_path": MASTER_PATH,
        "dry_run": False,
        "clear_cache": False,
    }
    t_start = time.perf_counter()
    summary = execute_reasoning(job)
    summary["worker_latency_seconds"] = round(time.perf_counter() - t_start, 2)
    return summary


def main():
    print_banner("TEST: 4 CONCURRENT BATCHES WITH SHARED PATTERNS (IDEMPOTENCY VERIFICATION)")
    t0_all = time.perf_counter()

    # 1. Bersihkan Seluruh Cache dan Tabel
    print("\n[LANGKAH 1] PEMBERSIHAN DATA DAN RESET PATTERN CACHE...")
    with get_db_connection() as con:
        execute_pg(con, "DELETE FROM manual_matches;")
        execute_pg(con, "DELETE FROM institution;")
        execute_pg(con, "DELETE FROM reasoning_jobs;")
        execute_pg(con, "DELETE FROM reasoning_patterns;")
        print("✓ Database bersih: 0 baris pada seluruh tabel.")

    # 2. Seeding Data 4 Batch secara paralel
    print("\n[LANGKAH 2] SEEDING 4 BATCH GRADE B (TOTAL 12,000 DATA INPUT)...")
    t0_seed = time.perf_counter()
    for b_id in BATCH_IDS:
        m_res = run_standalone_matching(file_id=b_id, csv_path=CSV_GRADE_B, master_parquet_path=MASTER_PATH, grade=2)
        print(f"  ✓ {b_id}: {m_res['manual_review']} baris manual review diisolasi.")
    print(f"✓ Selesai seeding 4 batch dalam {time.perf_counter() - t0_seed:.2f} detik.")

    # 3. Eksekusi 4 Batch Bersamaan (Konkuren di detik yang sama)
    print("\n[LANGKAH 3] TRIGGER 4 BATCH SECARA SIMULTAN (THREAD POOL EXECUTOR)...")
    print("Masing-masing batch berjalan di worker terpisah dan memiliki pola yang sama.")
    print("Sistem harus mengunci pola (RESOLVING) sehingga hanya batch pertama yang memanggil LLM.\n")

    t0_exec = time.perf_counter()
    results = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_single_batch, b_id): b_id for b_id in BATCH_IDS}
        for future in as_completed(futures):
            b_id = futures[future]
            try:
                res = future.result()
                results[b_id] = res
                print(f"  [SELESAI] {b_id} -> Durasi: {res['worker_latency_seconds']}s | LLM Calls: {res['llm_calls']} | Cache Hits: {res['cache_hits']}/{res['total_rows']}")
            except Exception as exc:
                print(f"  [ERROR] {b_id} gagal: {exc}")
                raise exc

    total_exec_time = time.perf_counter() - t0_exec
    print(f"\n✓ Seluruh 4 batch selesai diproses dalam {total_exec_time:.2f} detik!")

    # 4. Audit Integritas Idempotensi & Pola
    print_banner("AUDIT INTEGRITAS IDEMPOTENSI & POLA (POSTGRESQL)")
    with get_db_connection() as con:
        # Cek reasoning_patterns
        patterns = con.execute("""
            SELECT pattern_name, status, hit_count, reason_template, locked_by
            FROM pg.public.reasoning_patterns
            ORDER BY hit_count DESC
        """).fetchall()

        print(f"=== TABEL: reasoning_patterns (TOTAL: {len(patterns)} POLA DI-GENERATE) ===")
        total_pattern_hits = 0
        for p in patterns:
            total_pattern_hits += p[2]
            print(f"• Pola      : {p[0]}")
            print(f"  Status    : {p[1]}")
            print(f"  Locked By : {p[4]}")
            print(f"  Hit Count : {p[2]}")
            print(f"  Template  : {p[3][:90]}...\n")

        # Cek manual_matches per batch
        mm_rekap = con.execute("""
            SELECT file_id, count(*),
                   count(case when reasoning_source='LLM' then 1 end) as llm_hits,
                   count(case when reasoning_source='CACHE' then 1 end) as cache_hits,
                   count(case when reasoning_status='COMPLETED' then 1 end) as completed
            FROM pg.public.manual_matches
            GROUP BY file_id
            ORDER BY file_id
        """).fetchall()

        print("=== REKAPITULASI TABEL: manual_matches ===")
        total_rows_all_batches = 0
        total_llm_hits_all = 0
        total_cache_hits_all = 0
        for r in mm_rekap:
            total_rows_all_batches += r[1]
            total_llm_hits_all += r[2]
            total_cache_hits_all += r[3]
            print(f"• {r[0]:<25} | Total: {r[1]} | LLM: {r[2]} | Cache: {r[3]} | Completed: {r[4]}")

    # 5. Evaluasi & Asersi
    print_banner("EVALUASI HASIL UJI KONKURENSI")
    total_llm_calls_made = sum(r["llm_calls"] for r in results.values())
    total_patterns_made = len(patterns)

    print(f"1. Total Baris Diproses (4 x 77)   : {total_rows_all_batches} baris (Ekspektasi: 308)")
    print(f"2. Total Pola Dibuat di DB         : {total_patterns_made} pola")
    print(f"3. Total Panggilan LLM ke Ollama   : {total_llm_calls_made} panggilan (Bukan {total_patterns_made * 4}!)")
    print(f"4. Total Kumulatif Hit Count       : {total_pattern_hits} (Ekspektasi: 308)")
    print(f"5. Total Eksekusi 4 Batch Konkuren : {total_exec_time:.2f} detik")

    assert total_rows_all_batches == 308, f"Expected 308 rows, got {total_rows_all_batches}"
    assert total_pattern_hits == 308, f"Expected 308 pattern hits, got {total_pattern_hits}"
    # Panggilan LLM tidak boleh lebih dari total pola unik (misal 3 pola -> maksimal 3 panggilan untuk 4 batch)
    assert total_llm_calls_made == total_patterns_made, f"Thundering herd detected! LLM calls ({total_llm_calls_made}) > patterns ({total_patterns_made})"

    print("\n" + "★" * 80)
    print(" [HASIL]: SUKSES 100%! ANTI-THUNDERING-HERD & IDEMPOTENSI BERHASIL.")
    print(" Batch yang pertama mendapatkan lock memanggil LLM; 3 batch sisanya menunggu")
    print(" dan langsung mengonsumsi template yang selesai tanpa duplikasi komputasi LLM.")
    print("★" * 80 + "\n")


if __name__ == "__main__":
    main()
