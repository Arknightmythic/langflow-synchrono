# synchrono-service-starrocks

Versi kedua synchrono-service: grading dan matching memakai **StarRocks** sebagai
penyimpanan dan mesin join, dengan **alur hybrid**. Kontrak API-nya sama dengan
synchrono-service (REST + kontrak Langflow `/api/v1/run/...`), jadi portal bisa
diarahkan ke sini tanpa mengubah kode.

## Alur

```
Unggah → S3 ─► worker grading (DuckDB, logika sama dengan synchrono-service)
                 ├─ enriched.parquet ke S3 (kontrak lama tetap)
                 └─ Stream Load (gzip) ─► synchrono_kl.records

worker matching:
  Pass 1   join NIK berkas × master                       StarRocks
  Pass 1   cek nama ibu bertentangan (Jaro-Winkler)       DuckDB (hanya pasangan yang ibunya beda)
  Pass 2   kandidat nama + tanggal lahir + nama ibu       StarRocks
           urutan kandidat (NIK dekat, tempat lahir)      DuckDB
  Pass 3   blocking per grade                             StarRocks (dimaterialkan)
           skor Jaro-Winkler, seri, klasifikasi, pola     DuckDB (kandidat ditarik bertahap)
  Hasil    snapshot, signature, reasoning, tabel portal   StarRocks (INSERT … SELECT)
```

Master **dibersihkan sekali** saat dimuat (semua varian pembersihan nama dihitung
lebih dulu), jadi matching tidak lagi membersihkan ratusan juta baris master di
setiap job.

## Empat database (StarRocks: skema = database)

| Database | Isi |
|---|---|
| `synchrono_service` | job grading, aturan (`grade_rules`, `grade_criteria`, `grade_bands`, `matching_queries`), `engine_config`, `config_versions`, `config_history`, `reasoning_patterns`, `service_api_keys`, registri `masters`, tabel kerja matching `w_*` (dihapus setelah job) |
| `synchrono_kl` | `records`: data K/L hasil grading, dipartisi per `file_id` |
| `synchrono_portal` | `matching_jobs`, `matching_results` (bentuk sama dengan tabel portal) |
| `synchrono_master` | `persons` (master, dipartisi per `master_id`), `dictionary` (kamus pengenalan kolom) |

## Menjalankan

```bash
cp .env.example .env            # isi STARROCKS_PASSWORD, S3_*, SERVICE_API_KEY
docker compose up -d --build    # valkey, schema (sekali jalan), api (:7870), worker-grading, worker-matching
```

Panduan server (mode service & mode benchmark terisolasi): [DEPLOY.md](DEPLOY.md).

- Stream Load **harus** lewat port BE (`STARROCKS_STREAM_LOAD_URL`, di cluster ini `:30888`).
  Port FE (`:30830`) mengalihkan ke `127.0.0.1:8040` dan gagal dari luar pod.
- Antrean: Celery + Valkey. `visibility_timeout` 6 jam, `acks_late`,
  `task_reject_on_worker_lost`, prefetch 1. Job yang menunggu ada di Valkey (AOF menyala).

## Memuat master

Master harus dimuat sebelum matching (atau dimuat otomatis dari `masterDataFile.s3Key`
saat job pertama — lama untuk master besar):

```bash
docker compose run --rm api python tools/load_master.py m100 s3://bucket/master/m100.parquet
# atau lewat API:  POST /api/v1/masters/m100/load  {"source": "s3://bucket/master/m100.parquet"}
```

## API

Kontrak sama dengan synchrono-service. Portal bisa diarahkan ke sini tanpa mengubah kode.

| Endpoint | Fungsi |
|---|---|
| `POST /api/v1/login`, `GET/POST /api/v1/api_key/`, `DELETE /api/v1/api_key/{id}` | login & API key ala Langflow (kunci disimpan di `synchrono_service.service_api_keys`) |
| `POST /api/v1/run/{flow}` | enam flow Langflow: `grading-dispatch`, `grading-status`, `grading` (sinkron), `config-rules`, `config-rules-update`, `matching-dispatch` (nama endpoint atau flow_id) |
| `POST /api/v1/grading/jobs`, `GET /api/v1/grading/jobs/{fileId}`, `/by-id/{jobId}` | dispatch & status grading |
| `POST /api/v1/grading/run` | grading sinkron |
| `GET /api/v1/config/rules[/{gradeId}]`, `PATCH /api/v1/config/rules/{gradeId}`, `PATCH /api/v1/config/global`, `GET /api/v1/config/history`, `GET /api/v1/config/versions/{v}` | baca, ubah (dengan validasi & `dryRun`), riwayat, versi konfigurasi |
| `POST /api/v1/matching/jobs`, `GET /api/v1/matching/jobs/{jobId}` | dispatch & status matching |
| `POST /api/v1/masters/{id}/load`, `GET /api/v1/masters/{id}` | muat & status master |
| `GET /health`, `/health/db`, `/health_check` | kesehatan |

`x-api-key` diterima lewat header atau query. Sumbernya `SERVICE_API_KEY` (boleh beberapa,
dipisah koma) atau kunci yang dibuat lewat `/api/v1/api_key/`.

## AI (opsional, sama dengan synchrono-service)

- **Pengenalan kolom (lapis 5)**: `NORMALISASI_AI` (`off` | `nama_saja` | `dengan_sampel`),
  `NORMALISASI_AI_BASE_URL` (tanpa `/v1`), `NORMALISASI_AI_MODEL`, `OLLAMA_API_KEY`.
  Contoh nilai hanya dikirim ke endpoint luar jika `NORMALISASI_AI_IZIN_SAMPEL_LUAR=1`.
- **Penghalusan reasoning**: `REASONING_AI_BASE_URL`, `REASONING_AI_MODEL`, `REASONING_AI_API_KEY`,
  `REASONING_AI_ALLOW_EXTERNAL=1` untuk endpoint di luar jaringan. Yang dikirim hanya kalimat
  berplaceholder; hasil disimpan di `synchrono_service.reasoning_patterns`.

## Konversi dump basis data

`.sql`, `.mdf`, `.dmp` diteruskan ke layanan konverter yang sama dengan synchrono-service
(`KONVERTER_URL`, `KONVERTER_MSSQL_URL`, `KONVERTER_ORACLE_URL`, `KONVERTER_MYSQL_URL`),
lalu digrading sebagai parquet dalam job yang sama.

## Kesetaraan dengan synchrono-service

Diuji baris per baris (`bench/parity_grading.py`, `bench/parity_matching.py`) pada
data uji `test-data-csv/uji-master-ae` (A–E) dan `uji-ae/uji_format.*`:

- **Grading**: hasil JSON dan isi enriched.parquet identik untuk 8 berkas (csv, parquet, xlsx).
- **Matching**: status, NIK master, skor, metode, rank_conflict, pola, reasoning, dan
  snapshot identik per baris.

## Pembersihan nama

Delapan kombinasi `nameCleaning` (titles, patronym, abbreviations) dihitung saat grading dan
saat master dimuat. Master atau data K/L yang dimuat sebelum kolom kombinasi baru ada akan
dimuat ulang otomatis saat job pertama yang membutuhkannya.

## Batas yang diketahui

- Pass 3 menarik pasangan kandidat ke DuckDB untuk Jaro-Winkler. Pada berkas tanpa NIK
  (grade C/D/E) jumlahnya bisa jutaan; di jaringan lambat ini jadi leher botol. Langkah
  berikutnya: UDF Java Jaro-Winkler di StarRocks (jar di-host di sisi server), sehingga
  Pass 3 sepenuhnya di StarRocks.
- Penyusunan hasil tidak satu transaksi (DELETE lalu INSERT).
