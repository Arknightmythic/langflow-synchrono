# Deploy Langflow Synchrono ke server (172.16.12.98)

Panduan menaikkan engine grading ke server, memakai SeaweedFS dan PostgreSQL
yang sudah ada di server. Branch: **`deploy-development`**.

Ringkasnya: yang berubah dari pengembangan lokal hanyalah **ke mana engine
menyambung** — SeaweedFS dan PostgreSQL server, bukan yang lokal. Logika
grading tidak disentuh.

---

## 1. Yang sudah dipastikan di server

Diperiksa langsung sebelum panduan ini ditulis:

| | Status |
|---|---|
| SeaweedFS `172.16.12.98:8333` (ADMIN / Password1234) | hidup |
| `s3://syncrono-master/wilayah/master_wilayah_nik.parquet` | ada, 7.265 kecamatan |
| Bucket `syncrono-uploads`, `syncrono-exports` | ada, masih kosong |
| PostgreSQL `172.16.12.98:5432` | hidup, **butuh kata sandi** |
| Langflow `:7860` | belum ada — ini yang dinaikkan |

**Satu hal yang belum diketahui: kata sandi PostgreSQL server.** Server menolak
koneksi tanpa kata sandi. Isi di `.env` (langkah 3). Tanpa itu semua endpoint
balas 500.

---

## 2. Berkas yang perlu dinaikkan — "cukup kode yang perlu"

Salin **hanya** ini ke server (mis. ke `/opt/synchrono/langflow-synchrono/`):

```
lib/                    SEMUA *.py          (logika bersama, wajib)
components/             grading/ config/ matching/   (node API)
infra/
    Dockerfile.langflow
    docker-compose.server.yml
    .env.server.example        -> disalin jadi .env
    apply_schema.py
    schema.sql schema_grading.sql schema_config.sql
    matching_queries.json       (hanya kalau matching dipakai)
    flow_util.py
    buat_flow_grading.py buat_flow_config.py
    buat_flow.py                (hanya kalau matching dipakai)
```

**JANGAN dinaikkan** (bukan kode runtime): `infra/cadangan/` (berisi data
kependudukan asli), pembuat data uji (`buat_data_uji*.py`, `buat_csv_*.py`,
`buat_csv_uji.py`), skrip cadangan/migrasi (`cadangkan_*`, `muat_cadangan`,
`salin_*`, `migrate_master`), `buat_postman.py`, `run_local.py`,
`run_grading_local.py`, seluruh `*.md`, dan berkas gambar.

> `docker-compose.server.yml` hanya me-mount `lib/`, `components/`, dan `infra/`.
> Skrip pengembangan yang ikut tersalin tidak berjalan kecuali dipanggil, jadi
> tidak berbahaya — tapi lebih rapi kalau tidak ikut.

Contoh menyalin dengan rsync (dari mesin ini, sesuaikan host):

```bash
rsync -av --relative \
  lib/ components/ \
  infra/Dockerfile.langflow infra/docker-compose.server.yml \
  infra/.env.server.example infra/apply_schema.py \
  infra/schema.sql infra/schema_grading.sql infra/schema_config.sql \
  infra/matching_queries.json infra/flow_util.py \
  infra/buat_flow_grading.py infra/buat_flow_config.py infra/buat_flow.py \
  user@172.16.12.98:/opt/synchrono/langflow-synchrono/
```

---

## 3. Menyalakan

Di server, dari folder `infra/`:

```bash
cp .env.server.example .env
# WAJIB: isi PG_PASSWORD di .env. Ganti juga LANGFLOW_SUPERUSER_PASSWORD.
nano .env

docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

Cek Langflow hidup:

```bash
curl -s http://localhost:7860/health_check    # harus 200
docker compose -f docker-compose.server.yml logs langflow --tail 30
```

---

## 4. Menyiapkan basis data

Skema idempotent (`IF NOT EXISTS`, `ON CONFLICT`) — aman dijalankan berulang.
Ia membuat `grading_jobs`, `grade_criteria`, `grade_bands`, tabel referensi, dan
menyemai `grade_rules`. Kata sandi terbaca dari `.env` lewat `PG_DSN`.

```bash
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/apply_schema.py

# periksa hasilnya
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/apply_schema.py --verify
```

---

## 5. Membangun flow

```bash
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/buat_flow_grading.py
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/buat_flow_config.py
# matching (opsional, butuh tabel master + matching_queries terisi):
# docker compose -f docker-compose.server.yml exec langflow \
#     python /synchrono/infra/buat_flow.py
```

Node id grading & config bersifat **tetap** (diturunkan dari nama endpoint +
komponen), jadi aman ditanam di portal:

| Endpoint | Node id |
|---|---|
| `grading-dispatch` | `GradingDispatch-a3967` |
| `grading-status` | `GradingStatus-3cc03` |
| `config-rules` | `GradingRuleGet-9c9c5` |
| `config-rules-update` | `GradingRuleUpdate-ea0f7` |
| `grading` (pipeline) | `OpenGradingSession-ab8d5` |

---

## 6. API key

```bash
TOKEN=$(curl -s -X POST http://localhost:7860/api/v1/login \
  -d "username=admin&password=<LANGFLOW_SUPERUSER_PASSWORD>" | \
  python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:7860/api/v1/api_key/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"synchrono-portal"}'
```

Nilai `api_key` **hanya muncul sekali**. Portal memakainya di header `x-api-key`.

---

## 7. Menguji dengan berkas CSV A-E

Berkas uji terverifikasi ada di `test-data-csv/uji-ae/` (dibangkitkan dari
tabel master, diverifikasi lewat pipeline: A→A, B→B, C→C, D→D, E→E). Masing-
masing 3.000 baris.

**Alur sebenarnya (lewat portal):** unggah `uji_gradeA.csv` … `uji_gradeE.csv`
lewat portal seperti berkas biasa. Portal mengubahnya jadi parquet, menaruh di
`s3://syncrono-uploads/uploads/{fileId}/data.parquet`, lalu memanggil
`grading-dispatch`. Grade yang muncul harus A, B, C, D, E berurutan.

**Menguji langsung tanpa portal** (setelah CSV diubah jadi parquet dan ditaruh
di `syncrono-uploads`):

```bash
curl -s -X POST "http://localhost:7860/api/v1/run/grading-dispatch?stream=false" \
  -H "x-api-key: <API_KEY>" -H "Content-Type: application/json" \
  -d '{"output_type":"chat","input_type":"text","input_value":"",
       "tweaks":{"GradingDispatch-a3967":{
         "payload":"{\"fileId\":\"uji-a\",\"s3Bucket\":\"syncrono-uploads\",\"parquetKey\":\"uploads/uji-a/data.parquet\"}"}}}'

# tanya status
curl -s -X POST "http://localhost:7860/api/v1/run/grading-status?stream=false" \
  -H "x-api-key: <API_KEY>" -H "Content-Type: application/json" \
  -d '{"output_type":"chat","input_type":"text","input_value":"",
       "tweaks":{"GradingStatus-3cc03":{"file_id":"uji-a"}}}'
```

Untuk membangkitkan ulang CSV uji (butuh tabel master terisi di PG):

```bash
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/buat_csv_uji.py --verifikasi
docker compose -f docker-compose.server.yml cp \
    langflow:/tmp/csv_uji ./csv_uji
```

---

## 8. Kalau bermasalah

| Gejala | Sebab paling sering |
|---|---|
| Semua endpoint 500 | Kata sandi PG salah/kosong di `.env`. Cek `logs \| grep -i postgres` |
| `NoSuchBucket` | `syncrono-uploads` belum ada, atau `parquetKey` salah |
| Grade jatuh ke E untuk data bagus | `WILAYAH_KECAMATAN_TEGAS=1` pada data dummy — set `0` |
| Flow hilang setelah recreate | `LANGFLOW_DATABASE_URL` tidak ke volume — sudah benar di compose ini |
| `Could not resolve host seaweedfs` | Container tidak bisa jangkau `172.16.12.98:8333` — cek jaringan server |

> **Catatan wilayah.** Selama data masih dummy, biarkan
> `WILAYAH_KECAMATAN_TEGAS=0`: kode kecamatan yang tak dikenal hanya dilaporkan,
> tidak menjatuhkan grade. Nyalakan `1` begitu data Dukcapil asli masuk.
