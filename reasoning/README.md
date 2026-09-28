# Synchrono AI Reasoning Service (FastAPI & Modular Core)

Modul **AI Reasoning** mandiri (*standalone microservice*) yang dirancang modular agar dapat dijalankan secara independen maupun **"dijahit" (*embedded*)** ke dalam aplikasi FastAPI lain (seperti `data-matching`, monolith backend Synchrono, atau worker batch) tanpa ketergantungan pada Langflow.

> 📖 **Dokumentasi Lengkap Standar Industri & Spesifikasi Arsitektur:**  
> Untuk rincian mendalam mengenai Two-Phase Semi-Join Pushdown 100M baris, Signature Hashing, State Machine, dan Anti-Double-Hit Cache, silakan baca: **[`reasoning/ARCHITECTURE.md`](ARCHITECTURE.md)**.

---

## 1. Fitur Utama

- **DuckDB-Native Pushdown (100M Parquet di S3):** Melakukan pencocokan 5-kolom (*nama, tempat lahir, tanggal lahir, jenis kelamin, nama ibu*) langsung ke data master 100 juta baris di Parquet (S3/SeaweedFS atau PostgreSQL `master`) dengan penggunaan memori RAM konstan < 200 MB.
- **Pattern Caching & Signature Hashing:** Mengelompokkan variasi selisih data ke *signature* unik berbasis SHA-256. Pemanggilan LLM on-premise (**Gemma 3:12B**) hanya terjadi 1x per pola baru; ribuan baris data lainnya otomatis mengisi template via SQL vectorized string replacement (efisiensi token & komputasi hingga 99%).
- **FastAPI Endpoints Fleksibel:** Mendukung pemicu asinkron (*background worker* 202 Accepted), pemantauan status real-time, pembersihan cache, serta eksekusi langsung via path URL yang mendukung karakter slash (`/trigger/{file_id:path}`).
- **Zero Hardcoded Paths:** Seluruh koneksi database, endpoint S3/SeaweedFS, dan model AI dikendalikan secara dinamis melalui file `.env`.
- **Dual Operational Modes:** Dapat berjalan sebagai microservice API mandiri via Uvicorn, atau diimpor langsung sebagai sub-router ke backend FastAPI lain (`app.include_router(reasoning_router)`).

---

## 2. Struktur Modul `reasoning/`

```
reasoning/
├── __init__.py         # Package facade (ekspor app, router, execute_reasoning, dll.)
├── app.py              # Aplikasi FastAPI mandiri + CORS + lifespan stale harvester
├── router.py           # HTTP APIRouter (/trigger/{file_id:path}, /dispatch, /status, /clear-cache)
├── schemas.py          # Schema data Pydantic v2 untuk kontrak request & response
├── core.py             # Engine DuckDB: Two-Phase Join, profiling selisih, LLM Ollama, template hydration
├── jobs.py             # Manajemen state machine di PostgreSQL (reasoning_jobs, reasoning_patterns)
├── worker.py           # Background thread dispatcher dengan batasan semaphore konkurensi
├── db.py               # Pool koneksi DuckDB terisolasi dengan konektor PostgreSQL & S3
├── config.py           # Konfigurasi environment dinamis (.env) dengan auto-resolver endpoint S3
├── README.md           # Panduan ringkas pengembang (file ini)
└── ARCHITECTURE.md     # Spesifikasi arsitektur teknis lengkap & standar industri
```

---

## 3. Cara Menjalankan Secara Mandiri (Standalone Microservice)

### Menjalankan Server API:
```bash
# Menggunakan Uvicorn langsung:
uvicorn reasoning.app:app --host 0.0.0.0 --port 8000 --workers 2

# Atau menggunakan virtualenv proyek:
./.venv/bin/uvicorn reasoning.app:app --host 0.0.0.0 --port 8000
```

Setelah server aktif, dokumentasi interaktif Swagger UI dapat diakses di:
`http://localhost:8000/docs`

---

## 4. Cara "Menjahit" (*Embed / Include*) ke Aplikasi Lain

Jika Anda memiliki backend FastAPI yang sudah ada (misalnya `main.py` di service lain):

```python
from fastapi import FastAPI
from reasoning import reasoning_router

app = FastAPI(title="Main Application Backend")

# Jahit router reasoning ke dalam aplikasi Anda:
app.include_router(reasoning_router)

# Atau dengan custom prefix:
# app.include_router(reasoning_router, prefix="/api")
```

Setelah di-include, semua endpoint reasoning (`/v1/reasoning/*`) otomatis aktif dan terintegrasi di port server aplikasi utama Anda.

---

## 5. Ringkasan Endpoint API

| Method | Path | Deskripsi |
| :--- | :--- | :--- |
| `POST` | `/v1/reasoning/trigger/{file_id:path}` | Trigger job asinkron via path URL (mendukung slash seperti `uploads/2026/09/data.csv`). |
| `POST` | `/v1/reasoning/dispatch` | Trigger job asinkron via JSON request body standar. |
| `GET` | `/v1/reasoning/status/{file_id:path}` | Cek status pengerjaan job, metrik baris, cache hit, dan durasi. |
| `POST` | `/v1/reasoning/clear-cache` | Kosongkan tabel cache pola `reasoning_patterns` di PostgreSQL. |
| `POST` | `/v1/reasoning/run-sync/{file_id:path}` | Eksekusi reasoning secara sinkron/blocking (cocok untuk debugging & test). |
| `GET` | `/v1/reasoning/health` | Health check koneksi PostgreSQL dan konfigurasi S3/LLM. |

---

## 6. Contoh Pemanggilan (cURL)

### A. Memicu Job dengan Path Slash
```bash
curl -X POST "http://localhost:8000/v1/reasoning/trigger/uploads/2026/09/batch_01.csv?clearCache=false"
```
*Response (202 Accepted):*
```json
{
  "status": "QUEUED",
  "jobId": "reasoning-20260928-a1b2c3d4",
  "fileId": "uploads/2026/09/batch_01.csv",
  "message": "AI Reasoning job successfully triggered for file 'uploads/2026/09/batch_01.csv'."
}
```

### B. Cek Status Job
```bash
curl "http://localhost:8000/v1/reasoning/status/uploads/2026/09/batch_01.csv"
```
*Response (200 OK):*
```json
{
  "found": true,
  "status": "COMPLETED",
  "jobId": "reasoning-20260928-a1b2c3d4",
  "fileId": "uploads/2026/09/batch_01.csv",
  "done": true,
  "stage": "COMPLETED",
  "error": null,
  "result": {
    "file_id": "uploads/2026/09/batch_01.csv",
    "status": "COMPLETED",
    "total_rows": 77,
    "patterns_generated": 3,
    "cache_hits": 74,
    "llm_hits": 3,
    "duration_seconds": 9.92
  }
}
```

---

## 7. Pengujian (Testing)

Jalankan test suite menggunakan pytest di dalam virtualenv:
```bash
./.venv/bin/pytest tests/test_fastapi_reasoning.py tests/test_reasoning.py -v
```
Seluruh 13 skenario uji (FastAPI endpoints, slash-path handling, caching pattern deduplication, fallback generator, dan live Parquet 100M pushdown) diverifikasi lolos 100%.
