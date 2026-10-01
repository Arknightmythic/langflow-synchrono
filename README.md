# Synchrono Service

Grading, konfigurasi, dan matching + reasoning — **tanpa Langflow, dengan
kontrak Langflow.** Portal yang sekarang memanggil Langflow bisa dipindah ke
service ini **tanpa mengubah satu baris kode pun**: cara mendapatkan API key,
bentuk permintaan, `tweaks` dan node id-nya, selubung balasan, sampai kode
status dan teks galatnya sama.

Deploy ke server: [DEPLOY.md](DEPLOY.md). Cara kerja matching & fuzzy match
per grade: [MATCHING.md](MATCHING.md). Melihat & mengubah ambang, bobot, elemen
kosong, dan pembersihan nama lewat API: [KONFIGURASI.md](KONFIGURASI.md).

---

## Untuk portal: yang sama, dan yang boleh diganti

| | Langflow | Service ini |
|---|---|---|
| Mendapatkan kunci | `POST /api/v1/login` (form) → `POST /api/v1/api_key/` | **sama** |
| Memanggil | `POST /api/v1/run/<endpoint>?stream=false`, header `x-api-key` (atau `?x-api-key=`) | **sama** |
| Badan | `{output_type, input_type, input_value, tweaks}` | **sama** |
| Balasan | `outputs[0].outputs[0].results.message.text` (string JSON) | **sama**, seluruh selubung |
| Galat | 403 / 404 / 500 dengan `detail` persis | **sama** |
| Alamat | `http://<host>:7860` | `http://<host>:8000` — atau `:7860` kalau menggantikan Langflow |

Enam flow, node id **tetap** dan sama dengan Langflow:

| Endpoint | Node id | Untuk |
|---|---|---|
| `grading-dispatch` | `GradingDispatch-a3967` | kirim job grading (asinkron) |
| `grading-status` | `GradingStatus-3cc03` | polling status grading |
| `config-rules` | `GradingRuleGet-9c9c5` | baca aturan grade |
| `config-rules-update` | `GradingRuleUpdate-ea0f7` | ubah aturan grade |
| `grading` | `OpenGradingSession-ab8d5` | pipeline grading sinkron (penelusuran) |
| `matching-dispatch` | `MatchingDispatch-b4819` | kirim job matching + reasoning (asinkron) |

**Kunci API — dua pilihan.** Buat lewat `login` → `api_key` seperti di Langflow
(disimpan sebagai SHA-256 di tabel `service_api_keys` PostgreSQL engine), atau
isi `SERVICE_API_KEY` dengan kunci `sk-...` yang **sekarang dipakai portal untuk
Langflow** — portal pindah tanpa mengubah setelan kuncinya sekalipun.

---

## Terbukti sama — diukur 28 Sep 2026, berdampingan dengan Langflow

| Uji | Hasil |
|---|---|
| `infra/uji_kompat.py` — permintaan yang sama ke keduanya | **semua sama**: login, api_key (buat/daftar/hapus/penyamaran), 403/404/500 beserta `detail`, selubung (100 jalur kunci), isi `text`, dan perlakuan nilai tweak yang janggal (`3` dibuang, `{"value": ...}` dipakai, array ditolak) |
| Grading tujuh format lewat `grading-dispatch` | `csv`, `xlsx`, `parquet`, `sql`, `mdf`, `dmp` COMPLETED dengan **result identik**; `xls` ditolak dengan pesan yang sama |
| Matching grade D & B lewat simulasi portal (`matching-dispatch`) | 400.040 baris: keputusan, snapshot, dan **reasoning identik**; cache reasoning dipakai bersama (22/22 dan 32/32 pola dari cache, nol panggilan LLM); callback HTTP 200 |
| `infra/uji_asap.py` (endpoint REST + pembanding 62 field) | 21/21 |
| `.sql` MySQL/MariaDB lewat `grading-dispatch` — tanpa dan dengan `sqlDialect` | COMPLETED; result **identik di setiap field** dengan `.csv` dan `.sql` PostgreSQL (grade A, 3.000 trusted, 0 anomali). Tanpa `sqlDialect`, konverter PostgreSQL hanya mengintip kepalanya lalu engine membelokkannya ke `konverter-mysql` |
| **Berdiri sendiri** (tanpa checkout Langflow): 15 dump dummy (`test-data-csv/uji-sql/`, pg_dump 16 / mysqldump 8.4 / mariadb-dump 10.11, grade A–E) + 5 CSV | 20/20 COMPLETED; **setiap dump identik di setiap field dengan CSV grade-nya** |
| Berdiri sendiri: matching grade D lewat simulasi portal | 200.020 baris, COMPLETED, reasoning 22 pola, tersuntik ke DB portal; migrasi/seed dari `infra/skema/` jalan |

---

## Berdiri sendiri — logika Langflow, disalin ke repo ini

Sejak 28 Sep 2026 service ini **tidak butuh checkout langflow-synchrono** untuk
jalan. Yang dijalankan tetap **kelas komponen yang sama** dengan milik Langflow
— tapi salinannya ada di repo ini:

| Folder | Isi | Asal |
|---|---|---|
| `lib/` | grading, matching, reasoning, konversi, normalisasi | `langflow-synchrono/lib/` |
| `components/` | komponen keenam flow + matching lama | `langflow-synchrono/components/` |
| `konverter/`, `infra/Dockerfile.konverter*` | konverter jalur B `.sql`/`.mdf`/`.dmp` | `langflow-synchrono/konverter/`, `infra/` |
| `infra/skema/` | `migrate.py`, `seed.py`, migrasi & seeder | `langflow-synchrono/infra/` |

Teks balasan identik dengan Langflow karena kodenya sama, dimuat lewat
[`app/alur.py`](app/alur.py). Kedua repo kini berkembang sendiri; perbaikan di
satu sisi biasanya perlu dibawa ke sisi lain. Kalau kedua checkout
bersebelahan, perbedaannya terlihat dengan:

```powershell
python infra\cek_salinan.py      # N/N sama, atau daftar yang berbeda
```

Satu kelebihan nyata dibanding Langflow: **tidak ada flow yang perlu dibangun
ulang.** Langflow menyimpan salinan kode komponen DI DALAM flow; komponen yang
berubah tidak berlaku sampai flow-nya dibangun ulang. Terbukti di mesin ini:
flow `grading-dispatch` Langflow masih memakai kode 21 Sep, tanpa pemeriksaan
format yang ditambahkan 24 Sep — `.xls` diterima lalu gagal di pekerja,
alih-alih ditolak seketika. Service ini selalu memuat berkas komponen terkini;
cukup restart container.

---

## Menjalankan di mesin ini

Yang dibutuhkan dari luar hanya PostgreSQL di `localhost:5432` (DBngin) dan
SeaweedFS/S3 di `localhost:8333` — milik stack mana pun, atau
`docker-compose.infra.yml` di folder ini. Konverter dan penerus S3 ikut naik:

```powershell
docker compose up -d --build
```

- Login lokal: `admin` / `synchrono123`.
- Kunci tetap untuk skrip & benchmark: `synchrono-bench-key`.
- Dokumentasi API otomatis: <http://localhost:8000/docs>.
- Perubahan di `lib/`, `components/`, atau `app/`: **`docker restart synchrono-service`**.
  Kalau ada migrasi baru di `infra/skema/db/migrasi/` (mis. `006_config_dinamis`),
  jalankan dulu `python infra/skema/migrate.py && python infra/skema/seed.py`
  (dari folder `infra/skema`, `PG_DSN` menunjuk PostgreSQL engine), baru restart.
- Dump `.sql` MySQL/MariaDB, `.mdf`, `.dmp` butuh konverternya sendiri, di balik
  profil: `docker compose --profile mysql up -d --build konverter-mysql`
  (atau `mssql` / `oracle`). Tanpa itu unggahan format tersebut gagal dengan
  pesan yang menyebut profilnya.
- Perubahan di `konverter/`: **`docker restart synchrono-service-konverter`**
  (dan `-mysql` dst. yang hidup) — kodenya di-mount, dimuat sekali saat menyala.
  Kalau `infra/Dockerfile.konverter*` yang berubah: `docker compose up -d --build
  konverter`.

Koleksi Postman: **`infra/postman_synchrono_service.json`** — auth, enam flow,
contoh galat, kesehatan, dan REST. Jalankan 0a → 0b dulu (atau isi `api_key`);
untuk server, ganti variabel `base_url`. Bangkitkan ulang dengan
`python infra\buat_postman.py` — node id diambil dari `app/alur.py`.

Menguji kecocokan dengan Langflow (dari host, pustaka standar saja):

```powershell
python infra\uji_kompat.py
```

---

## Endpoint REST — dipertahankan untuk benchmark

Bentuk lama service pembanding, tetap jalan untuk `beban/` dan
`infra/uji_asap.py`. **Portal tidak memakainya.**

| | |
|---|---|
| `POST /api/v1/grading/jobs` | kirim job — kini lewat komponen `GradingDispatch` yang sama, jadi ikut menolak format di depan |
| `GET /api/v1/grading/jobs/{fileId}`, `/by-id/{jobId}` | status |
| `POST /api/v1/grading/run` | grading sinkron |
| `GET/PATCH /api/v1/config/rules[/{gradeId}]` | aturan grade: kriteria, pita, ambang, bobot, elemen kosong, pembersihan nama |
| `PATCH /api/v1/config/global` | nilai global: bobot skor mutu, kombinasi grade E, selisih seri, ambang nama ibu |
| `GET /api/v1/config/history`, `/versions/{versi}` | riwayat perubahan konfigurasi & isi satu versi — **boleh dipakai portal** ([KONFIGURASI.md](KONFIGURASI.md)) |
| `POST /api/v1/matching/run` | matching **lama** (n1..n7, tanpa pass & reasoning) — bukan pipeline spesifikasi |

Kode statusnya bermakna (202, 400, 401, 404, 409, 422, 503), badan balasannya
JSON langsung tanpa selubung. Perbandingan kinerja lama: [PERBANDINGAN.md](PERBANDINGAN.md).

---

## Perbedaan kecil yang disengaja

- `stream=true` diterima, tapi balasan selalu utuh (portal memakai `stream=false`).
- `login` tidak memasang cookie — token dibaca dari badan balasan, seperti portal.
- Hanya enam flow di atas. API pengelolaan Langflow lainnya (`/api/v1/flows`,
  `/api/v1/all`, kanvas) tidak ada; flow matching lama ber-UUID juga tidak.
- `/health` membawa kunci tambahan; `db` di `/health_check` adalah PostgreSQL engine.
- `config-rules` membawa bagian tambahan (`matching.weights`, `missingElements`,
  `nameCleaning`, `blocking`, `analysis`, `global`, `configVersion`), dan
  `config-rules-update` menerima kunci `global`. Semua field lama tetap ada
  dengan bentuk yang sama — sejak 30 Sep 2026, lihat KONFIGURASI.md.
- Pemeriksaan kunci selalu aktif, kecuali `SERVICE_AUTH=off` (hanya mesin lokal).
  Dulu `SERVICE_API_KEY` kosong berarti mati — kini kosong berarti "hanya kunci
  dari basis data".

---

## Isi folder

```
app/
  main.py            aplikasi FastAPI, kesehatan, pemasangan rute
  alur.py            enam flow Langflow: memuat komponen, menjalankan, menyelubungi
  akses.py           login, token, API key, pemeriksaan x-api-key
  rute_langflow.py   /api/v1/login, /api/v1/api_key/, /api/v1/run, /health_check
  rute_grading.py    REST grading
  rute_config.py     REST aturan grade
  rute_matching.py   REST matching lama (n1..n7)
  skema.py           model muatan REST (dokumentasi /docs)
lib/                 logika grading/matching/reasoning/konversi — salinan dari Langflow
components/          komponen keenam flow + matching lama — salinan dari Langflow
konverter/           layanan konversi jalur B — salinan dari Langflow
infra/
  skema/             migrate.py, seed.py, db/migrasi, db/seeder — salinan dari Langflow
  Dockerfile.konverter*, konverter*-nyalakan.sh, konverter-mysql.cnf
                     image konverter jalur B (PostgreSQL 18, SQL Server,
                     Oracle, MariaDB)
  s3-config.json     identitas S3 untuk SeaweedFS di docker-compose.infra.yml
  cek_salinan.py     apa saja yang berbeda dari langflow-synchrono
  postman_synchrono_service.json   koleksi Postman (dibangkitkan)
  buat_postman.py    pembangkit koleksi Postman
  uji_kompat.py      Langflow vs service, permintaan yang sama (alat uji)
  uji_asap.py        uji REST + pembanding hasil grading
  siapkan_seaweed.py isi SeaweedFS lokal dengan tata letak server
beban/               benchmark k6, Prometheus, Grafana (service vs Langflow)
docker-compose.yml         lokal, berdiri sendiri
docker-compose.server.yml  server, berdiri sendiri — `.env` di folder ini
docker-compose.infra.yml   PostgreSQL + SeaweedFS untuk server yang belum punya
.env.server.example        contoh `.env` server
MATCHING.md                cara kerja matching per grade, dan bedanya dengan sistem lama
KONFIGURASI.md             API konfigurasi: ambang, bobot, elemen kosong, riwayat, versi
compose.langflow-lokal.yml override Langflow lokal (hanya untuk membandingkan)
```

> **Branch terpisah, berdiri sendiri.** Service ini di-push sebagai branch
> tersendiri di repo inocts, dan tidak butuh checkout `langflow-synchrono` untuk
> jalan — lihat DEPLOY.md. Yang masih menyebut Langflow hanya alat pembanding
> (`uji_kompat.py`, `beban/`, `compose.langflow-lokal.yml`).
