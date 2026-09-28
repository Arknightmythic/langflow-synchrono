# Synchrono Engine — Matching, Grading & AI Reasoning

Platform pemrosesan data kependudukan skala besar yang **berdiri sendiri**, terpisah dari backend `data-matching` lama. Terdiri dari engine Matching & Grading (Langflow + DuckDB) serta modul **AI Reasoning** mandiri (*FastAPI microservice & embeddable package*).

```
                            ┌────────────────────────────────────────┐
                            │       Backend Synchrono / Klien        │
                            └───────┬────────────────────────┬───────┘
                                    │ HTTP                   │ HTTP
                                    ▼                        ▼
                       ┌────────────────────────┐  ┌────────────────────────┐
                       │ Langflow Matching &    │  │ FastAPI AI Reasoning   │
                       │ Grading Flows          │  │ Service (`reasoning/`) │
                       └────────────┬───────────┘  └────────────┬───────────┘
                                    │                           │ Dispatches Tasks
                                    │                           ▼
                                    │              ┌────────────────────────┐
                                    │              │ Redis / Celery Queue   │
                                    │              └────────────┬───────────┘
                                    │                           ▼
                                    │              ┌────────────────────────┐
                                    │              │ Celery Worker Pool     │
                                    │              │ (Anti-Thundering Herd) │
                                    │              └────────────┬───────────┘
                                    │                           │
                   ┌────────────────┼───────────────────────────┤
                   ▼                ▼                           ▼
            SeaweedFS (S3)     PostgreSQL                    DuckDB
            Parquet incoming   Master, jobs, status locks    Mesin hitung analitik,
            & Master 100M      & reasoning_patterns          join & vectorized SQL
```

**Karakteristik Utama Arsitektur:**
- **Matching & Grading:** Didorong oleh 7 custom node Langflow dan DuckDB. Tidak ada ketergantungan pada Polars, rapidfuzz, maupun pymysql: DuckDB menangani parquet di S3, koneksi PostgreSQL, dan `jaro_winkler_similarity` sekaligus.
- **AI Reasoning (FastAPI & Event-Driven Celery):** Berdiri sendiri di direktori `reasoning/`. Menggunakan *Two-Phase Semi-Join Pushdown* langsung ke master Parquet 100 juta baris di S3, *Pattern Caching & Signature Hashing* (menghemat beban LLM hingga 99%), dan model lokal **Gemma 3:12B** (Ollama).
- **Anti-Thundering-Herd Idempotency:** Penguncian status pola atomik di PostgreSQL (`RESOLVING` -> `COMPLETED`) mencegah duplikasi panggilan LLM saat banyak batch diproses secara serentak.
- **LLM-as-a-Judge Framework:** Evaluasi kualitas otomatis di direktori `evals/` mengukur factuality, format template, dan keringkasan reviewer sesuai standar evaluasi modern.
- **Manajemen Dependensi Modern:** Menggunakan `uv` (`pyproject.toml` dan `uv.lock`) untuk manajemen dependensi yang deterministik, cepat, dan terdokumentasi rapi.

---

## Indeks Dokumentasi Sistem

Seluruh dokumentasi teknis, kontrak API, dan spesifikasi arsitektur terbagi rapi berdasarkan topik:

| Dokumen | Topik & Cakupan Utama |
| :--- | :--- |
| [`reasoning/ARCHITECTURE.md`](reasoning/ARCHITECTURE.md) | **Spesifikasi Arsitektur AI Reasoning (Standar Industri):** Zero-OOM Two-Phase Semi-Join 100M baris, Pattern Signature Hashing, State Machine PostgreSQL, dan Anti-Double-Hit Cache. |
| [`reasoning/README.md`](reasoning/README.md) | **Panduan Pengembang AI Reasoning:** Quickstart FastAPI microservice, playbook integrasi (*embedding/jahit*), Celery worker, dan contoh API cURL. |
| [`evals/rubric.md`](evals/rubric.md) | **Rubrik Evaluasi LLM-as-a-Judge:** Kriteria Factuality Fidelity, Template Consistency, dan Conciseness Readability. |
| [`docs/PARQUET_MATCHING_REASONING.md`](docs/PARQUET_MATCHING_REASONING.md) | Penjelasan alur matching CSV, integrasi master Parquet 100M, dan narasi cerdas AI untuk manual review. |
| [`docs/PLUGGABLE_REASONING_ARCHITECTURE.md`](docs/PLUGGABLE_REASONING_ARCHITECTURE.md) | Cetak Biru Modularitas: Panduan fleksibilitas sumber data (Parquet / PostgreSQL Master / S3) dan decoupling API. |
| [`docs/LARGE_SCALE_EVENT_DRIVEN_REASONING.md`](docs/LARGE_SCALE_EVENT_DRIVEN_REASONING.md) | Panduan stress-testing data jutaan baris & arsitektur event-driven paralel anti double-hit LLM. |
| [`docs/API.md`](docs/API.md) | Daftar endpoint REST API Langflow, format payload, otentikasi API key, dan koleksi Postman. |
| [`docs/GRADING.md`](docs/GRADING.md) | Layanan grading kualitas data asinkron (5 lapis penilaian aturan). |
| [`docs/NORMALISASI.md`](docs/NORMALISASI.md) | Normalisasi kolom dan format tanggal bercampur menggunakan DuckDB/AI. |
| [`docs/KONFIGURASI.md`](docs/KONFIGURASI.md) | Panduan konfigurasi ambang batas grade melalui API dinamis. |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Panduan instalasi dan deployment kontainer Docker on-premise / server. |
| [`docs/LURING.md`](docs/LURING.md) | Panduan bekerja mandiri secara luring/offline tanpa koneksi VPN kantor. |

---

## Status Verifikasi

Diuji dengan **data produksi asli** (file `825fc484`, grade 4, 200.020 baris), dibandingkan dengan hasil backend lama di StarRocks:

| Kategori | Backend StarRocks | Service ini | Status |
|---|---|---|:---:|
| `AUTO_MATCH` | 115.551 | 115.551 | ✅ |
| `MANUAL_REVIEW` | 24.022 | 24.022 | ✅ |
| `AUTO_UNMATCH` | 60.447 | 60.447 | ✅ |
| **Total** | **200.020** | **200.020** | ✅ |

Waktu: **15,9 detik** (join 2,8 + skor 1,3 + tulis 10,6), diukur dari laptop.
Backend lama butuh ~51 detik di server.

Yang **sudah** terbukti:
- Hasil matching identik dengan produksi (grade 1 & 4)
- `jaro_winkler_similarity` DuckDB identik bit-per-bit dengan rapidfuzz, termasuk rumus grade 5 yang memakai rata-rata wilayah bersyarat (selisih `0.00e+00`)
- Upsert mencegah duplikasi: dijalankan 2x tetap 1,00x (StarRocks langsung 2,00x)
- SeaweedFS baca-tulis parquet, PostgreSQL baca-tulis
- Langflow 1.12.1 di Docker berjalan dan **ketujuh node terdaftar** di kategori `matching`
- **AI Reasoning Unit Test Suite:** 15/15 test lulus 100% (`tests/test_celery_concurrency.py`, `tests/test_fastapi_reasoning.py`, `tests/test_reasoning.py`).
- **Idempotensi 4 Batch Konkuren:** 308 baris Grade B diproses serentak dengan **0 duplikasi panggilan LLM** (hanya 3 panggilan LLM untuk 3 pola unik di seluruh sistem, sisanya 100% cache hit terhidrasi dalam 18.11s).
- **Senior QA Endurance Benchmark:** Cold cache (9.44s) vs Warm cache (3.24s dengan 100% cache hit ratio) serta matriks edge cases anomali tervalidasi.
- **Evaluasi LLM-as-a-Judge:** 100% lulus pada seluruh sampel uji untuk 3 kriteria (Faktual, Format Template, Keterbacaan) dengan kalibrasi Cohen's Kappa = 1.000.

---

## 1. Prasyarat & Manajemen Dependensi (`uv`)

| Komponen | Kegunaan |
|---|---|
| **Python 3.12+** | Runtime utama service dan modul reasoning |
| **`uv` (Astral)** | *Package & Project Manager* modern, mengelola `pyproject.toml` dan `uv.lock` |
| **Docker** | Menjalankan kontainer SeaweedFS (S3) dan Langflow |
| **PostgreSQL** | `localhost:5432`, database `synchrono` |
| **Ollama** | Penyedia model LLM lokal on-premise (`gemma3:12b`) |

### Instalasi & Menjalankan dengan `uv` (Direkomendasikan)
Proyek ini dikelola menggunakan [**`uv`**](https://github.com/astral-sh/uv) untuk instalasi deterministik, isolasi virtual environment, dan eksekusi secepat kilat:

```bash
# 1. Pasang uv (bila belum terpasang):
curl -LsSf https://astral.sh/uv/install.sh | sh          # Linux / WSL / macOS
# atau di Windows:
# powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Sinkronkan seluruh dependency sesuai uv.lock:
uv sync

# 3. Jalankan test suite otomatis (13/13 tests passed):
uv run pytest tests/ -v

# 4. Jalankan AI Reasoning FastAPI microservice:
uv run uvicorn reasoning.app:app --host 0.0.0.0 --port 8000 --workers 2
```

> **Alternatif Pip (Legacy):**  
> File `requirements.txt` juga disediakan untuk server atau lingkungan yang belum menginstal `uv`:
> ```bash
> pip install -r requirements.txt
> ```

---

## 2. Siapkan Infrastruktur

### SeaweedFS

```bash
cd infra
docker compose up -d seaweedfs
```

| Antarmuka | Alamat / Port | Keterangan |
|---|---|---|
| S3 API | `localhost:8333` | Digunakan oleh DuckDB membaca/menulis Parquet |
| Master UI | `localhost:9333` | Dasbor status SeaweedFS |
| Filer | `localhost:8888` | File browser |
| Kredensial | `synchrono` / `synchrono123` | Lihat `infra/s3-config.json` |

### Skema PostgreSQL

Masih dari folder `infra/`:

```bash
python migrate.py                 # Bentuk basis data: 13 tabel
python seed.py                    # Data awal: ref_*, grade_rules, matching_queries
```

Membuat 13 tabel dan mengisi konfigurasinya. Hanya butuh `duckdb` — DDL dijalankan lewat `postgres_execute()`.

### Isi Tabel Master & Sinkronisasi Parquet

```bash
python migrate_master.py muat     # Muat data master ke PostgreSQL / Parquet
python salin_dari_minio.py curated/20260908_100247_825fc484_data_dukcapil_gradeD.parquet
```

---

## 3. Modul AI Reasoning (FastAPI Microservice & Embeddable Package)

Modul AI Reasoning berada di direktori mandiri [`reasoning/`](reasoning/). Modul ini memberikan narasi cerdas berbasis LLM (**Gemma 3:12B**) untuk baris `MANUAL_REVIEW` tanpa membebani memori dan tanpa pemanggilan LLM yang redundan.

### A. Cara Menjalankan Sebagai Standalone Microservice

Jalankan server REST API menggunakan `uv`:

```bash
# Menjalankan server AI Reasoning mandiri di port 8000:
uv run uvicorn reasoning.app:app --host 0.0.0.0 --port 8000 --workers 2
```
Buka Swagger UI di: `http://localhost:8000/docs`

### B. Cara "Menjahit" (*Embed*) ke Backend FastAPI Lain

Jika Anda memiliki backend FastAPI yang sudah ada (misalnya monolith backend Synchrono):

```python
from fastapi import FastAPI
from reasoning import reasoning_router

app = FastAPI(title="Synchrono Unified Backend")

# Cukup 1 baris untuk menjahit seluruh kapabilitas reasoning:
app.include_router(reasoning_router)
```

### C. Ringkasan Endpoint AI Reasoning

| Method | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `POST` | `/v1/reasoning/trigger/{file_id:path}` | Trigger job asinkron via URL path (mendukung slash seperti `uploads/2026/09/dukcapil.csv`). Mengembalikan `202 Accepted`. |
| `POST` | `/v1/reasoning/dispatch` | Trigger job asinkron via JSON request body standar. |
| `GET` | `/v1/reasoning/status/{file_id:path}` | Polling status job, stage pengerjaan, metrik cache hits, dan durasi. |
| `POST` | `/v1/reasoning/clear-cache` | Menghapus seluruh pola di `reasoning_patterns` untuk pengujian ulang. |
| `POST` | `/v1/reasoning/run-sync/{file_id:path}` | Eksekusi reasoning secara langsung (blocking/synchronous). |
| `GET` | `/v1/reasoning/health` | Health check koneksi PostgreSQL dan konfigurasi S3/LLM. |

*Contoh cURL Trigger:*
```bash
curl -X POST "http://localhost:8000/v1/reasoning/trigger/uploads/2026/09/batch_01.csv"
```

*Contoh cURL Cek Status:*
```bash
curl "http://localhost:8000/v1/reasoning/status/uploads/2026/09/batch_01.csv"
```

### D. Menjalankan Event-Driven Celery Worker (Redis Broker)

Untuk lingkungan produksi dengan volume konkurensi tinggi, jalankan worker Celery:

```bash
# Menjalankan worker Celery (concurrency 4):
uv run celery -A reasoning.celery_app worker --loglevel=info --concurrency=4
```
*Sistem dilengkapi mekanisme failover otomatis: jika Celery broker tidak tersedia, API akan otomatis melakukan fallback ke thread latar belakang in-process.*

### E. Menjalankan Evaluasi LLM-as-a-Judge (`evals/`)

Framework evaluasi otomatis untuk memverifikasi kualitas narasi AI reasoning berdasarkan rubrik operasional:

```bash
# 1. Jalankan harness evaluasi terhadap sampel data:
uv run python evals/run_eval.py --limit 20

# 2. Kalibrasi skor hakim terhadap ground-truth manusia (Cohen's Kappa):
uv run python evals/calibrate.py --human evals/sample_human_labels.csv --judge evals/results.json
```

### F. Rangkaian Uji Senior QA & Ketahanan Konkurensi

```bash
# 1. Uji ketahanan konkurensi 4 batch simultan (verifikasi anti-thundering-herd):
uv run python scripts/test_concurrent_4_batches.py

# 2. Uji ketahanan multi-grade, cold vs warm cache, dan edge cases:
uv run python scripts/senior_qa_endurance_test.py

# 3. Jalankan seluruh unit test suite:
uv run pytest tests/ -v
```

> 💡 **Rincian lengkap:** Baca [Spesifikasi Arsitektur AI Reasoning](reasoning/ARCHITECTURE.md), [Rubrik LLM-Judge](evals/rubric.md), dan [Panduan Pengembang Reasoning](reasoning/README.md).

---

## 4. Pasang Matching di Langflow

### Menjalankan Kontainer Langflow

```bash
cd infra
docker compose up -d --build langflow
```

Buka `http://localhost:7860`, login dengan `admin` / `synchrono123`. Node matching tersedia di sidebar grup **matching**.

### Rangkai Flow Matching

Alurnya linear — tiap node menerima `Session` DuckDB dan meneruskannya:

```
[1 Open Session] ──session──► [2 Prepare Incoming] ──incoming_ready──►
[3 Prepare Master] ──master_ready──► [4 Load Config] ──config_ready──►
[5 Run Join] ──joined──► [6 Score & Classify] ──scored──►
[7 Persist Results] ──summary──► ringkasan JSON
```

Port I/O Tiap Node:
| Node | Input | Output |
|---|---|---|
| 1 `OpenMatchingSession` | `file_id`, `parquet_path`, `grade` | `session` |
| 2 `PrepareIncoming` | `session` | `incoming_ready` |
| 3 `PrepareMaster` | `session` | `master_ready` |
| 4 `LoadMatchingConfig` | `session` | `config_ready` |
| 5 `RunMatchingJoin` | `session` | `joined` |
| 6 `ScoreAndClassify` | `session` | `scored` |
| 7 `PersistResults` | `session` | `summary` |

---

## 5. Panggil Matching lewat API

```powershell
# 1. Login memperoleh token
$login = Invoke-RestMethod -Uri "http://localhost:7860/api/v1/login" -Method Post `
    -Body @{ username = "admin"; password = "synchrono123" } `
    -ContentType "application/x-www-form-urlencoded"
$hdr = @{ Authorization = "Bearer $($login.access_token)" }

# 2. Jalankan flow matching
$payload = @{
    tweaks = @{
        OpenMatchingSession = @{
            file_id      = "825fc484"
            parquet_path = "s3://synchrono/curated/xxx.parquet"
            grade        = 4
        }
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://localhost:7860/api/v1/run/<FLOW_ID>?stream=false" `
    -Method Post -Headers $hdr -Body $payload -ContentType "application/json"
```

Response JSON:
```json
{
  "message": "Grade 4 matching completed",
  "file_id": "825fc484",
  "grade": 4,
  "processed_rows": 200020,
  "matched_rows": 115551,
  "manual_review_rows": 24022,
  "unmatched_rows": 60447,
  "sync_status": 1
}
```

---

## 6. Daftar Node Matching

| # | Node | Tanggung Jawab Utama |
|---|---|---|
| 1 | `OpenMatchingSession` | Buka koneksi DuckDB, pasang ekstensi `httpfs` & `postgres`, hubungkan SeaweedFS + PostgreSQL. |
| 2 | `PrepareIncoming` | Buat view `incoming_df` atas Parquet incoming di S3 + normalisasi kolom & teks. |
| 3 | `PrepareMaster` | Buat view `master_df` atas tabel master PostgreSQL / Parquet + normalisasi. |
| 4 | `LoadMatchingConfig` | Ambil query matching dan aturan ambang skor grade dari basis data. |
| 5 | `RunMatchingJoin` | Eksekusi SQL blocking join per grade. |
| 6 | `ScoreAndClassify` | Hitung Jaro-Winkler berbobot dan klasifikasikan `AUTO_MATCH`, `MANUAL_REVIEW`, `AUTO_UNMATCH`. |
| 7 | `PersistResults` | Upsert hasil ke tabel `institution` dan `manual_matches`. |

---

## 7. Keunggulan dari Sistem Lama

1. **Duplikasi Hilang di Level Skema:** Penulisan menggunakan `PRIMARY KEY (file_id, id_incoming)` dan klausa `ON CONFLICT DO UPDATE`. Dijalankan berulang kali, hasilnya deterministik dan stabil 1.00× (StarRocks menggelembung 2×).
2. **Kolom Jebakan Dihilangkan:** Kolom `nik_incoming` dan `area_incoming` yang mayoritas NULL dibuang dari skema baru untuk mencegah misleading query.
3. **Master Tidak Ditarik ke Memori:** Master 100 juta baris diakses menggunakan predicate pushdown dan parquet row-group skipping, memangkas kebutuhan RAM dari ratusan GB menjadi < 200 MB.
4. **AI Reasoning Skala Besar:** Narasi penjelasan review tidak lagi memanggil LLM per baris, melainkan menggunakan pengenalan pola diskrit (*signature hashing*) yang memangkas latensi hingga 99%.
5. **Modern Dependency Management:** Dilengkapi `pyproject.toml` dan `uv.lock` untuk reproduktibilitas lingkungan komputasi 100%.

---

## 8. Struktur Direktori Proyek

```
langflow-synchrono/
├── pyproject.toml                # Definisi proyek & dependensi uv (PEP 621)
├── uv.lock                       # Lockfile deterministik dependensi uv
├── requirements.txt              # Ekspor dependensi standar pip
├── .python-version               # Versi Python yang ditentukan (3.12)
├── README.md                     # Dokumentasi arsitektur utama (file ini)
├── run_local.py                  # Skrip runner 7 node matching lokal (dry run / write)
├── services.sh                   # Manajemen lifecycle service (start/stop/restart)
├── reasoning/                    # MODUL AI REASONING (FastAPI & Modular Core)
│   ├── __init__.py               # Package facade (ekspor app, router, core)
│   ├── app.py                    # Standalone FastAPI service + CORS + harvester
│   ├── router.py                 # Endpoint /trigger/{file_id:path}, /dispatch, /status
│   ├── schemas.py                # Schema validasi Pydantic v2
│   ├── core.py                   # Engine DuckDB Pushdown 100M + Pattern Hashing + Ollama
│   ├── jobs.py                   # State machine antrean job PostgreSQL
│   ├── worker.py                 # Thread pool background dispatcher
│   ├── db.py                     # DuckDB connection pool & PostgreSQL connector
│   ├── config.py                 # Konfigurasi environment dinamis (.env)
│   ├── README.md                 # Panduan pengembang reasoning
│   └── ARCHITECTURE.md           # Spesifikasi arsitektur standar industri
├── components/                   # Custom node Langflow (dimount ke /components)
│   ├── matching/                 # Node pipeline matching (n1_open_session … n7_persist)
│   ├── grading/                  # Node pipeline grading (G1 … G6 + API dispatch/status)
│   └── config/                   # Node pembacaan & modifikasi aturan grade
├── lib/                          # Pustaka internal (dimount ke /synchrono/lib)
│   ├── _shared.py                # Helper koneksi & rumus skor Jaro-Winkler
│   ├── _kolam.py                 # Pool koneksi DuckDB bersama
│   ├── _grading.py               # Pipeline grading data
│   ├── _normalisasi.py           # Normalisasi multi-lapis
│   ├── _jobs.py                  # State machine antrean grading
│   ├── _worker.py                # Worker latar grading
│   └── _reasoning*.py            # Backward-compatibility proxies ke reasoning/
├── infra/                        # Konfigurasi container, migrasi, dan seeder
│   ├── docker-compose.yml        # Docker compose lokal (SeaweedFS + Langflow)
│   ├── docker-compose.server.yml # Compose server produksi
│   ├── Dockerfile.langflow       # Build image Langflow + duckdb
│   ├── db/migrasi/*.sql          # Berkas migrasi DDL PostgreSQL
│   ├── db/seeder/*               # Berkas data awal (seed)
│   ├── migrate.py                # Runner migrasi basis data
│   └── seed.py                   # Runner seeder data awal
└── tests/                        # Test suite otomatis (pytest)
    ├── test_fastapi_reasoning.py # 5 pengujian integrasi FastAPI & embedding router
    └── test_reasoning.py         # 8 pengujian core reasoning, hashing & 100M parquet
```
