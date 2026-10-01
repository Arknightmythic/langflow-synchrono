# Deploy synchrono-service ke server

Menjalankan grading, config, dan matching + reasoning **tanpa Langflow**, dengan
kontrak API yang sama persis (lihat README.md).

**Berdiri sendiri** (sejak 28 Sep 2026). Satu checkout branch service sudah
cukup: `lib/`, `components/`, konverter jalur B, dan migrasi/seed ada di repo
ini. Checkout `langflow-synchrono` TIDAK dibutuhkan. Dari luar hanya perlu:

| | Server 192.168.2.107 | Server yang belum punya |
|---|---|---|
| PostgreSQL engine | `synchrono-postgres` (stack lama), port 5430 | `docker-compose.infra.yml` di folder ini |
| SeaweedFS | `synchrono-seaweedfs` (stack lama), port 8355 | idem |

Dua-duanya dijangkau lewat alamat di `.env`, jadi tidak peduli milik stack mana.

## Dua cara menjalankan

| | `SERVICE_PORT` | Langflow | Portal |
|---|---|---|---|
| **A. Berdampingan** — untuk mencoba | 8000 | tetap jalan di 7860 | alamat diganti ke `:8000` (setelan, bukan kode) |
| **B. Menggantikan** | 7860 | dihentikan | **tidak berubah sama sekali** — kalau `SERVICE_API_KEY` diisi kunci lama |

---

## 1. Ambil branch service (sekali)

```bash
mkdir -p ~/syncrono/service && cd ~/syncrono/service
git clone -b service-master <url-repo> synchrono-service
cd synchrono-service
```

## 2. Isi `.env` (di folder ini)

```bash
cp .env.server.example .env
nano .env
```

Wajib: `PG_HOST`, `PG_PASSWORD`, `S3_ENDPOINT`, `S3_RELAY_TARGET`,
`S3_ACCESS_KEY`/`S3_SECRET_KEY`. Untuk portal: `SERVICE_API_KEY` (kunci `sk-...`
yang sekarang disetel di portal), `PORTAL_PG_DSN`. Penjelasan tiap variabel ada
di berkas contohnya.

## 3. PostgreSQL + SeaweedFS

Sudah ada (192.168.2.107): lewati. Belum ada:

```bash
docker compose -f docker-compose.infra.yml --env-file .env up -d
```

## 4. Menaikkan

```bash
alias dc='docker compose -f docker-compose.server.yml --env-file .env'
dc up -d --build
```

Ikut naik: `skema-service` (migrasi + seed, sekali jalan), `s3-relay`,
`konverter` (PostgreSQL 18). Untuk **B. Menggantikan**, hentikan Langflow lama
lebih dulu (port 7860 harus kosong) dan isi `SERVICE_PORT=7860`.

Konverter lain di balik profil — nyalakan yang dibutuhkan saja:

```bash
dc --profile mysql  up -d --build konverter-mysql     # .sql MySQL/MariaDB
dc --profile mssql  up -d --build konverter-mssql     # .mdf
dc --profile oracle up -d --build konverter-oracle    # .dmp — nyala pertama beberapa menit
```

Kalau profilnya tidak dinyalakan, unggahan format itu gagal dengan pesan yang
menyebut perintah menyalakannya — bukan gagal diam-diam.

Supaya alias tidak hilang saat login ulang, tulis ke `~/.bashrc` dengan path
absolut:

```bash
D=~/syncrono/service/synchrono-service
echo "alias dc='docker compose -f $D/docker-compose.server.yml --env-file $D/.env'" >> ~/.bashrc
```

## 5. Mendapatkan API key — cara yang sama dengan Langflow

Kalau `SERVICE_API_KEY` sudah diisi kunci lama portal, langkah ini tidak perlu.

```bash
TOKEN=$(curl -s -X POST http://localhost:7860/api/v1/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=<sandi>' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -X POST http://localhost:7860/api/v1/api_key/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"synchrono-portal"}'
```

Nilai `api_key` di balasan **hanya muncul sekali**. (Pakai `:8000` kalau A.)

## 6. Memeriksa

```bash
docker logs synchrono-skema-service | tail -2           # "[skema] migrasi & seeding selesai."
docker logs synchrono-service 2>&1 | grep '\[start\]'   # keenam flow "ok"
curl -s http://localhost:7860/health_check              # {"status":"ok","chat":"ok","db":"ok"}
docker exec synchrono-konverter psql --version          # PostgreSQL 18.x

KEY=$(grep '^SERVICE_API_KEY=' .env | cut -d= -f2- | cut -d, -f1)
curl -s -X POST 'http://localhost:7860/api/v1/run/config-rules?stream=false' \
  -H "x-api-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"output_type":"chat","input_type":"text","input_value":"","tweaks":{"GradingRuleGet-9c9c5":{"grade_id":"2"}}}' | head -c 200; echo
```

Di portal, **dua** alamat harus menunjuk ke service ini: alamat Grading Engine
di halaman "Parameter Koneksi" (grading), dan `MATCHING_SERVICE_URL` +
`MATCHING_SERVICE_API_KEY` di env portal (matching).

## 7. REDEPLOY

```bash
cd ~/syncrono/service/synchrono-service && git pull
```

| Yang berubah | Perintah |
|---|---|
| `lib/`, `components/` | `dc restart synchrono-service` — di-mount, tidak perlu build |
| `konverter/*.py` | `dc restart konverter` (dan `konverter-mysql` dst. yang hidup) |
| `infra/Dockerfile.konverter*` | `dc up -d --build konverter` (dan yang lain) |
| `app/`, `Dockerfile`, `requirements.txt` | `dc up -d --build synchrono-service` |
| `infra/skema/db/` (migrasi baru) | `dc up -d skema-service` — migrasi yang sudah tercatat tidak diulang |

**Tidak ada flow yang perlu dibangun ulang** — beda dengan Langflow.

**Migrasi 008 (1 Okt 2026)** — hanya basis data, kode tidak berubah:

```bash
git pull && dc up -d skema-service && docker logs -f synchrono-skema-service   # "== 008_samakan_kueri_blocking.sql =="
```

Service tidak perlu di-restart: kueri blocking dibaca dari basis data di awal
setiap job.

**Pembaruan konfigurasi dinamis (30 Sep 2026)** mengubah `lib/`, `app/`,
`components/`, dan menambah migrasi `006_config_dinamis` dan
`007_blocking_semua_elemen`. Urutannya: migrasi dulu, baru service —

```bash
dc up -d skema-service && docker logs -f synchrono-skema-service   # 006 & 007 diterapkan, lalu selesai
dc up -d --build synchrono-service
```

Migrasi mengisi bobot/elemen kosong dengan nilai yang selama ini berlaku dan
hanya MENAMBAH kolom keluaran kueri blocking, jadi hasil matching tidak berubah
sampai ada yang mengubah konfigurasi lewat API (KONFIGURASI.md). Periksa:
`GET /api/v1/config/rules` memuat `configVersion`, dan setiap grade A–E punya
`matching.weights` serta `matching.availableElements` berisi keenam elemen.

---

## Pindah dari susunan lama (dua checkout + overlay)

Sampai 28 Sep 2026 service di server 192.168.2.107 dinaikkan dari
`~/syncrono/service/engine/langflow-syncorono/infra` dengan DUA berkas compose
dan `.env` di folder Langflow. Pindah ke susunan mandiri:

```bash
cd ~/syncrono/service/engine/synchrono-service && git pull

# .env pindah ke folder ini — nilai yang sama.
cp ../langflow-syncorono/infra/.env .env
nano .env    # pastikan ada S3_RELAY_TARGET; LANGFLOW_* tidak dipakai lagi dan boleh dihapus

alias dc='docker compose -f docker-compose.server.yml --env-file .env'
dc config --quiet && echo "konfigurasi ok"
dc up -d --build --remove-orphans
dc --profile mysql up -d --build konverter-mysql      # kalau butuh dump MySQL
```

`COMPOSE_PROJECT_NAME=synchrono-service` tetap sama, jadi container lama diganti
dan volume dipertahankan. Volume `synchrono-service_konverter-data` (klaster
PostgreSQL 16 konverter lama) tidak dipakai lagi — `docker volume rm` kalau mau.
Checkout `langflow-syncorono` di folder `engine/` tidak dibutuhkan lagi oleh
service; biarkan atau hapus.

Kembali ke Langflow kapan saja: `dc stop synchrono-service`, lalu
`docker compose start langflow` di folder Langflow lama.

---

## Yang perlu diingat

- **Salinan dari langflow-synchrono.** `lib/`, `components/`, `konverter/`, dan
  `infra/skema/` berasal dari sana dan kini berkembang sendiri. Perbaikan di satu
  repo biasanya perlu dibawa ke yang lain; `python3 infra/cek_salinan.py` (kalau
  kedua checkout bersebelahan) menunjukkan apa saja yang berbeda.
- `infra/uji_kompat.py` membandingkan service dengan Langflow yang sedang jalan
  — alat uji, bukan syarat.
- Build image memerlukan akses PyPI dari server dan internet untuk extension
  DuckDB (`httpfs`, `postgres`, `excel`, `mysql`), serta image dasar PostgreSQL
  18 / MariaDB 11.8 untuk konverter.
- `WEB_CONCURRENCY` di atas 1 memperbanyak proses, dan batas konkurensi
  grading/matching berlaku **per proses**.
