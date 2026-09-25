# Panduan Arsitektur Modular: Penggantian Input Data & API Serving Layer

Dokumen ini adalah **panduan cetak biru teknis (*technical blueprint*)** bagi pengembang maupun AI Agent di masa mendatang untuk melakukan:
1. **Penggantian Awal (Sumber Data Master):** Beralih dengan mudah antara **Parquet 100 Juta Baris**, **Tabel PostgreSQL (`pg.public.master`)**, atau **Cloud/Object Storage (S3/MinIO)**.
2. **Penggantian Akhir (Serving Layer / Pintu API):** Beralih antara **Langflow Flow** (Visual Workflow & Canvas) atau **Standalone FastAPI** (Mikroservis Ringan Berkinerja Tinggi).
3. **Preservasi Core Engine:** Menjamin logika inti pencocokan pola, sanitasi, caching, dan inferensi LLM `gemma3:12b` di [`lib/_reasoning.py`](file:///home/mario_siregar_isgs/kerjaan/langflow-synchrono/lib/_reasoning.py) **tetap utuh 100% tanpa perlu dirombak ulang**.

---

## 1. DESAIN ARSITEKTUR HEXAGONAL (PORTS & ADAPTERS)

Sistem AI Reasoning Synchrono dibangun dengan prinsip *Separation of Concerns* di mana logika bisnis murni berada di dalam inti (*Core*), sedangkan input data dan pintu masuk API hanyalah *adapter* yang bisa diganti-ganti (*pluggable*):

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              ARSITEKTUR MODULAR AI REASONING SYNCHRONO                                │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘

    [PINTU MASUK / SERVING LAYER]             [CORE REASONING ENGINE]            [SUMBER DATA MASTER]
    (Bisa Dipilih / Diganti)                  (Terisolasi & Mandiri)           (Bisa Dipilih / Diganti)

 ┌──────────────────────────────┐          ┌───────────────────────────┐         ┌─────────────────────────┐
 │ Opsi 1: LANGFLOW (Saat Ini)  │          │                           │         │ Opsi A: 100M PARQUET    │
 │ • Flow Visual Kanvas         ├─────────►│     lib/_reasoning.py     ├────────►│ • DuckDB Semi-Join      │
 │ • POST /api/v1/run/...       │          │                           │         │ • Local disk / S3       │
 └──────────────────────────────┘          │ • SQL Verdict Generation  │         └─────────────────────────┘
                                           │ • Pattern Signature Hash  │
 ┌──────────────────────────────┐          │ • Local Gemma 3:12B LLM   │         ┌─────────────────────────┐
 │ Opsi 2: FASTAPI (Mikroservis)│          │ • SQL Bulk Hydration      │         │ Opsi B: POSTGRESQL DB   │
 │ • Uvicorn Python Standalone  ├─────────►│ • Fallback Deterministik  ├────────►│ • pg.public.master     │
 │ • POST /v1/reasoning/...     │          │                           │         │ • Native SQL Join       │
 └──────────────────────────────┘          │   lib/_reasoning_jobs.py  │         └─────────────────────────┘
                                           │                           │
 ┌──────────────────────────────┐          │ • Asynchronous Worker     │         ┌─────────────────────────┐
 │ Opsi 3: CLI / BATCH SCRIPT   │          │ • Job Lifecycle & States  │         │ Opsi C: OBJECT STORE    │
 │ • run_reasoning_local.py     ├─────────►│ • Stale Job Harvester     ├────────►│ • S3 / MinIO / Seaweed  │
 └──────────────────────────────┘          └───────────────────────────┘         └─────────────────────────┘
```

---

## 2. PENGGANTIAN AWAL: SUMBER DATA MASTER (SOURCE ADAPTER)

Modul [`lib/_reasoning.py`](file:///home/mario_siregar_isgs/kerjaan/langflow-synchrono/lib/_reasoning.py) sudah memiliki logika pencabangan bawaan (*built-in dual-mode*) pada fungsi `build_verdict_table()`. Anda tidak perlu mengubah baris kode apapun, cukup atur konfigurasinya:

### Skenario A: Menggunakan Master Parquet (Default Saat Ini)
* **Kapan digunakan:** Dataset master sangat masif (puluhan hingga ratusan juta baris, misal file 3.01 GB `uji-master-100juta.parquet`).
* **Cara Mengaktifkan:**
  1. Setel Environment Variable di `.env`:
     ```ini
     MASTER_PARQUET_PATH=/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet
     ```
  2. Atau kirimkan pada parameter payload request API:
     ```json
     {
       "masterParquetPath": "/path/to/master.parquet"
     }
     ```
* **Mekanisme di Core:** DuckDB mengeksekusi *Two-Phase Semi-Join Pushdown* langsung ke file Parquet tanpa me-load seluruh baris ke memori.

---

### Skenario B: Menggunakan Tabel PostgreSQL Master (`pg.public.master`)
* **Kapan digunakan:** Data kependudukan tersimpan terpusat di tabel relasional PostgreSQL `master`, atau ukuran data master di bawah 10–20 juta baris.
* **Cara Mengaktifkan:**
  1. Kosongkan nilai `MASTER_PARQUET_PATH` di `.env`:
     ```ini
     MASTER_PARQUET_PATH=
     ```
  2. Atau jangan kirim `masterParquetPath` (kirim `null` / abaikan) di payload request.
* **Mekanisme di Core:** Sistem otomatis mendeteksi bahwa jalur Parquet kosong, lalu mengalihkan query ke tabel PostgreSQL relasional:
  ```sql
  -- Potongan query otomatis di lib/_reasoning.py (lines 228-250)
  SELECT mm.*, m.nama_lengkap, m.tempat_lahir, m.tanggal_lahir, m.jenis_kelamin, m.nama_ibu
  FROM pg.public.manual_matches mm
  JOIN pg.public.institution i USING (file_id, id_incoming)
  LEFT JOIN pg.public.master m ON i.nik_master = m.nik
  WHERE mm.file_id = :file_id AND mm.reasoning_status IN ('PENDING', 'FAILED');
  ```

---

### Skenario C: Menggunakan Cloud / Object Storage (S3 / MinIO / SeaweedFS)
* **Kapan digunakan:** File Parquet master disimpan di bucket S3 atau MinIO on-premise.
* **Cara Mengaktifkan:**
  1. Pasang kredensial S3 di `.env`:
     ```ini
     S3_ENDPOINT=http://synchrono-seaweedfs:8333
     S3_ACCESS_KEY=any_access_key
     S3_SECRET_KEY=any_secret_key
     MASTER_PARQUET_PATH=s3://master-bucket/master-100m.parquet
     ```
  2. DuckDB secara *native* membaca jalur `s3://` melalui ekstensi `httpfs` tanpa perlu men-download file 3 GB tersebut ke disk lokal terlebih dahulu.

---

## 3. PENGGANTIAN AKHIR: SERVING LAYER (LANGFLOW VS FASTAPI)

Saat ini sistem berjalan di atas **Langflow**. Jika di masa depan tim memutuskan ingin menggunakan **FastAPI murni** (misal untuk menghemat RAM kontainer atau menghilangkan GUI Langflow), Anda dapat memasangnya dalam 5 menit.

### Perbandingan Karakteristik:

| Parameter | Opsi 1: Langflow (Saat Ini) | Opsi 2: Standalone FastAPI (Alternatif) |
| :--- | :--- | :--- |
| **Kelebihan Utama** | Visual Canvas UI, low-code graph, mudah dipantau non-developer | Sangat ringan, konsumsi RAM < 100 MB, throughput HTTP tinggi |
| **Kompleksitas Infra** | Memerlukan kontainer Langflow (~1.2 GB image) | Cukup binary Python standar + Uvicorn |
| **Integrasi Core** | Mengimpor `lib/_reasoning_jobs.py` di Custom Component | Mengimpor `lib/_reasoning_jobs.py` di Router Endpoint |

---

### Implementasi Siap Pakai: Standalone FastAPI Microservice

Jika ingin beralih ke FastAPI, cukup buat file `fastapi_app.py` di root repositori dengan kode berikut:

```python
"""
fastapi_app.py - Standalone FastAPI Server for Synchrono AI Reasoning.
Bypasses Langflow UI while maintaining 100% of the core reasoning engine.

Run command:
    uvicorn fastapi_app:app --host 0.0.0.0 --port 8000 --workers 2
"""

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from lib._kolam import pinjam
from lib._reasoning_jobs import build_job_payload, record_job, get_job, harvest_stale_jobs
from lib._reasoning_worker import dispatch_worker

app = FastAPI(
    title="Synchrono AI Reasoning Microservice",
    description="Standalone lightweight API for AI Reasoning on top of DuckDB & Gemma 3:12B",
    version="2.0.0"
)

class DispatchRequest(BaseModel):
    file_id: str = Field(..., alias="fileId", description="Target file ID in manual_matches")
    master_parquet_path: Optional[str] = Field(None, alias="masterParquetPath")
    llm_model: Optional[str] = Field("gemma3:12b", alias="llmModel")
    dry_run: Optional[bool] = Field(False, alias="dryRun")
    limit: Optional[int] = Field(None)

    class Config:
        populate_by_name = True

@app.get("/health")
def health():
    return {"status": "HEALTHY", "engine": "DuckDB + Gemma 3:12B"}

@app.post("/v1/reasoning/dispatch", status_code=status.HTTP_202_ACCEPTED)
def dispatch_reasoning(req: DispatchRequest):
    payload_data = req.dict(by_alias=True)
    job = build_job_payload(payload_data)

    with pinjam() as con:
        harvest_stale_jobs(con)
        record_job(con, job)

    # Lepas ke background worker thread asinkron
    dispatch_worker(job["job_id"])

    return {
        "status": "QUEUED",
        "jobId": job["job_id"],
        "fileId": job["file_id"],
        "message": "AI Reasoning job successfully queued via FastAPI."
    }

@app.get("/v1/reasoning/status/{file_id}")
def check_status(file_id: str):
    with pinjam() as con:
        job = get_job(con, file_id=file_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reasoning job found for file_id '{file_id}'"
        )

    is_done = job.get("status") in ("COMPLETED", "FAILED")
    return {
        "found": True,
        "status": job["status"],
        "jobId": job["job_id"],
        "fileId": job["file_id"],
        "done": is_done,
        "stage": job.get("stage"),
        "error": job.get("error"),
        "result": job.get("result"),
        "updatedAt": str(job.get("updated_at"))
    }
```

#### Cara Menjalankan FastAPI di Docker Compose:
Tambahkan service berikut di `docker-compose.yml`:
```yaml
  synchrono-fastapi:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - ./:/app
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:synchrono123@pg-synchrono:5432/synchrono
      - REASONING_AI_BASE_URL=https://ollama.mcp-tools.my.id
      - REASONING_AI_MODEL=gemma3:12b
    command: >
      bash -c "pip install fastapi uvicorn duckdb psycopg2-binary pyarrow &&
               uvicorn fastapi_app:app --host 0.0.0.0 --port 8000"
```

---

## 4. KONTRAK TERPROTEKSI CORE ENGINE (DILARANG DIROMBAK)

Agar fleksibilitas di atas tetap terjaga, fungsi-fungsi berikut di dalam folder [`lib/`](file:///home/mario_siregar_isgs/kerjaan/langflow-synchrono/lib/) **bersifat suci (*immutable contract*)**:

1. **`build_verdict_table(con, file_id, master_parquet_path=None)`**
   - Menghasilkan tabel `reasoning_verdict` di DuckDB.
   - Tetap pertahankan argumen `master_parquet_path=None` agar otomatis beralih ke DB relasional jika nilainya `None`.
2. **`resolve_unresolved_patterns(con, model=None, limit=None)`**
   - Mengelompokkan pola perbedaan, melakukan *deduplikasi hash MD5*, dan memanggil LLM hanya untuk pola baru.
3. **`apply_reasoning_templates(con, file_id, ...)`**
   - Melakukan bulk SQL replacement untuk menghidrasi template kalimat ke seluruh baris.
4. **`execute_reasoning(job, dry_run=False, limit=None)`**
   - Titik eksekusi terpadu (*unified runner*) yang dipanggil baik oleh Langflow, FastAPI, maupun skrip CLI.

---

## 5. PLAYBOOK UNTUK AI AGENT MASA DEPAN (QUICK-ACTION STEPS)

Jika di masa depan pengguna meminta:

### "Tolong ganti API-nya ke FastAPI aja, jangan pakai Langflow":
1. Buat file `fastapi_app.py` sesuai template di **Bagian 3** dokumen ini.
2. Tambahkan dependensi `fastapi` dan `uvicorn`.
3. Jalankan `uvicorn fastapi_app:app --host 0.0.0.0 --port 8000`.
4. Endpoint langsung aktif di `POST /v1/reasoning/dispatch` dan `GET /v1/reasoning/status/{file_id}`.
5. **JANGAN sentuh atau ubah kode di `lib/_reasoning.py`**.

### "Tolong ganti pengecekannya lewat database PostgreSQL Master aja, jangan Parquet":
1. Buka `.env` dan setel `MASTER_PARQUET_PATH=`.
2. Pastikan tabel `public.master` di PostgreSQL terisi data.
3. Jalankan pengujian `python3 run_reasoning_local.py --file-id <ID> --live`.
4. Sistem otomatis melakukan JOIN ke `pg.public.master` via NIK.
5. **JANGAN sentuh atau ubah kode di `lib/_reasoning.py`**.
