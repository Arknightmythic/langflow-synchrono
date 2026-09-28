"""
Bangkitkan koleksi Postman untuk synchrono-service.

    python infra/buat_postman.py
    -> infra/postman_synchrono_service.json   (impor ke Postman)

Node id di `tweaks` diambil dari app/alur.py — sumber yang SAMA dengan yang
dipakai service — bukan ditulis tangan. Kunci `tweaks` yang meleset satu huruf
diabaikan diam-diam (perilaku Langflow yang ditiru service ini), jadi menyalin
id secara manual adalah cara paling mudah membuat koleksi yang "jalan tapi
salah".

Tidak butuh service yang sedang jalan; hanya pustaka standar Python.

Variabel koleksi:
    base_url      http://localhost:8000  (server: http://<host>:8000, atau :7860
                  kalau service menggantikan Langflow)
    username / password   akun login (sama dengan admin Langflow)
    api_key       diisi otomatis oleh "0b. Buat API key" — atau isi manual
    bearer, api_key_id, grading_job_id, matching_job_id   diisi otomatis
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DISINI = Path(__file__).resolve().parent
sys.path.insert(0, str(DISINI.parent))

from app.alur import ALUR  # noqa: E402 — hanya pustaka standar di tingkat modul

KELUARAN = DISINI / "postman_synchrono_service.json"


def node(endpoint: str) -> str:
    return ALUR[endpoint].simpul[0].node_id


# ── Skrip Postman ───────────────────────────────────────────────────────────

# Dipasang di setiap permintaan /api/v1/run: membuka selubung Langflow, menguji
# status, dan menaruh isi `text` yang sudah di-parse di konsol & tab Tests.
SKRIP_RUN = [
    "pm.test('HTTP 200', function () { pm.response.to.have.status(200); });",
    "var r = pm.response.json();",
    "if (r.outputs) {",
    "  var hasil = JSON.parse(r.outputs[0].outputs[0].results.message.text);",
    "  console.log(hasil);",
    "  pm.test('isi text: ' + JSON.stringify(hasil).slice(0, 180), function () {});",
    "} else {",
    "  // Galat komponen: `detail` berisi STRING JSON, persis Langflow.",
    "  try { console.log(JSON.parse(r.detail).message); } catch (e) { console.log(r.detail); }",
    "}",
]


def skrip_simpan(variabel: str) -> list[str]:
    """SKRIP_RUN + simpan `jobId` dari isi text ke variabel koleksi."""
    return SKRIP_RUN + [
        "if (r.outputs) {",
        f"  if (hasil.jobId) pm.collectionVariables.set('{variabel}', hasil.jobId);",
        "}",
    ]


def acara(tes: list[str] | None = None, pra: list[str] | None = None) -> list[dict]:
    hasil = []
    if pra:
        hasil.append({"listen": "prerequest", "script": {"exec": pra, "type": "text/javascript"}})
    if tes:
        hasil.append({"listen": "test", "script": {"exec": tes, "type": "text/javascript"}})
    return hasil


def url(jalur: str, query: list[tuple[str, str]] | None = None) -> dict:
    # Garis miring di UJUNG dipertahankan: `/api/v1/api_key/` tanpa itu dibalas
    # 307 (pengalihan), bukan dijalankan — terukur saat koleksi ini diuji.
    # Di format Postman, ujung bergaris miring = elemen kosong terakhir di `path`.
    potong = [p for p in jalur.strip("/").split("/") if p]
    if jalur.endswith("/"):
        potong.append("")
    u = {"raw": "{{base_url}}/" + "/".join(potong), "host": ["{{base_url}}"], "path": potong}
    if query:
        u["query"] = [{"key": k, "value": v} for k, v in query]
        u["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query)
    return u


def json_badan(isi) -> dict:
    return {"mode": "raw", "raw": json.dumps(isi, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}}}


def permintaan(nama, metode, jalur, *, header=None, badan=None, query=None,
               deskripsi="", tes=None, pra=None) -> dict:
    p = {"name": nama, "request": {
        "method": metode,
        "header": [{"key": k, "value": v} for k, v in (header or {}).items()],
        "url": url(jalur, query),
        "description": deskripsi,
    }}
    if badan is not None:
        p["request"]["body"] = badan
    ev = acara(tes, pra)
    if ev:
        p["event"] = ev
    return p


def run(nama, endpoint, tweaks: dict, deskripsi, *, input_type="text",
        tes=None, pra=None, header_tambahan=None) -> dict:
    """Permintaan /api/v1/run berbentuk persis seperti yang dikirim portal."""
    header = {"x-api-key": "{{api_key}}", "Content-Type": "application/json"}
    header.update(header_tambahan or {})
    return permintaan(
        nama, "POST", f"/api/v1/run/{endpoint}",
        query=[("stream", "false")],
        header=header,
        badan=json_badan({"output_type": "chat", "input_type": input_type,
                          "input_value": "", "tweaks": {node(endpoint): tweaks}}),
        deskripsi=deskripsi + f"\n\nNode id `{node(endpoint)}` — sama dengan Langflow.",
        tes=tes or SKRIP_RUN, pra=pra,
    )


# ── Muatan contoh — menunjuk berkas uji yang ADA di SeaweedFS lokal ─────────

def muatan_grading(file_id: str, sumber: str, berkas: str) -> str:
    # Persis bentuk portal: `parquetKey` SAMA dengan `enrichedParquetKey` (nama
    # tujuan), dan berkas masukannya di `rawSourceKey`. Engine mengenali
    # kesamaan itu lalu membaca rawSourceKey (lib/_grading._pilih_sumber).
    return json.dumps({
        "fileId": file_id,
        "filename": berkas,
        "s3Bucket": "bucket-test",
        "s3Endpoint": "",
        "rawSourceKey": sumber,
        "parquetKey": f"uploads/{file_id}/enriched.parquet",
        "enrichedParquetKey": f"uploads/{file_id}/enriched.parquet",
        "institution": {"id": "inst-001", "name": "Kementerian Sosial"},
        "callback": {"url": "", "secretToken": ""},
    }, ensure_ascii=False)


MUATAN_MATCHING = json.dumps({
    "jobId": "{{matching_job_id}}",
    "fileId": "sim-grade-d",
    "masterFileId": "md-master-uji-299k",
    "actor": "admin@dukcapil.go.id",
    "callbackUrl": "http://host.docker.internal:3999/api/internal/matching/callback",
    "s3Bucket": "bucket-test",
    "s3Endpoint": "",
    "incomingFile": {"fileId": "sim-grade-d", "filename": "data.csv",
                     "s3Key": "uploads/sim-grade-d/enriched.parquet", "format": "parquet"},
    "masterDataFile": {"masterFileId": "md-master-uji-299k",
                       "filename": "md-master-uji-299k.parquet",
                       "s3Key": "master-data/md-master-uji-299k.parquet", "format": "parquet"},
    "rulePreset": "FAST",
}, ensure_ascii=False)


# ── Koleksi ─────────────────────────────────────────────────────────────────

def koleksi() -> dict:
    auth = {"name": "0. Auth — cara Langflow", "description": (
        "Jalankan berurutan 0a → 0b. `api_key` tersimpan otomatis ke variabel koleksi. "
        "Alternatif: isi `api_key` manual dengan kunci dari SERVICE_API_KEY "
        "(lokal: synchrono-bench-key)."), "item": [
        permintaan(
            "0a. Login (dapat bearer token)", "POST", "/api/v1/login",
            header={"Content-Type": "application/x-www-form-urlencoded"},
            badan={"mode": "urlencoded", "urlencoded": [
                {"key": "username", "value": "{{username}}"},
                {"key": "password", "value": "{{password}}"}]},
            deskripsi="Form-urlencoded, BUKAN JSON (JSON dibalas 422 — sama dengan Langflow).",
            tes=["pm.test('HTTP 200', function () { pm.response.to.have.status(200); });",
                 "var d = pm.response.json();",
                 "if (d.access_token) pm.collectionVariables.set('bearer', d.access_token);"]),
        permintaan(
            "0b. Buat API key", "POST", "/api/v1/api_key/",
            header={"Authorization": "Bearer {{bearer}}", "Content-Type": "application/json"},
            badan=json_badan({"name": "postman"}),
            deskripsi="Nilai `api_key` HANYA muncul di balasan ini; tersimpan ke variabel koleksi.",
            tes=["pm.test('HTTP 200', function () { pm.response.to.have.status(200); });",
                 "var d = pm.response.json();",
                 "if (d.api_key) pm.collectionVariables.set('api_key', d.api_key);",
                 "if (d.id) pm.collectionVariables.set('api_key_id', d.id);"]),
        permintaan(
            "0c. Daftar API key", "GET", "/api/v1/api_key/",
            header={"Authorization": "Bearer {{bearer}}"},
            deskripsi="Nilai kunci tersamar: 8 karakter pertama + bintang."),
    ]}

    grading = {"name": "1. Grading", "item": [
        run("1a. Dispatch — CSV", "grading-dispatch",
            {"payload": muatan_grading("postman-csv", "uploads/fmt-csv/raw/data.csv", "data.csv")},
            "Balas QUEUED dalam <5 detik; grading berjalan di latar. `jobId` tersimpan ke "
            "`grading_job_id` untuk 1d. `payload` adalah STRING berisi JSON.",
            tes=skrip_simpan("grading_job_id")),
        run("1b. Dispatch — .xlsx", "grading-dispatch",
            {"payload": muatan_grading("postman-xlsx", "uploads/fmt-xlsx/raw/data.xlsx", "data.xlsx")},
            "Jalur A, dibaca langsung.", tes=skrip_simpan("grading_job_id")),
        run("1c. Dispatch — .sql (jalur B, konverter)", "grading-dispatch",
            {"payload": muatan_grading("postman-sql", "uploads/fmt-sql/raw/data.sql", "data.sql")},
            "Dipulihkan di container konverter lebih dulu. `.mdf`/`.dmp` sama bentuknya "
            "(butuh konverter-mssql / konverter-oracle hidup).",
            tes=skrip_simpan("grading_job_id")),
        run("1d. Status — job terakhir (job_id)", "grading-status",
            {"job_id": "{{grading_job_id}}"},
            "Polling sampai `done: true`. `result` = muatan callback yang sama persis."),
        run("1e. Status — per berkas (file_id)", "grading-status",
            {"file_id": "postman-csv"},
            "Job TERBARU untuk berkas itu. Berkas yang belum pernah digrading: found=false."),
        run("1f. Pipeline sinkron (penelusuran, bukan untuk portal)", "grading",
            {"file_id": "uji-b", "s3_bucket": "bucket-test",
             "parquet_key": "uploads/uji-b/data.parquet", "s3_endpoint": "",
             "enriched_key": "uji-kompat/postman-pipeline.parquet"},
            "Memblokir sampai selesai, tanpa menyentuh tabel grading_jobs."),
        run("1g. Contoh galat — format ditolak (.xls)", "grading-dispatch",
            {"payload": muatan_grading("postman-xls", "uploads/fmt-xls/raw/data.xls", "data.xls")},
            "Ditolak SEKETIKA: HTTP 500, `detail` berisi string JSON — persis Langflow."),
    ]}

    config = {"name": "2. Config — aturan grade", "item": [
        run("2a. Baca semua grade", "config-rules", {"grade_id": ""},
            "Kosongkan grade_id untuk semua grade."),
        run("2b. Baca satu grade", "config-rules", {"grade_id": "2"},
            "grade_id berupa TEKS \"2\". Angka 2 (bukan teks) dibuang — sama dengan Langflow."),
        run("2c. Ubah aturan — dryRun (tidak menulis)", "config-rules-update",
            {"payload": json.dumps({
                "gradeId": 2, "updatedBy": "postman", "dryRun": True,
                "criteria": {"minCompleteness": {"tempat_lahir": 0.75}, "minNikTrusted": 0.75},
                "score": {"min": 72}, "matching": {"autoScoreMin": 86.0}}, ensure_ascii=False),
             "dry_run": False},
            "Validasi + daftar perubahan tanpa menulis. Hapus `dryRun` untuk benar-benar menyimpan."),
    ]}

    matching = {"name": "3. Matching + reasoning", "description": (
        "PRASYARAT (spesifikasi §1 langkah 1): portal membuat baris `syncrono_matching_job` "
        "berstatus PENDING dengan id = `matching_job_id` SEBELUM dispatch. Tanpa baris itu "
        "job gagal dan callback FAILED — lihat langflow-synchrono/infra/simulasi_portal/simulasi.py."),
        "item": [
        run("3a. Dispatch matching (bentuk persis spesifikasi §3)", "matching-dispatch",
            {"payload": MUATAN_MATCHING},
            "Balas IN_PROGRESS seketika; hasil tersuntik ke DB portal lalu callback ke "
            "`callbackUrl`. `matching_job_id` dibuat baru oleh skrip pra-permintaan. "
            "Header `x-api-key` DAN `Authorization: Bearer` dikirim bersamaan, seperti spesifikasi.",
            input_type="chat",
            header_tambahan={"Authorization": "Bearer {{api_key}}"},
            pra=["// jobId baru tiap kirim — harus sama dengan baris syncrono_matching_job di DB portal.",
                 "pm.collectionVariables.set('matching_job_id', pm.variables.replaceIn('{{$guid}}'));"],
            tes=skrip_simpan("matching_job_id")),
        run("3b. Contoh galat — field wajib kurang", "matching-dispatch",
            {"payload": json.dumps({"jobId": "postman"})},
            "Semua field yang kurang disebut sekaligus (HTTP 500, seperti Langflow).",
            input_type="chat"),
    ]}

    sehat = {"name": "4. Kesehatan", "item": [
        permintaan("4a. /health_check (bentuk Langflow)", "GET", "/health_check",
                   deskripsi="{status, chat, db} — db = PostgreSQL engine. Tidak sehat -> 500."),
        permintaan("4b. /health (tanpa basis data)", "GET", "/health",
                   deskripsi="Probe container; tidak menyentuh PostgreSQL maupun S3."),
        permintaan("4c. /health/db", "GET", "/health/db",
                   deskripsi="Satu perjalanan ke PostgreSQL, dengan waktunya."),
    ]}

    rest = {"name": "5. REST service (benchmark — BUKAN untuk portal)", "description": (
        "Bentuk lama service pembanding: JSON langsung tanpa selubung, kode status bermakna "
        "(202, 400, 401, 404, 409, 422). Tanpa kunci -> 401 (bukan 403 seperti /api/v1/run)."),
        "item": [
        permintaan("5a. Kirim job grading", "POST", "/api/v1/grading/jobs",
                   header={"x-api-key": "{{api_key}}", "Content-Type": "application/json"},
                   badan=json_badan(json.loads(muatan_grading(
                       "postman-rest", "uploads/fmt-csv/raw/data.csv", "data.csv"))),
                   deskripsi="202 QUEUED. Lewat komponen GradingDispatch yang sama."),
        permintaan("5b. Status per berkas", "GET", "/api/v1/grading/jobs/postman-rest",
                   header={"x-api-key": "{{api_key}}"}),
        permintaan("5c. Baca aturan grade 2", "GET", "/api/v1/config/rules/2",
                   header={"x-api-key": "{{api_key}}"}),
        permintaan("5d. Ubah aturan — dryRun", "PATCH", "/api/v1/config/rules/2",
                   header={"x-api-key": "{{api_key}}", "Content-Type": "application/json"},
                   badan=json_badan({"criteria": {"minCompleteness": {"tempat_lahir": 0.75}},
                                     "score": {"min": 72}, "updatedBy": "postman",
                                     "dryRun": True}),
                   deskripsi="409 kalau validasi menolak."),
    ]}

    # Di folder TERAKHIR, bukan di Auth: Collection Runner menjalankan berurutan,
    # dan kunci yang dihapus di langkah ke-4 membuat semua permintaan sesudahnya 403.
    bersih = {"name": "6. Bersihkan", "item": [
        permintaan(
            "6a. Hapus API key (yang dibuat 0b)", "DELETE", "/api/v1/api_key/{{api_key_id}}",
            header={"Authorization": "Bearer {{bearer}}"},
            deskripsi="Berlaku seketika — kunci yang dihapus langsung ditolak. Setelah ini "
                      "isi ulang `api_key` (0b) sebelum memanggil yang lain."),
    ]}

    return {
        "info": {
            "name": "Synchrono Service — kontrak Langflow",
            "description": (
                "Grading, config, dan matching + reasoning TANPA Langflow, dengan kontrak "
                "Langflow yang sama persis: login -> api_key, lalu POST /api/v1/run/<endpoint> "
                "dengan header x-api-key dan `tweaks` yang dikunci node id.\n\n"
                "Mulai dari 0a → 0b (atau isi `api_key` manual). Balasan /api/v1/run berselubung: "
                "isinya string JSON di outputs[0].outputs[0].results.message.text — skrip Tests "
                "membukanya dan mencetaknya di konsol.\n\n"
                "Dibangkitkan oleh synchrono-service/infra/buat_postman.py; node id diambil dari "
                "app/alur.py."),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000"},
            {"key": "username", "value": "admin"},
            {"key": "password", "value": "synchrono123"},
            {"key": "bearer", "value": ""},
            {"key": "api_key", "value": "synchrono-bench-key"},
            {"key": "api_key_id", "value": ""},
            {"key": "grading_job_id", "value": ""},
            {"key": "matching_job_id", "value": ""},
        ],
        "item": [auth, grading, config, matching, sehat, rest, bersih],
    }


if __name__ == "__main__":
    k = koleksi()
    KELUARAN.write_text(json.dumps(k, ensure_ascii=False, indent=2), encoding="utf-8")
    n = sum(len(f["item"]) for f in k["item"])
    print(f"{KELUARAN}  ({n} permintaan dalam {len(k['item'])} folder)")
    for a in ALUR.values():
        print(f"   {a.endpoint:22s} {a.simpul[0].node_id}")
