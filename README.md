# synchrono-service-starrocks

Versi kedua synchrono-service: grading dan matching memakai **StarRocks** sebagai
penyimpanan dan mesin join, dengan **alur hybrid**. Kontrak API-nya sama dengan
synchrono-service (REST + kontrak Langflow `/api/v1/run/...`), jadi portal bisa
diarahkan ke sini tanpa mengubah kode.

## Alur

```
Unggah → S3 ─► worker grading (DuckDB, logika sama dengan synchrono-service)
                 ├─ enriched.parquet ke S3 (kontrak lama tetap)
                 ├─ Stream Load (gzip) ─► syncrono_starrock.syncrono_kl_records (bahan matching)
                 └─ Stream Load (gzip) ─► syncrono_starrock.syncrono_kl_enriched (hasil grading untuk portal)

worker matching:
  Pass 1   join NIK berkas × master                       StarRocks
  Pass 1   cek nama ibu bertentangan (Jaro-Winkler)       DuckDB (hanya pasangan yang ibunya beda)
  Pass 2   kandidat nama + tanggal lahir + nama ibu       StarRocks
           urutan kandidat (NIK dekat, tempat lahir)      DuckDB
  Pass 3   blocking + skor Jaro-Winkler + 10 teratas,     StarRocks, satu kueri (UDF Java)
           seri, klasifikasi, pola
           — tanpa UDF_JAR_URL: kandidat ditarik ke DuckDB dan dinilai di sana
  Hasil    snapshot, signature, reasoning, tabel portal   StarRocks (CTE, INSERT … SELECT)
```

Master **dibersihkan sekali** saat dimuat (semua varian pembersihan nama dihitung
lebih dulu), jadi matching tidak lagi membersihkan ratusan juta baris master di
setiap job.

## Dua database (StarRocks: skema = database)

Tabel service, K/L, dan portal ada di `syncrono_starrock`. Nama tabelnya diawali nama
kelompoknya: `syncrono_service_`, `syncrono_kl_`, `syncrono_portal_`. Master punya database
sendiri, `syncrono_master`. Nama keduanya bisa diganti lewat `DB_STARROCK` dan `DB_MASTER`.

| Tabel | Isi |
|---|---|
| `syncrono_starrock.syncrono_service_*` | job grading (`grading_jobs`), aturan (`grade_rules`, `grade_criteria`, `grade_bands`, `matching_queries`), `engine_config`, `config_versions`, `config_history`, `reasoning_patterns`, `service_api_keys`, registri `masters`, tabel kerja matching `syncrono_service_w_*` (dihapus setelah job) |
| `syncrono_starrock.syncrono_kl_records` | data K/L hasil grading untuk matching, dipartisi per `file_id`. Partisi berkas dihapus oleh task latar `kl.release` (antrean matching) setelah job matching selesai dan callback terkirim, kecuali masih ada job lain untuk berkas itu; matching berikutnya memuatnya lagi dari `enriched.parquet` |
| `syncrono_starrock.syncrono_kl_enriched` | hasil grading untuk portal: satu baris = satu baris `enriched.parquet`, dipartisi per `file_id`. Lihat di bawah |
| `syncrono_starrock.syncrono_portal_*` | `matching_jobs`, `matching_results` (bentuk sama dengan tabel portal). Kolom `reviewed_count`, `reviewed_at`, `reviewed_by` diisi portal saat review; service hanya membuatnya |
| `syncrono_master.persons`, `syncrono_master.dictionary` | master (dipartisi per `master_id`), kamus pengenalan kolom |

### Hasil grading untuk portal (`syncrono_kl_enriched`)

Diisi di akhir grading dengan membaca ulang `enriched.parquet` yang baru ditulis, jadi isinya
sama dengan parquet (parquet tetap ditulis seperti biasa). Grading ulang berkas yang sama
mengganti isinya.

| Kolom | Isi |
|---|---|
| `file_id`, `row_no` | berkas dan nomor baris di parquet (mulai 1, urutan parquet) |
| `data` | seluruh kolom parquet untuk baris itu, sebagai JSON dengan nama kolom yang sama. NaN/infinity menjadi `null` |
| `nik_trusted`, `is_anomaly`, `anomaly_type` | salinan kolom parquet yang sama, untuk filter |

StarRocks mengurutkan kunci JSON menurut abjad. Urutan kolom parquet ada di hasil grading,
`enrichedStorage.columns` (callback grading dan `syncrono_service_grading_jobs.result`).

```sql
SELECT row_no, data
  FROM syncrono_starrock.syncrono_kl_enriched
 WHERE file_id = '<fileId>'          -- tambah AND is_anomaly untuk baris beranomali saja
 ORDER BY row_no
 LIMIT 100 OFFSET 0;
```

Cek kesamaan dengan parquet: `python bench/parity_enriched.py [fileId ...]`.

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
| `POST /api/v1/login`, `GET/POST /api/v1/api_key/`, `DELETE /api/v1/api_key/{id}` | login & API key ala Langflow (kunci disimpan di `syncrono_starrock.syncrono_service_service_api_keys`) |
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
  berplaceholder; hasil disimpan di `syncrono_starrock.syncrono_service_reasoning_patterns`.

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

- UDF Jaro-Winkler (`udf/`, jar dibangun di Dockerfile) disajikan oleh api di
  `/udf/synchrono-udf.jar` dan didaftarkan worker matching ke StarRocks. FE dan BE harus bisa
  mengunduhnya dari `UDF_JAR_URL`; kalau tidak bisa, Pass 3 otomatis kembali ke DuckDB.
  Hasil Java-nya identik bit per bit dengan `jaro_winkler_similarity` dan `round` DuckDB
  (diuji 3 juta pasangan). Nama fungsi (`syncrono_jw_<kunci>`) diturunkan dari isi jar dan
  `UDF_JAR_URL`, jadi tiap deployment memakai fungsinya sendiri; BE yang restart mengunduh
  ulang jar dari URL itu, sehingga api harus tetap hidup. Fungsi lama tidak dihapus otomatis:
  `SHOW FUNCTIONS FROM syncrono_starrock`, lalu `DROP FUNCTION` bila perlu.
- Penyusunan hasil tidak satu transaksi (DELETE lalu INSERT).
