# Synchrono Service

Grading, konfigurasi, dan matching + reasoning — **tanpa Langflow, dengan
kontrak Langflow.** Portal yang sekarang memanggil Langflow bisa dipindah ke
service ini **tanpa mengubah satu baris kode pun**: cara mendapatkan API key,
bentuk permintaan, `tweaks` dan node id-nya, selubung balasan, sampai kode
status dan teks galatnya sama.

Deploy ke server: [DEPLOY.md](DEPLOY.md).

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

---

## Satu logika — dimuat langsung, bukan disalin

Yang dijalankan adalah **kelas komponen yang sama** dengan yang dipakai
Langflow (`langflow-synchrono/components/`) dan modul `lib/` yang sama, dimuat
langsung dari checkout repo yang sama ([`app/alur.py`](app/alur.py)). Teks
balasan identik bukan karena ditiru, tapi karena kode yang menghasilkannya sama.

Satu kelebihan nyata dibanding Langflow: **tidak ada flow yang perlu dibangun
ulang.** Langflow menyimpan salinan kode komponen DI DALAM flow; komponen yang
berubah tidak berlaku sampai flow-nya dibangun ulang. Terbukti di mesin ini:
flow `grading-dispatch` Langflow masih memakai kode 21 Sep, tanpa pemeriksaan
format yang ditambahkan 24 Sep — `.xls` diterima lalu gagal di pekerja,
alih-alih ditolak seketika. Service ini selalu memuat berkas komponen terkini;
cukup restart container.

**Satu pengecualian: konverter jalur B DISALIN.** Keputusan 28 Sep 2026 —
konverter `.sql`/`.mdf`/`.dmp` (`konverter/`, `infra/Dockerfile.konverter*`)
ada di kedua branch, dan service membangun miliknya sendiri. Rute konversinya
(`lib/_konversi.py`) tetap dimuat dari `lib/` bersama. Dua salinan harus
sejalan — periksa sebelum deploy:

```powershell
python infra\cek_konverter.py      # 16/16 sama, atau daftar yang berbeda
```

---

## Menjalankan di mesin ini

Service ini menumpang jaringan dan penyimpanan stack Langflow — SeaweedFS
lokal, penerus S3, dan PostgreSQL di host — jadi stack itu harus hidup lebih
dulu. Konverter jalur B-nya milik service sendiri (`service-konverter*`):

```powershell
cd ..\langflow-synchrono\infra ; docker compose up -d seaweedfs s3-relay
cd ..\..\synchrono-service      ; docker compose up -d --build
```

- Login lokal: `admin` / `synchrono123` (sama dengan Langflow lokal).
- Kunci tetap untuk skrip & benchmark: `synchrono-bench-key`.
- Dokumentasi API otomatis: <http://localhost:8000/docs>.
- Perubahan di `lib/`, `components/`, atau `app/`: **`docker restart synchrono-service`**.
- Dump `.sql` MySQL/MariaDB, `.mdf`, `.dmp` butuh konverternya sendiri, di balik
  profil: `docker compose --profile mysql up -d --build service-konverter-mysql`
  (atau `mssql` / `oracle`). Tanpa itu unggahan format tersebut gagal dengan
  pesan yang menyebut profilnya.
- Perubahan di `konverter/`: **`docker restart synchrono-service-konverter`**
  (dan `-mysql` dst. yang hidup) — kodenya di-mount, dimuat sekali saat menyala.
  Kalau `infra/Dockerfile.konverter*` yang berubah: `docker compose up -d --build
  service-konverter`.

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
| `GET/PATCH /api/v1/config/rules[/{gradeId}]` | aturan grade |
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
infra/
  postman_synchrono_service.json   koleksi Postman (dibangkitkan)
  buat_postman.py    pembangkit koleksi Postman
  uji_kompat.py      Langflow vs service, permintaan yang sama
  uji_asap.py        uji REST + pembanding hasil grading
  siapkan_seaweed.py isi SeaweedFS lokal dengan tata letak server
  cek_konverter.py   salinan konverter di sini vs langflow-synchrono
  Dockerfile.konverter*, konverter*-nyalakan.sh, konverter-mysql.cnf
                     image konverter jalur B (PostgreSQL 18, SQL Server,
                     Oracle, MariaDB) — SALINAN dari langflow-synchrono
konverter/           layanan konversi jalur B — SALINAN dari langflow-synchrono
beban/               benchmark k6, Prometheus, Grafana
docker-compose.yml         lokal, berdampingan dengan stack Langflow
docker-compose.server.yml  server — dipakai BERSAMA compose server Langflow
compose.langflow-lokal.yml override Langflow lokal: rujukan wilayah dari SeaweedFS lokal
```

> **Branch terpisah, dua checkout.** Service ini di-push sebagai branch
> tersendiri di repo inocts, terpisah dari branch Langflow, dan tidak mengubah
> satu pun berkas di sana demi service. Saat jalan ia tetap memuat `lib/` dan
> `components/` dari checkout `langflow-synchrono` di sebelahnya — lihat
> DEPLOY.md. Konverter jalur B satu-satunya yang disalin (lihat di atas).
