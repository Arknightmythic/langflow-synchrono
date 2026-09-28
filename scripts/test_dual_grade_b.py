#!/usr/bin/env python3
"""
Dual Grade B AI Reasoning Test Runner.

Demonstrates and verifies two deployment/integration paradigms:
  1. Versi 1: Independent / Standalone FastAPI Microservice (Cold Cache)
  2. Versi 2: Embedded ('Dijahit') into an external partner FastAPI app (Warm Cache)

Dataset: Grade B (77 manual review candidates from uji_gradeB.csv matched against 100M Parquet)
"""

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
LOCAL_MASTER = "/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet"

# Use local master parquet for reliable high-speed host testing
MASTER_PATH = LOCAL_MASTER

ID_TEST_1 = "test/fastapi/grade_b_v2"
ID_TEST_2 = "external/partner/grade_b_v2"


def print_banner(title: str):
    print(f"\n{'='*78}")
    print(f" {title}")
    print(f"{'='*78}")


def main():
    print_banner("DUAL GRADE B REASONING TEST: STANDALONE FASTAPI VS EMBEDDED")
    print(f"Incoming CSV   : {CSV_GRADE_B}")
    print(f"Master Dataset : {MASTER_PATH}")
    print(f"Test 1 ID      : {ID_TEST_1} (Standalone FastAPI)")
    print(f"Test 2 ID      : {ID_TEST_2} (Embedded Third-Party App)")

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 1: Bersihkan DB & Hapus Seluruh Cache Pola
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 1: PEMBERSIHAN BASIS DATA & CACHE POLA")
    with get_db_connection() as con:
        # Hapus data uji lama
        execute_pg(con, f"DELETE FROM manual_matches WHERE file_id IN ({q(ID_TEST_1)}, {q(ID_TEST_2)});")
        execute_pg(con, f"DELETE FROM institution WHERE file_id IN ({q(ID_TEST_1)}, {q(ID_TEST_2)});")
        execute_pg(con, f"DELETE FROM reasoning_jobs WHERE file_id IN ({q(ID_TEST_1)}, {q(ID_TEST_2)});")

        # Bersihkan seluruh cache pattern
        p_count = con.execute("SELECT count(*) FROM pg.public.reasoning_patterns;").fetchone()[0]
        execute_pg(con, "DELETE FROM reasoning_patterns;")
        print(f"✓ Berhasil menghapus {p_count} pola dari tabel reasoning_patterns (Cache direset ke 0).")
        print(f"✓ Tabel manual_matches dan institution untuk kedua ID telah dibersihkan.")

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 2: Seeding Data Matching Grade B (Matching ke 100M Parquet)
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 2: SEEDING DATA GRADE B KE DATABASE")
    print(f"--> Melakukan matching untuk {ID_TEST_1}...")
    run_standalone_matching(file_id=ID_TEST_1, csv_path=CSV_GRADE_B, master_parquet_path=MASTER_PATH, grade=2)

    print(f"--> Melakukan matching untuk {ID_TEST_2}...")
    run_standalone_matching(file_id=ID_TEST_2, csv_path=CSV_GRADE_B, master_parquet_path=MASTER_PATH, grade=2)

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 3: VERSI 1 — Standalone FastAPI Microservice (Cold Cache)
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 3: UJI VERSI 1 — STANDALONE FASTAPI MICROSERVICE (COLD CACHE)")
    print("Inisialisasi klien HTTP untuk standalone FastAPI app...")
    client_v1 = TestClient(standalone_app)

    # Health check
    h_resp = client_v1.get("/v1/reasoning/health")
    print(f"✓ Health Check: {h_resp.status_code} -> {h_resp.json()}")

    # Trigger via path dengan slash
    print(f"--> Memicu job via POST /v1/reasoning/trigger/{ID_TEST_1}...")
    t_start_v1 = time.perf_counter()
    trigger_resp = client_v1.post(
        f"/v1/reasoning/trigger/{ID_TEST_1}",
        params={"masterParquetPath": MASTER_PATH, "dryRun": False, "clearCache": False}
    )
    assert trigger_resp.status_code == 202, f"Expected 202, got {trigger_resp.status_code}"
    print(f"✓ Trigger Accepted (202): {trigger_resp.json()}")

    # Polling status
    print(f"--> Memantau status pengerjaan via GET /v1/reasoning/status/{ID_TEST_1}...")
    result_v1 = None
    while True:
        s_resp = client_v1.get(f"/v1/reasoning/status/{ID_TEST_1}")
        s_data = s_resp.json()
        print(f"    [Status Polling] Status: {s_data.get('status')} | Stage: {s_data.get('stage')}")
        if s_data.get("done"):
            result_v1 = s_data
            break
        time.sleep(1.0)

    t_v1 = time.perf_counter() - t_start_v1
    print(f"✓ Versi 1 Selesai dalam {t_v1:.2f} detik!")

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 4: VERSI 2 — Embedded ke Program Eksternal (Warm Cache)
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 4: UJI VERSI 2 — EMBEDDED KE PROGRAM ORANG (WARM CACHE)")
    print("Mensimulasikan backend monolitik perusahaan lain...")
    external_app = FastAPI(title="External Partner Enterprise Core", version="3.5.0")

    # 'Jahit' router reasoning ke backend eksternal
    external_app.include_router(reasoning_router, prefix="/api/v1")
    print("✓ Berhasil 'menjahit' reasoning_router ke external_app pada prefix '/api/v1'.")

    client_v2 = TestClient(external_app)

    # Health check lewat prefix aplikasi eksternal
    ext_health = client_v2.get("/api/v1/v1/reasoning/health")
    print(f"✓ External App Health Check: {ext_health.status_code} -> {ext_health.json()}")

    # Trigger via external app
    print(f"--> Memicu job via POST /api/v1/v1/reasoning/trigger/{ID_TEST_2}...")
    t_start_v2 = time.perf_counter()
    trigger_ext_resp = client_v2.post(
        f"/api/v1/v1/reasoning/trigger/{ID_TEST_2}",
        params={"masterParquetPath": MASTER_PATH, "dryRun": False, "clearCache": False}
    )
    assert trigger_ext_resp.status_code == 202, f"Expected 202, got {trigger_ext_resp.status_code}"
    print(f"✓ Trigger Accepted (202): {trigger_ext_resp.json()}")

    # Polling status via external app
    print(f"--> Memantau status via GET /api/v1/v1/reasoning/status/{ID_TEST_2}...")
    result_v2 = None
    while True:
        s_resp = client_v2.get(f"/api/v1/v1/reasoning/status/{ID_TEST_2}")
        s_data = s_resp.json()
        print(f"    [Status Polling] Status: {s_data.get('status')} | Stage: {s_data.get('stage')}")
        if s_data.get("done"):
            result_v2 = s_data
            break
        time.sleep(0.5)

    t_v2 = time.perf_counter() - t_start_v2
    print(f"✓ Versi 2 Selesai dalam {t_v2:.2f} detik!")

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 5: Tampilkan Contoh Narasi Hasil Penalaran AI & Verifikasi di DB
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 5: SAMPEL HASIL PENALARAN AI DI DATABASE (manual_matches)")
    with get_db_connection() as con:
        rows_v1 = con.execute(f"""
            SELECT id_incoming, nama_incoming, reason, pattern_name, reasoning_source
            FROM pg.public.manual_matches
            WHERE file_id = {q(ID_TEST_1)}
            ORDER BY id_incoming
            LIMIT 3;
        """).fetchall()

        print(f"\n[SAMPEL VERSI 1: {ID_TEST_1}]")
        for r in rows_v1:
            print(f"• ID: {r[0]} | Nama: {r[1]}")
            print(f"  Pola   : {r[3]} ({r[4]})")
            print(f"  Alasan : {r[2]}\n")

        rows_v2 = con.execute(f"""
            SELECT id_incoming, nama_incoming, reason, pattern_name, reasoning_source
            FROM pg.public.manual_matches
            WHERE file_id = {q(ID_TEST_2)}
            ORDER BY id_incoming
            LIMIT 3;
        """).fetchall()

        print(f"\n[SAMPEL VERSI 2: {ID_TEST_2}]")
        for r in rows_v2:
            print(f"• ID: {r[0]} | Nama: {r[1]}")
            print(f"  Pola   : {r[3]} ({r[4]})")
            print(f"  Alasan : {r[2]}\n")

    # ─────────────────────────────────────────────────────────────────────────────
    # LANGKAH 6: Tabel Perbandingan Hasil Uji
    # ─────────────────────────────────────────────────────────────────────────────
    print_banner("LANGKAH 6: TABEL PERBANDINGAN HASIL UJI")
    res1 = result_v1.get("result", {})
    res2 = result_v2.get("result", {})

    tot1 = max(res1.get("total_rows", 1), 1)
    tot2 = max(res2.get("total_rows", 1), 1)
    ratio_1 = f"{(res1.get('cache_hits', 0) / tot1) * 100:.1f}%"
    ratio_2 = f"{(res2.get('cache_hits', 0) / tot2) * 100:.1f}%"

    print(f"{'Metrik':<30} | {'Versi 1 (Standalone FastAPI)':<30} | {'Versi 2 (Embedded Partner App)':<30}")
    print(f"{'-'*30}-+-{'-'*30}-+-{'-'*30}")
    print(f"{'File ID':<30} | {ID_TEST_1:<30} | {ID_TEST_2:<30}")
    print(f"{'Model Integrasi':<30} | {'Standalone Microservice':<30} | {'Embedded (app.include_router)':<30}")
    print(f"{'Kondisi Cache':<30} | {'Cold Cache (Kosong)':<30} | {'Warm Cache (Pola Tersedia)':<30}")
    print(f"{'Total Baris Diproses':<30} | {res1.get('total_rows', 0):<30} | {res2.get('total_rows', 0):<30}")
    print(f"{'Pola Baru Dibuat':<30} | {res1.get('patterns_generated', 0):<30} | {res2.get('patterns_generated', 0):<30}")
    print(f"{'Panggilan LLM (Gemma 3)':<30} | {res1.get('llm_calls', 0):<30} | {res2.get('llm_calls', 0):<30}")
    print(f"{'Cache Hits':<30} | {res1.get('cache_hits', 0):<30} | {res2.get('cache_hits', 0):<30}")
    print(f"{'Cache Hit Ratio':<30} | {ratio_1:<30} | {ratio_2:<30}")
    print(f"{'Waktu Eksekusi Total':<30} | {f'{t_v1:.2f} detik':<30} | {f'{t_v2:.2f} detik':<30}")
    print(f"{'='*78}\n")


if __name__ == "__main__":
    main()
