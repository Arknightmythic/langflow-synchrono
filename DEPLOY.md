# Deploy synchrono-service ke server

Menjalankan grading, config, dan matching + reasoning **tanpa Langflow**, dengan
kontrak API yang sama persis (lihat README.md).

Service ini ada di **branch tersendiri** repo inocts, terpisah dari branch
Langflow — tapi TIDAK berdiri sendiri saat jalan. Logikanya adalah `lib/` dan
`components/` milik `langflow-synchrono` (dimuat langsung, tidak disalin), dan
migrasi, `.env`, serta konverter jalur B-nya juga dari sana. Jadi di server ada
DUA checkout dari repo yang sama, bersebelahan:

```
/opt/synchrono/langflow-synchrono/     branch Langflow — sudah ada, `git pull` seperti biasa
/opt/synchrono/synchrono-service/      branch service (folder ini)
```

Tidak ada satu pun berkas di branch Langflow yang diubah demi service ini.

Semua perintah `docker compose` dijalankan dari `langflow-synchrono/infra`,
dengan **dua** berkas compose: milik Langflow lebih dulu, milik service kedua.

## Dua cara menjalankan

| | Port | Langflow | Portal |
|---|---|---|---|
| **A. Berdampingan** — untuk mencoba | 8000 | tetap jalan di 7860 | alamat diganti ke `:8000` (setelan, bukan kode) |
| **B. Menggantikan** | 7860 | dihentikan | **tidak berubah sama sekali** — kalau `SERVICE_API_KEY` diisi kunci lama |

Keduanya berbagi PostgreSQL engine, SeaweedFS, dan konverter yang sama.

---

## SERVER LAMA — 172.16.12.98

### 1. Ambil branch service (sekali)

```bash
cd /opt/synchrono
# URL repo SAMA dengan milik langflow-synchrono:
git -C langflow-synchrono remote get-url origin
git clone -b <branch-service> <url-repo-itu> synchrono-service
```

### 2. Tambahkan ke `.env` (file yang SAMA dengan Langflow)

```bash
cd /opt/synchrono/langflow-synchrono/infra
nano .env
```

```bash
# Kosong = sama dengan akun admin Langflow
SERVICE_SUPERUSER=
SERVICE_SUPERUSER_PASSWORD=
# Kunci sk-... yang sekarang dipakai portal untuk Langflow (disarankan)
SERVICE_API_KEY=sk-...
# String acak panjang, supaya token login bertahan melewati restart
SERVICE_SECRET_KEY=<acak>
# 8000 = berdampingan. 7860 = menggantikan Langflow (langkah 3B)
SERVICE_PORT=8000
```

Variabel lain — `PG_*`, `S3_*`, `PORTAL_PG_DSN`, `REASONING_AI_*` — **sudah ada**
dan dipakai bersama; contohnya di `langflow-synchrono/infra/.env.server.example`.
Variabel `SERVICE_*` di atas hanya didokumentasikan di sini — branch Langflow
tidak menyebutnya.

### 3A. Menaikkan — berdampingan dengan Langflow

```bash
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env up -d --build synchrono-service
```

Ikut naik: `skema-service` (migrasi + seed, sekali jalan), `s3-relay`, dan
`konverter`. Langflow **tidak** dibangun maupun dinaikkan oleh perintah ini.

### 3B. Menaikkan — menggantikan Langflow

```bash
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.server.yml --env-file .env stop langflow
# di .env: SERVICE_PORT=7860
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env up -d --build synchrono-service
```

Kembali ke Langflow: `stop synchrono-service`, `SERVICE_PORT=8000`, lalu
`up -d langflow` dengan berkas Langflow saja.

### 4. Mendapatkan API key — cara yang sama dengan Langflow

Kalau `SERVICE_API_KEY` sudah diisi kunci lama portal, langkah ini tidak perlu.

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=<sandi>' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://localhost:8000/api/v1/api_key/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"synchrono-portal"}'
```

Nilai `api_key` di balasan **hanya muncul sekali**. (Pakai `:7860` kalau 3B.)

### 5. Memeriksa

```bash
curl -s http://localhost:8000/health_check          # {"status":"ok","chat":"ok","db":"ok"}
docker logs synchrono-service 2>&1 | grep '\[start\]'   # keenam flow harus "ok"

curl -s -X POST 'http://localhost:8000/api/v1/run/config-rules?stream=false' \
  -H 'x-api-key: <kunci>' -H 'Content-Type: application/json' \
  -d '{"output_type":"chat","input_type":"text","input_value":"","tweaks":{"GradingRuleGet-9c9c5":{"grade_id":"2"}}}'
```

Kalau Langflow juga jalan (3A), bandingkan keduanya dengan permintaan yang sama:

```bash
LANGFLOW_PASS=<sandi> SERVICE_PASS=<sandi> \
  python3 /opt/synchrono/synchrono-service/infra/uji_kompat.py
```

### 6. REDEPLOY

Kalau yang berubah hanya branch Langflow (`lib/`, `components/`):

```bash
cd /opt/synchrono/langflow-synchrono && git pull
cd infra
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env restart synchrono-service
```

`lib/` dan `components/` di-mount dari checkout itu — **restart cukup**, dan
**tidak ada flow yang perlu dibangun ulang** (beda dengan Langflow).

Kalau branch service ikut berubah (`app/`, Dockerfile), build ulang:

```bash
cd /opt/synchrono/synchrono-service && git pull
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env up -d --build synchrono-service
```

---

## SERVER BARU — 192.168.2.107

Sama dengan server lama, dengan dua perbedaan: alamat di `.env` (sudah terisi
untuk Langflow: `PG_HOST=192.168.2.107`, `S3_ENDPOINT=192.168.2.107:8355`), dan
PostgreSQL + SeaweedFS harus hidup lebih dulu lewat `docker-compose.infra.yml`.

### 1. Ambil branch service (sekali)

```bash
cd /opt/synchrono
git -C langflow-synchrono remote get-url origin
git clone -b <branch-service> <url-repo-itu> synchrono-service
```

### 2. Tambahkan ke `.env`

```bash
cd /opt/synchrono/langflow-synchrono/infra
nano .env
```

Isinya sama dengan server lama langkah 2 (`SERVICE_*`).

### 3. Pastikan infrastruktur hidup

```bash
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.infra.yml ps
```

Kalau belum: `docker compose -f docker-compose.infra.yml --env-file .env up -d`.

### 4A. Berdampingan dengan Langflow

```bash
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env up -d --build synchrono-service
```

### 4B. Menggantikan Langflow

```bash
cd /opt/synchrono/langflow-synchrono/infra
docker compose -f docker-compose.server.yml --env-file .env stop langflow
# di .env: SERVICE_PORT=7860
docker compose -f docker-compose.server.yml \
               -f ../../synchrono-service/docker-compose.server.yml \
               --env-file .env up -d --build synchrono-service
```

### 5. API key, memeriksa, redeploy

Sama dengan server lama langkah 4, 5, dan 6.

---

## Unggahan `.mdf` dan `.dmp`

Konverternya sama dengan milik Langflow, dan menyalakannya pun sama
(langflow-synchrono/DEPLOY.md §0C):

```bash
docker compose -f docker-compose.server.yml --env-file .env \
  --profile mssql --profile oracle up -d konverter-mssql konverter-oracle
```

## Yang perlu diingat

- **Dua branch harus sejalan.** Service memuat komponen lewat path berkas dan
  memakai rantai node yang sama dengan `infra/buat_flow_*.py` Langflow (lihat
  `app/alur.py`). Perubahan isi komponen ikut berlaku otomatis; tapi kalau
  branch Langflow menambah atau mengganti nama komponen/flow, `app/alur.py` di
  branch ini perlu disesuaikan. `infra/uji_kompat.py` menangkap selisihnya.
- Build image memerlukan akses PyPI dari server (paket Python) dan internet
  untuk extension DuckDB (`httpfs`, `postgres`, `excel`) — sama dengan image
  Langflow.
- `WEB_CONCURRENCY` di atas 1 memperbanyak proses, dan batas konkurensi
  grading/matching berlaku **per proses**.
