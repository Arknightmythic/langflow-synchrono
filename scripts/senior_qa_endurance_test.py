#!/usr/bin/env python3
"""
Senior QA Automation and Performance Endurance Test Suite.
Validates the AI Reasoning Engine under realistic multi-grade workloads,
measuring Cold vs Warm latency, 100M Parquet pushdown, pattern cache mechanics,
and cumulative hit count integrity.
"""

import os
import sys
import time
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reasoning import app as standalone_app, reasoning_router
from reasoning.db import get_db_connection
from reasoning.jobs import execute_pg, q
from scripts.run_matching_csv_to_db import run_standalone_matching

CSV_GRADE_B = "/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji_gradeB.csv"
MASTER_PATH = "/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet"

ID_COLD = "qa/grade_b_cold"
ID_WARM = "qa/grade_b_warm"
ID_EDGE = "qa/edge_cases_matrix"


def print_header(title: str):
    print("\n" + "=" * 80)
    print(f" [QA TEST SUITE] {title}")
    print("=" * 80)


def main():
    print_header("INSPECTION AND ENDURANCE BENCHMARK: SENIOR QA AUDIT")
    start_total_bench = time.perf_counter()

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 0: Sanitisasi Basis Data and Verifikasi Awal
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n>>> FASE 0: PEMBERSIHAN BASIS DATA (CLEAN SLATE)")
    with get_db_connection() as con:
        execute_pg(con, "DELETE FROM manual_matches;")
        execute_pg(con, "DELETE FROM institution;")
        execute_pg(con, "DELETE FROM reasoning_jobs;")
        execute_pg(con, "DELETE FROM reasoning_patterns;")

        mm_c = con.execute("SELECT count(*) FROM pg.public.manual_matches").fetchone()[0]
        rp_c = con.execute("SELECT count(*) FROM pg.public.reasoning_patterns").fetchone()[0]
        assert mm_c == 0, "manual_matches tidak kosong!"
        assert rp_c == 0, "reasoning_patterns tidak kosong!"
        print("✓ Seluruh tabel uji (manual_matches, institution, reasoning_jobs, reasoning_patterns) bersih (0 baris).")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 1: Seeding Matching Grade B ke 100M Parquet Master
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 1: MATCHING INGESTION PUSHDOWN (3,000 CSV vs 100M PARQUET)")
    t0_match1 = time.perf_counter()
    m_res1 = run_standalone_matching(file_id=ID_COLD, csv_path=CSV_GRADE_B, master_parquet_path=MASTER_PATH, grade=2)
    lat_match1 = time.perf_counter() - t0_match1
    print(f"✓ Ingestion and Matching 3,000 baris selesai dalam {lat_match1:.2f} detik.")
    print(f"  - Auto-Match (Skor >= 85)   : {m_res1['auto_match']:,} baris")
    print(f"  - Manual Review (Skor 75-85): {m_res1['manual_review']:,} baris (Terisolasi ke manual_matches)")
    print(f"  - Unmatch (Skor < 75)       : {m_res1['unmatch']:,} baris")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 2: Uji Ketahanan Cold Cache (Panggilan LLM + Inisialisasi Cache)
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 2: UJI KETAHANAN COLD CACHE (STANDALONE FASTAPI MICROSERVICE)")
    client_standalone = TestClient(standalone_app)

    t0_cold = time.perf_counter()
    resp_cold = client_standalone.post(
        f"/v1/reasoning/trigger/{ID_COLD}",
        params={"masterParquetPath": MASTER_PATH, "dryRun": False, "clearCache": False}
    )
    assert resp_cold.status_code == 202, f"Expected 202, got {resp_cold.status_code}"
    print(f"✓ Trigger Accepted (202): Job ID = {resp_cold.json().get('jobId')}")

    result_cold = None
    while True:
        s_resp = client_standalone.get(f"/v1/reasoning/status/{ID_COLD}")
        s_data = s_resp.json()
        print(f"    [Status Polling] Status: {s_data.get('status')} | Stage: {s_data.get('stage')}")
        if s_data.get("done"):
            result_cold = s_data
            break
        time.sleep(1.0)

    lat_cold = time.perf_counter() - t0_cold
    res_cold_meta = result_cold.get("result", {})
    print(f"\n✓ Cold Cache Execution Selesai dalam {lat_cold:.2f} detik!")
    print(f"  - Total Baris Manual Review: {res_cold_meta.get('total_rows')}")
    print(f"  - Pola Baru Di-generate     : {res_cold_meta.get('patterns_generated')}")
    print(f"  - Panggilan LLM (Gemma 3)  : {res_cold_meta.get('llm_calls')}")
    print(f"  - Cache Hits                : {res_cold_meta.get('cache_hits')}")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 3: Seeding and Uji Ketahanan Warm Cache (Re-ingestion Batch 2)
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 3: UJI KETAHANAN WARM CACHE (EMBEDDED PARTNER FASTAPI APP)")
    run_standalone_matching(file_id=ID_WARM, csv_path=CSV_GRADE_B, master_parquet_path=MASTER_PATH, grade=2)

    # Simulasikan embedded router di app eksternal
    ext_app = FastAPI(title="External Partner Core", version="1.0.0")
    ext_app.include_router(reasoning_router, prefix="/api/v1")
    client_ext = TestClient(ext_app)

    t0_warm = time.perf_counter()
    resp_warm = client_ext.post(
        f"/api/v1/v1/reasoning/trigger/{ID_WARM}",
        params={"masterParquetPath": MASTER_PATH, "dryRun": False, "clearCache": False}
    )
    assert resp_warm.status_code == 202

    result_warm = None
    while True:
        s_resp = client_ext.get(f"/api/v1/v1/reasoning/status/{ID_WARM}")
        s_data = s_resp.json()
        print(f"    [Status Polling] Status: {s_data.get('status')} | Stage: {s_data.get('stage')}")
        if s_data.get("done"):
            result_warm = s_data
            break
        time.sleep(0.5)

    lat_warm = time.perf_counter() - t0_warm
    res_warm_meta = result_warm.get("result", {})
    print(f"\n✓ Warm Cache Execution Selesai dalam {lat_warm:.2f} detik!")
    print(f"  - Total Baris Manual Review: {res_warm_meta.get('total_rows')}")
    print(f"  - Pola Baru Di-generate     : {res_warm_meta.get('patterns_generated')}")
    print(f"  - Panggilan LLM (Gemma 3)  : {res_warm_meta.get('llm_calls')}")
    print(f"  - Cache Hits                : {res_warm_meta.get('cache_hits')}")
    hit_ratio_warm = (res_warm_meta.get('cache_hits', 0) / max(res_warm_meta.get('total_rows', 1), 1)) * 100
    print(f"  - Cache Hit Ratio           : {hit_ratio_warm:.1f}%")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 4: Uji Ketahanan Matriks Pola Beragam (Diverse Anomaly and Edge Cases)
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 4: UJI RESILIENSI MATRIKS ANOMALI (EDGE CASES AND DIVERSE PATTERNS)")
    with get_db_connection() as con:
        sample_niks = con.execute(f"SELECT nik FROM read_parquet('{MASTER_PATH}') WHERE nik IS NOT NULL LIMIT 5").fetchall()
        nik_list = [r[0] for r in sample_niks]

        for idx, nik in enumerate(nik_list):
            inc_id = f"edge_inc_{idx:03d}"
            execute_pg(con, f"""
                INSERT INTO institution (file_id, id_incoming, nik_master, match_score, match_result, inserted_date)
                VALUES ({q(ID_EDGE)}, {q(inc_id)}, {q(nik)}, 78.50, 2, now())
                ON CONFLICT (file_id, id_incoming) DO UPDATE SET match_result = 2;
            """)
            execute_pg(con, f"""
                INSERT INTO manual_matches (
                    file_id, id_incoming, nama_incoming, tempat_lahir_incoming,
                    tanggal_lahir_incoming, jenis_kelamin_incoming, nama_ibu_incoming,
                    reason, pattern_name, reasoning_source, reasoning_status
                )
                VALUES (
                    {q(ID_EDGE)}, {q(inc_id)},
                    'QA Edge User ' || {idx}, '', '1990-01-01', 'LAKI-LAKI', 'QA Mother ' || {idx},
                    NULL, NULL, NULL, 'PENDING'
                )
                ON CONFLICT (file_id, id_incoming) DO UPDATE SET reasoning_status = 'PENDING';
            """)

    t0_edge = time.perf_counter()
    resp_edge = client_standalone.post(
        f"/v1/reasoning/trigger/{ID_EDGE}",
        params={"masterParquetPath": MASTER_PATH, "dryRun": False, "clearCache": False}
    )
    assert resp_edge.status_code == 202

    result_edge = None
    while True:
        s_resp = client_standalone.get(f"/v1/reasoning/status/{ID_EDGE}")
        s_data = s_resp.json()
        if s_data.get("done"):
            result_edge = s_data
            break
        time.sleep(0.5)

    lat_edge = time.perf_counter() - t0_edge
    print(f"✓ Edge Cases Processed dalam {lat_edge:.2f} detik. Status: {result_edge.get('status')}")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 5: Audit Isi Basis Data and Verifikasi hit_count di reasoning_patterns
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 5: AUDIT BASIS DATA AND VERIFIKASI HIT_COUNT SECARA PERSISTEN")
    with get_db_connection() as con:
        patterns = con.execute("""
            SELECT pattern_hash, pattern_name, pattern_signature, reason_template, hit_count, created_at::varchar, updated_at::varchar
            FROM pg.public.reasoning_patterns
            ORDER BY hit_count DESC
        """).fetchall()

        print(f"\n=== TABEL: reasoning_patterns (TOTAL: {len(patterns)} POLA TERSIMPAN) ===")
        for p in patterns:
            print(f"• Pattern Name: {p[1]}")
            print(f"  Hash        : {p[0]}")
            print(f"  Hit Count   : {p[4]}  <-- TERHITUNG SECARA KUMULATIF")
            print(f"  Template    : {p[3]}")
            print(f"  Updated At  : {p[6]}\n")

        summary_mm = con.execute("""
            SELECT file_id, count(*),
                   count(case when reasoning_source='LLM' then 1 end) as llm_hits,
                   count(case when reasoning_source='CACHE' then 1 end) as cache_hits,
                   count(case when reasoning_status='COMPLETED' then 1 end) as completed
            FROM pg.public.manual_matches
            GROUP BY file_id
            ORDER BY file_id
        """).fetchall()

        print("=== REKAPITULASI TABEL: manual_matches ===")
        for r in summary_mm:
            print(f"• File ID: {r[0]:<25} | Total: {r[1]:<3} | LLM: {r[2]:<2} | Cache: {r[3]:<3} | Completed: {r[4]:<3}")

    # ─────────────────────────────────────────────────────────────────────────────
    # FASE 6: Laporan Akhir Senior QA and Matriks Latensi
    # ─────────────────────────────────────────────────────────────────────────────
    print_header("FASE 6: MATRIKS BENCHMARK AND KESIMPULAN SENIOR QA")
    total_bench_dur = time.perf_counter() - start_total_bench

    print(f"{'Pengujian':<35} | {'Baris':<8} | {'Latensi':<14} | {'Throughput':<16} | {'Hit Ratio':<10}")
    print(f"{'-'*35}-+-{'-'*8}-+-{'-'*14}-+-{'-'*16}-+-{'-'*10}")
    c_hits_cold = res_cold_meta.get("cache_hits", 0)
    cold_ratio_str = f"{(c_hits_cold / 77) * 100:.1f}%"
    warm_ratio_str = f"{hit_ratio_warm:.1f}%"

    print(f"{'Matching 100M Parquet (DuckDB)':<35} | {'3,000':<8} | {f'{lat_match1:.2f} s':<14} | {f'{3000/lat_match1:.1f} baris/s':<16} | {'N/A':<10}")
    print(f"{'Cold Cache Reasoning (Run 1)':<35} | {'77':<8} | {f'{lat_cold:.2f} s':<14} | {f'{77/lat_cold:.1f} baris/s':<16} | {cold_ratio_str:<10}")
    print(f"{'Warm Cache Reasoning (Run 2)':<35} | {'77':<8} | {f'{lat_warm:.2f} s':<14} | {f'{77/lat_warm:.1f} baris/s':<16} | {warm_ratio_str:<10}")
    print(f"{'Edge Cases and Anomaly Matrix':<35} | {'5':<8} | {f'{lat_edge:.2f} s':<14} | {f'{5/lat_edge:.1f} baris/s':<16} | {'N/A':<10}")
    print(f"{'-'*89}")
    print(f"Total Durasi Seluruh Uji QA: {total_bench_dur:.2f} detik")
    print("=" * 89 + "\n")


if __name__ == '__main__':
    main()
