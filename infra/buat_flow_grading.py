"""
Bangun KETIGA flow grading di Langflow — tanpa merangkai manual di kanvas.

    python buat_flow_grading.py

Idempoten: flow dengan nama sama DIPERBARUI, bukan digandakan.

TIGA FLOW, BUKAN SATU

  grading-dispatch  API 1. Dipanggil backend saat berkas selesai diunggah ke
                    S3. Mencatat job, melepas pekerja, balas 202 seketika.

  grading-status    API 2. Dipanggil berulang (polling) oleh backend selama
                    menunggu. Membaca tabel grading_jobs.

  grading           Pipeline grading itu sendiri, G1-G6. Dipanggil dispatch
                    lewat jalur dalam proses, BUKAN lewat HTTP. Flow ini tetap
                    dibuat supaya seluruh tahapannya terlihat dan bisa
                    dijalankan satu-satu dari kanvas saat menelusuri masalah —
                    tetapi memanggilnya lewat API akan MEMBLOKIR sampai selesai.
"""

import json
import os
from pathlib import Path

from flow_util import BASE, bangun, katalog, masuk, simpan_contoh

DISINI = Path(__file__).parent

PIPELINE = [
    ("OpenGradingSession",   None,      "session"),
    ("LoadRawParquet",       "session", "raw_ready"),
    ("FlagAnomalies",        "session", "flagged"),
    ("ScoreAndGrade",        "session", "scored"),
    ("WriteEnrichedParquet", "session", "written"),
    ("BuildCallbackPayload", "session", "payload"),
    ("ChatOutput",           "input_value", "message"),
]

DISPATCH = [
    ("GradingDispatch", None,          "accepted"),
    ("ChatOutput",      "input_value", "message"),
]

STATUS = [
    ("GradingStatus", None,          "status"),
    ("ChatOutput",    "input_value", "message"),
]


def main() -> int:
    token = masuk()
    print("login OK")

    per_nama = katalog(token, "grading")
    komponen = sorted(k for k in per_nama if k != "ChatOutput")
    print(f"komponen di kategori 'grading': {len(komponen)}")
    for k in komponen:
        print(f"   - {k}")
    print()

    fid_d, node_d = bangun(
        token, "Synchrono Grading Dispatch",
        "API 1 — catat job grading, lepas pekerja, balas 202 QUEUED.",
        "grading-dispatch", per_nama, DISPATCH)

    fid_s, node_s = bangun(
        token, "Synchrono Grading Status",
        "API 2 — status job grading terbaru untuk satu file_id (polling).",
        "grading-status", per_nama, STATUS)

    fid_p, node_p = bangun(
        token, "Synchrono Grading Pipeline",
        "Pipeline grading G1-G6. Sinkron; untuk penelusuran, bukan untuk backend.",
        "grading", per_nama, PIPELINE)

    # ── Berkas contoh untuk Postman ──────────────────────────────────────
    # Kunci `tweaks` HARUS node id, bukan nama komponen. Kalau memakai nama
    # komponen, flow tetap jalan tapi parameternya tidak tersampaikan dan node
    # pertama gagal dengan "fileId kosong".
    contoh_dispatch = {
        "output_type": "chat",
        "input_type": "text",
        "input_value": "",
        "tweaks": {
            node_d: {
                "payload": json.dumps({
                    "fileId": "csv_1789441689523_iq5ws",
                    "filename": "data_kependudukan.csv",
                    "storageType": "s3",
                    "s3Bucket": "bucket-test",
                    "s3Endpoint": "http://localhost:8333",
                    "csvKey": "uploads/csv_1789441689523_iq5ws/original.csv",
                    "parquetKey": "uploads/csv_1789441689523_iq5ws/data.parquet",
                    "rowCount": 50000,
                    "institution": {"id": "inst-001", "name": "Kementerian Sosial"},
                    "callback": {
                        "url": "http://host.docker.internal:3000/api/internal/grading/callback",
                        "secretToken": "syncrono-grading-callback-secret-key",
                    },
                }, ensure_ascii=False)
            }
        },
    }

    contoh_status = {
        "output_type": "chat",
        "input_type": "text",
        "input_value": "",
        "tweaks": {node_s: {"file_id": "csv_1789441689523_iq5ws"}},
    }

    simpan_contoh(DISINI / "postman_grading_dispatch.json", contoh_dispatch)
    simpan_contoh(DISINI / "postman_grading_status.json", contoh_status)

    garis = "=" * 74
    print(f"\n{garis}")
    print("  API 1 — DISPATCH")
    print(f"    POST {BASE}/api/v1/run/grading-dispatch?stream=false")
    print(f"    kanvas    : {BASE}/flow/{fid_d}")
    print(f"    node id   : {node_d}")
    print(f"    body      : infra/postman_grading_dispatch.json")
    print()
    print("  API 2 — STATUS (polling)")
    print(f"    POST {BASE}/api/v1/run/grading-status?stream=false")
    print(f"    kanvas    : {BASE}/flow/{fid_s}")
    print(f"    node id   : {node_s}")
    print(f"    body      : infra/postman_grading_status.json")
    print()
    print("  PIPELINE (penelusuran saja — MEMBLOKIR)")
    print(f"    kanvas    : {BASE}/flow/{fid_p}")
    print(f"    node id   : {node_p}")
    print(garis)
    print("\n  Header wajib pada kedua API:")
    print("    x-api-key: <API KEY>        <- BUKAN Authorization: Bearer")
    print("    Content-Type: application/json")
    print("\n  Buat API key di UI: Settings -> Langflow API Keys.")

    if os.getenv("LANGFLOW_API_KEY"):
        print("\n  (LANGFLOW_API_KEY terbaca dari environment)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
