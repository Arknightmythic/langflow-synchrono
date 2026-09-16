"""
Bangkitkan koleksi Postman dari kondisi Langflow yang SEDANG berjalan.

    python buat_postman.py
    -> infra/postman_synchrono.json  (impor ke Postman)

Node id dibaca langsung dari flow, bukan ditulis tangan. Itu penting karena
kunci `tweaks` HARUS berupa node id: salah satu huruf saja dan parameternya
tidak sampai — flow tetap jalan, lalu gagal dengan "fileId kosong". Membangkitkan
ulang koleksi ini sesudah flow dibangun ulang jauh lebih aman daripada
menyalinnya manual.

Koleksi memakai variabel:
    {{base_url}}   http://localhost:7860
    {{api_key}}    isi sendiri di Postman (lihat API.md bagian 1)
"""

import json
from pathlib import Path

from flow_util import BASE, katalog, masuk, minta  # noqa: F401

DISINI = Path(__file__).parent
KELUARAN = DISINI / "postman_synchrono.json"

# endpoint -> (nama di Postman, node yang menerima tweaks, contoh tweaks, catatan)
PERMINTAAN = {
    "grading-dispatch": (
        "1. Grading — Dispatch (POST job)",
        "GradingDispatch",
        # Menunjuk berkas uji yang BENAR-BENAR ADA di SeaweedFS. Bentuk
        # muatannya tetap lengkap sesuai spesifikasi, tapi memakai contoh yang
        # tidak ada membuat dispatch membalas QUEUED lalu gagal diam-diam di
        # latar belakang — pengalaman pertama yang membingungkan.
        {"payload": json.dumps({
            "fileId": "uji-b",
            "filename": "uji-b.csv",
            "storageType": "s3",
            "s3Bucket": "bucket-test",
            "s3Endpoint": "http://localhost:8333",
            "csvKey": "uploads/uji-b/original.csv",
            "parquetKey": "uploads/uji-b/data.parquet",
            "rowCount": 2000,
            "institution": {"id": "inst-001", "name": "Kementerian Sosial"},
            "callback": {"url": "", "secretToken": ""},
        }, ensure_ascii=False)},
        "Balas 202 QUEUED dalam <5 detik. Grading berjalan di latar belakang.",
    ),
    "grading-status": (
        "2. Grading — Status (polling)",
        "GradingStatus",
        {"file_id": "uji-b"},
        "Panggil berulang sampai `done: true`. Isi `job_id` untuk job tertentu.",
    ),
    "config-rules": (
        "3. Config — Baca aturan",
        "GradingRuleGet",
        {"grade_id": ""},
        "Kosongkan grade_id untuk semua grade; isi '2' untuk satu grade.",
    ),
    "config-rules-update": (
        "4. Config — Ubah aturan",
        "GradingRuleUpdate",
        {"payload": json.dumps({
            "gradeId": 2,
            "updatedBy": "reno",
            "dryRun": True,
            "criteria": {"minCompleteness": {"tempat_lahir": 0.75},
                         "minNikTrusted": 0.75},
            "score": {"min": 72},
            "matching": {"autoScoreMin": 86.0},
        }, ensure_ascii=False), "dry_run": False},
        "dryRun=true memvalidasi dan menampilkan diff tanpa menulis.",
    ),
    "grading": (
        "5. Grading — Pipeline mentah (penelusuran)",
        "OpenGradingSession",
        {"file_id": "uji-b", "s3_bucket": "bucket-test",
         "parquet_key": "uploads/uji-b/data.parquet",
         "s3_endpoint": "", "enriched_key": ""},
        "SINKRON — memblokir sampai selesai. Untuk menelusuri, bukan untuk backend.",
    ),
}


def kotak(nama, catatan, url, tweaks):
    """
    `tweaks` berbentuk {node_id: {param: nilai}}.

    Bisa memuat LEBIH DARI SATU node, dan itu bukan kelebihan yang jarang
    terpakai: tweak dikunci per node, jadi parameter milik node terakhir tidak
    bisa dititipkan lewat node pertama. `dry_run` pada matching sempat dikirim
    ke OpenMatchingSession padahal pemiliknya PersistResults — tidak ada galat,
    nilainya hanya diabaikan, dan 200 ribu baris tetap tertulis.
    """
    badan = {"output_type": "chat", "input_type": "text", "input_value": "",
             "tweaks": tweaks}
    return {
        "name": nama,
        "request": {
            "method": "POST",
            "header": [
                {"key": "x-api-key", "value": "{{api_key}}"},
                {"key": "Content-Type", "value": "application/json"},
            ],
            "url": {
                "raw": url,
                "host": ["{{base_url}}"],
                "path": url.replace("{{base_url}}/", "").split("?")[0].split("/"),
                "query": [{"key": "stream", "value": "false"}],
            },
            "body": {"mode": "raw",
                     "raw": json.dumps(badan, indent=2, ensure_ascii=False),
                     "options": {"raw": {"language": "json"}}},
            "description": catatan,
        },
    }


def main() -> int:
    token = masuk()
    flows = minta("GET", "/api/v1/flows/", token=token) or []

    # nama komponen -> node id, per flow
    per_endpoint = {}
    for fl in flows:
        nodes = {n["data"]["type"]: n["id"]
                 for n in (fl.get("data") or {}).get("nodes", [])}
        per_endpoint[fl.get("endpoint_name") or fl["id"]] = {
            "nama": fl.get("name"), "id": fl["id"], "nodes": nodes,
            "punya_endpoint": bool(fl.get("endpoint_name")),
        }

    item = []

    # ── Autentikasi: cara memperoleh API key ──────────────────────────────
    item.append({
        "name": "0. Auth",
        "item": [
            {
                "name": "0a. Login (dapat bearer token)",
                "request": {
                    "method": "POST",
                    "header": [{"key": "Content-Type",
                                "value": "application/x-www-form-urlencoded"}],
                    "url": {"raw": "{{base_url}}/api/v1/login",
                            "host": ["{{base_url}}"],
                            "path": ["api", "v1", "login"]},
                    "body": {"mode": "urlencoded", "urlencoded": [
                        {"key": "username", "value": "{{username}}"},
                        {"key": "password", "value": "{{password}}"}]},
                    "description":
                        "Token dari sini HANYA untuk endpoint pengelolaan "
                        "(/api/v1/flows, /api/v1/all, /api/v1/api_key). "
                        "Endpoint /api/v1/run MENOLAKNYA — pakai x-api-key.",
                },
                "event": [{"listen": "test", "script": {"exec": [
                    "var d = pm.response.json();",
                    "if (d.access_token) "
                    "pm.collectionVariables.set('bearer', d.access_token);",
                ], "type": "text/javascript"}}],
            },
            {
                "name": "0b. Buat API key",
                "request": {
                    "method": "POST",
                    "header": [
                        {"key": "Authorization", "value": "Bearer {{bearer}}"},
                        {"key": "Content-Type", "value": "application/json"}],
                    "url": {"raw": "{{base_url}}/api/v1/api_key/",
                            "host": ["{{base_url}}"],
                            "path": ["api", "v1", "api_key", ""]},
                    "body": {"mode": "raw",
                             "raw": json.dumps({"name": "postman"}, indent=2)},
                    "description":
                        "Field `api_key` pada balasan HANYA muncul sekali. "
                        "Simpan; tidak bisa dilihat lagi.",
                },
                "event": [{"listen": "test", "script": {"exec": [
                    "var d = pm.response.json();",
                    "if (d.api_key) pm.collectionVariables.set('api_key', d.api_key);",
                ], "type": "text/javascript"}}],
            },
            {
                "name": "0c. Daftar API key",
                "request": {
                    "method": "GET",
                    "header": [{"key": "Authorization", "value": "Bearer {{bearer}}"}],
                    "url": {"raw": "{{base_url}}/api/v1/api_key/",
                            "host": ["{{base_url}}"],
                            "path": ["api", "v1", "api_key", ""]},
                    "description": "Nilai key-nya tidak ikut ditampilkan, hanya namanya.",
                },
            },
        ],
    })

    # ── Endpoint service ──────────────────────────────────────────────────
    hilang = []
    for endpoint, (nama, komponen, tweaks, catatan) in PERMINTAAN.items():
        info = per_endpoint.get(endpoint)
        if not info:
            hilang.append(endpoint)
            continue
        nid = info["nodes"].get(komponen)
        if not nid:
            hilang.append(f"{endpoint} (node {komponen})")
            continue
        url = f"{{{{base_url}}}}/api/v1/run/{endpoint}?stream=false"
        item.append(kotak(nama, f"{catatan}\n\nflow: {info['nama']}\n"
                                f"node id: {nid}", url, {nid: tweaks}))

    # ── Matching: satu-satunya yang belum punya endpoint_name ─────────────
    m = next((v for k, v in per_endpoint.items()
              if v["nama"] == "Synchrono Matching"), None)
    if m:
        nid = m["nodes"].get("OpenMatchingSession")
        nid_simpan = m["nodes"].get("PersistResults")
        url = f"{{{{base_url}}}}/api/v1/run/{m['id']}?stream=false"
        item.append(kotak(
            "6. Matching — Jalankan (SINKRON)",
            "PERHATIAN: flow ini belum punya endpoint_name, jadi dipanggil "
            "dengan UUID. Node id-nya juga ACAK — berubah tiap kali flow "
            "dibangun ulang, tidak seperti flow grading dan config. "
            "Bangkitkan ulang koleksi ini sesudah menjalankan buat_flow.py.\n\n"
            "Contoh ini memakai dry_run=true sehingga TIDAK menulis apa pun. "
            "Setel false untuk menyimpan hasil — penulisannya upsert, jadi "
            "aman diulang.\n\n"
            f"flow: {m['nama']}\nflow_id: {m['id']}\n"
            f"node masuk : {nid}\nnode simpan: {nid_simpan}",
            url,
            # DUA node. `dry_run` milik PersistResults dan harus dikirim ke
            # node itu — dititipkan lewat node pertama, ia diabaikan diam-diam
            # dan 200 ribu baris tetap tertulis.
            {
                nid: {"file_id": "d88150c5",
                      "parquet_path": "s3://synchrono/curated/"
                                      "20260908_100245_d88150c5_data_dukcapil_gradeA.parquet",
                      "grade": 1, "pakai_enriched": True},
                nid_simpan: {"dry_run": True},
            }))

    koleksi = {
        "info": {
            "name": "Synchrono — Langflow Services",
            "description":
                "Grading, konfigurasi aturan, dan matching.\n\n"
                "Dibangkitkan oleh infra/buat_postman.py dari kondisi Langflow "
                "yang sedang berjalan — node id di dalam `tweaks` diambil "
                "langsung dari flow, bukan ditulis tangan.\n\n"
                "Isi variabel `api_key` dulu, atau jalankan folder 0. Auth "
                "yang akan mengisinya otomatis.",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": item,
        "variable": [
            {"key": "base_url", "value": BASE},
            {"key": "username", "value": "admin"},
            {"key": "password", "value": "synchrono123"},
            {"key": "bearer", "value": ""},
            {"key": "api_key", "value": ""},
        ],
    }

    KELUARAN.write_text(json.dumps(koleksi, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"{len(item)} entri -> {KELUARAN}")
    for it in item:
        if "item" in it:
            print(f"   {it['name']}  ({len(it['item'])} permintaan)")
        else:
            print(f"   {it['name']}")
    if hilang:
        print(f"\n!! tidak ditemukan di Langflow: {hilang}")
        print("   Jalankan buat_flow_grading.py / buat_flow_config.py dulu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
