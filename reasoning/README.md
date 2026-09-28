# Synchrono AI Reasoning Service (FastAPI, Celery & Modular Core)

Modul **AI Reasoning** mandiri (*standalone microservice*) yang dirancang modular agar dapat dijalankan secara independen maupun **"dijahit" (*embedded*)** ke dalam aplikasi FastAPI lain (seperti `data-matching`, monolith backend Synchrono, atau worker batch) tanpa ketergantungan pada Langflow.

> 📖 **Dokumentasi Lengkap Standar Industri & Spesifikasi Arsitektur:**  
> Untuk rincian mendalam mengenai Two-Phase Semi-Join Pushdown 100M/300M baris, Signature Hashing, State Machine, dan Anti-Thundering-Herd Lock, silakan baca: **[`reasoning/ARCHITECTURE.md`](ARCHITECTURE.md)** serta **[`docs/END_TO_END_AI_REASONING.md`](../docs/END_TO_END_AI_REASONING.md)**.

---

## 1. Fitur Utama

- **DuckDB-Native Pushdown (Master 100M s/d 300M Parquet di S3):** Melakukan pencocokan 5-kolom (*nama, tempat lahir, tanggal lahir, jenis kelamin, nama ibu*) langsung ke data master ratusan juta baris di Parquet (S3/SeaweedFS atau PostgreSQL `master`) dengan penggunaan memori RAM konstan < 2 GB (Zero-OOM streaming).
- **Pattern Caching & Signature Hashing:** Mengelompokkan variasi selisih data ke *signature* unik berbasis MD5. Pemanggilan LLM on-premise (**Gemma 3:12B**) hanya terjadi 1x per pola baru; ribuan baris data lainnya otomatis mengisi template via SQL vectorized string replacement (efisiensi komputasi hingga 99.9%).
- **Anti-Thundering-Herd Idempotent Pattern Locking:** Menggunakan PostgreSQL atomic state machine (`RESOLVING` -> `COMPLETED`) dengan *Active Await* (300ms) untuk mencegah duplikasi panggilan LLM saat banyak batch diproses secara simultan.
- **Event-Driven Task Queue (Celery + Redis):** Mendukung antrean tugas asynchronous terdistribusi via Redis broker dengan `prefetch_multiplier=1` dan *late acks*. Dilengkapi mekanisme failover otomatis ke *in-process background thread pool* jika broker tidak tersedia.
- **Modular System Prompt (`reasoning/prompt.py`):** Definisi instruksi LLM terisolasi rapi, transparan, dan mudah dimodifikasi sesuai regulasi kependudukan.
- **FastAPI Endpoints Fleksibel:** Mendukung pemicu asinkron (*background worker* 202 Accepted), pemantauan status real-time, pembersihan cache, serta eksekusi langsung via path URL yang mendukung karakter slash (`/trigger/{file_id:path}`).
- **Manajemen Dependency Modern (`uv`):** Dikelola penuh dengan `uv` (`pyproject.toml` dan `uv.lock`), memastikan instalasi deterministik kilat dan kompatibilitas lintas environment.
- **Dual Operational Modes:** Dapat berjalan sebagai microservice API mandiri via Uvicorn, atau diimpor langsung sebagai sub-router ke backend FastAPI lain (`app.include_router(reasoning_router)`).

---

## 2. Struktur Modul `reasoning/`

```
reasoning/
├── __init__.py         # Package facade (ekspor app, router, execute_reasoning, prompt, dll.)
├── app.py              # Aplikasi FastAPI mandiri + CORS + lifespan stale harvester
├── celery_app.py       # Konfigurasi instance Celery dengan Redis broker & late ack
├── config.py           # Konfigurasi environment dinamis (.env) dengan auto-resolver endpoint S3
├── core.py             # Engine DuckDB: Two-Phase Join, profiling selisih, LLM Ollama, template hydration
├── db.py               # Pool koneksi DuckDB terisolasi dengan konektor PostgreSQL (get_db_connection, get_pg_connection)
├── jobs.py             # Manajemen state machine di PostgreSQL (reasoning_jobs, reasoning_patterns)
├── prompt.py           # Definisi SYSTEM_PROMPT baku untuk evaluasi anomali kependudukan
├── router.py           # HTTP APIRouter (/trigger/{file_id:path}, /dispatch, /status, /clear-cache)
├── schemas.py          # Schema data Pydantic v2 untuk kontrak request & response
├── tasks.py            # Definisi Celery task (process_reasoning_batch)
├── worker.py           # Dispatcher antrean Celery dengan failover in-process thread pool
├── README.md           # Panduan ringkas pengembang (file ini)
└── ARCHITECTURE.md     # Spesifikasi arsitektur teknis lengkap & standar industri
```

---

## 3. Cara Menjalankan Secara Mandiri (Standalone Microservice)

### A. Menjalankan Server API FastAPI:
```bash
# Sinkronisasi environment (otomatis membuat venv & menginstal library):
uv sync

# Menjalankan server API:
uv run uvicorn reasoning.app:app --host 0.0.0.0 --port 8000 --workers 2
```

Setelah server aktif, dokumentasi interaktif Swagger UI dapat diakses di:
`http://localhost:8000/docs`

### B. Menjalankan Worker Celery (Event-Driven Task Queue):
Untuk memproses antrean tugas di latar belakang menggunakan Redis broker:
```bash
uv run celery -A reasoning.celery_app worker --loglevel=info --concurrency=4
```
*Catatan: Jika Celery worker tidak dijalankan atau Redis tidak tersedia, API otomatis melakukan fallback ke thread latar belakang in-process tanpa menghentikan layanan.*

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

## 7. Rangkaian Pengujian (Testing)

Jalankan rangkaian test suite terpadu menggunakan `uv`:

```bash
# 1. Unit Test Suite (15/15 tests passed):
uv run pytest tests/ -v

# 2. Uji Konkurensi 4 Batch Serentak (Anti-Thundering Herd Idempotency):
uv run python scripts/test_concurrent_4_batches.py

# 3. Uji Ketahanan Endurance Senior QA (Cold vs Warm Cache):
uv run python scripts/senior_qa_endurance_test.py

# 4. Evaluasi Otomatis Mutu Narasi LLM-as-a-Judge:
uv run python evals/run_eval.py --limit 20
```
