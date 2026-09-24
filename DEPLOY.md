# Deploy Langflow Synchrono ke server (172.16.12.98)

Panduan menaikkan engine grading ke server, memakai SeaweedFS dan PostgreSQL
yang sudah ada di server. Branch: **`deploy-development`**.

Ringkasnya: yang berubah dari pengembangan lokal hanyalah **ke mana engine
menyambung** — SeaweedFS dan PostgreSQL server, bukan yang lokal. Logika
grading tidak disentuh.

---

## 0. Perintah — naikkan & redeploy

Dua server, **satu berkas compose**. Yang berbeda cuma isi `.env`.

### 0.1 Sekali saja, per server

```bash
cd langflow-synchrono/infra
cp .env.server.example .env
```

Lalu sunting `.env`:

```bash
# ── SERVER LAMA (172.16.12.98) — SeaweedFS & PostgreSQL dipasang di host ──
PG_HOST=172.16.12.98
PG_PORT=5430
PG_PASSWORD=<isi>
S3_ENDPOINT=172.16.12.98:8333
S3_RELAY_TARGET=172.16.12.98:8333
LANGFLOW_SUPERUSER_PASSWORD=<ganti>

# ── SERVER BARU (192.168.2.107) — keduanya container di docker-compose.infra.yml ──
PG_HOST=192.168.2.107
PG_PORT=5430
PG_PASSWORD=<isi>
S3_ENDPOINT=192.168.2.107:8355
S3_RELAY_TARGET=192.168.2.107:8355
LANGFLOW_SUPERUSER_PASSWORD=<ganti>
```

`S3_ENDPOINT` dan `S3_RELAY_TARGET` isinya sama; keduanya ada karena compose
tidak bisa memakai satu variabel sebagai bawaan variabel lain. Yang pertama
dipakai engine grading (lewat host), yang kedua oleh penerus untuk konverter
(yang tidak punya rute ke host). Lihat §7c.

**Server baru saja:** infrastrukturnya dinyalakan lebih dulu, sekali.

```bash
docker compose -f docker-compose.infra.yml --env-file .env up -d
```

### 0.2 Menaikkan

```bash
cd langflow-synchrono/infra

# Jalur A + .sql. Ini yang dipakai sehari-hari.
docker compose -f docker-compose.server.yml --env-file .env up -d --build

# Tambah .mdf (SQL Server — 2,34 GB di disk, ~2 GB memori selama hidup)
docker compose -f docker-compose.server.yml --env-file .env --profile mssql up -d --build

# Tambah .dmp (Oracle — 2,84 GB unduhan, paling berat)
docker compose -f docker-compose.server.yml --env-file .env --profile oracle up -d --build
```

Profilnya bertumpuk: pakai `--profile mssql --profile oracle` untuk keduanya.
Tanpa profilnya, format itu gagal dengan pesan yang menyebut layanannya belum
ada — bukan gagal diam-diam.

### 0.3 REDEPLOY sesudah mengubah kode

Ini bagian yang paling mudah keliru, dan urutannya menentukan.

```bash
cd langflow-synchrono
git pull
cd infra

# 1. Bangun ulang image yang berubah
docker compose -f docker-compose.server.yml --env-file .env build

# 2. Naikkan ulang
docker compose -f docker-compose.server.yml --env-file .env up -d

# 3. WAJIB kalau ada perubahan di lib/ — walaupun containernya "sudah jalan"
docker compose -f docker-compose.server.yml --env-file .env restart langflow
```

**Kenapa langkah 3 wajib.** Berkas di `lib/` di-bind-mount, jadi `up -d` sering
menganggap tidak ada yang berubah dan container tidak disentuh. Padahal proses
Langflow memuat modul `lib/` **sekali saat menyala** lalu menyimpannya di
memori. Tanpa restart, kode lama tetap yang berjalan.

Ini sudah terbukti mahal: pada pengujian lewat API, `.mdf` dan `.dmp` dikirim ke
konverter PostgreSQL karena Langflow masih memegang `lib/_konversi.py` versi
lama — dan pesan gagalnya menyesatkan ke arah yang sama sekali berbeda.

**Kalau yang berubah ada di `components/`**, restart saja TIDAK cukup: Langflow
menyimpan salinan kode komponen di dalam flow. Flow-nya harus dibangun ulang
(§5).

### 0.4 Memeriksa sesudah naik

```bash
# a. Engine hidup
curl -s http://localhost:7860/health_check

# b. Konverter hidup
docker exec synchrono-langflow python -c   "import urllib.request,json; print(json.load(urllib.request.urlopen('http://konverter:8390/sehat')))"
# -> {'ok': True, 'mesin': 'postgresql', 'antre': 0, 'maxConcurrent': 1}

# c. Konverter TIDAK punya jalan keluar — harus GAGAL
docker exec synchrono-konverter python -c   "import socket; socket.create_connection(('1.1.1.1',53),timeout=4)"
```

Pemeriksaan (c) sama pentingnya dengan (b). Kalau ia berhasil, jaringannya
salah dan seluruh alasan konverter dipisah jadi batal.

### 0.5 Uji tujuh bentuk

Data ujinya sudah disiapkan: `test-data-csv/uji-ae/uji_format.*` — tujuh bentuk
dari baris yang sama persis, lengkap dengan tabel umpan dan berkas yang memang
harus ditolak. Cara menjalankan dan hasil yang diharapkan ada di
`test-data-csv/uji-ae/UJI_FORMAT.md`.

Harapannya: **enam COMPLETED grade A skor 100, `.xls` FAILED.**

### 0.6 Menurunkan

```bash
# Berhenti tanpa menghapus data
docker compose -f docker-compose.server.yml --env-file .env --profile mssql --profile oracle stop

# Matikan mesin berat saja, sisanya tetap jalan
docker compose -f docker-compose.server.yml --env-file .env stop konverter-oracle
```

Jangan `down -v` kecuali memang ingin membuang volume Langflow — di dalamnya
ada flow dan API key.

---

## 1. Yang sudah dipastikan di server

Diperiksa langsung sebelum panduan ini ditulis:

| | Status |
|---|---|
| SeaweedFS `172.16.12.98:8333` (ADMIN / Password1234) | hidup |
| `s3://syncrono-master/wilayah/master_wilayah_nik.parquet` | ada, 7.265 kecamatan |
| Bucket `syncrono-uploads`, `syncrono-exports` | ada, masih kosong |
| PostgreSQL `172.16.12.98:**5430**` (root / root), db `synchrono` | hidup, tapi **KOSONG — nol tabel** |
| Langflow `:7860` | belum ada — ini yang dinaikkan |

**Port PostgreSQL-nya 5430, bukan 5432**, dan usernya `root`, bukan `postgres`.
Mudah keliru karena 5432 adalah yang lazim.

**Database `synchrono` ada dan bisa disambung, tapi isinya kosong** — belum ada
satu pun tabel di schema `public`. Itu normal untuk server baru: skemanya dibuat
oleh langkah 4. Selama langkah itu belum dijalankan, semua endpoint balas 500
karena `grading_jobs` dan `grade_criteria` tidak ada.

Tabel `master` juga akan kosong sesudahnya. Grading **tidak** membutuhkannya —
yang terpengaruh hanya lapis ke-4 pengenalan kolom (kamus master), yang otomatis
dilewati kalau master kosong. Matching baru butuh master terisi.

---

## 2. Berkas yang perlu dinaikkan — "cukup kode yang perlu"

Salin **hanya** ini ke server (mis. ke `/opt/synchrono/langflow-synchrono/`):

```
lib/                    SEMUA *.py          (logika bersama, wajib)
components/             grading/ config/ matching/   (node API)
konverter/              SEMUA *.py          (jalur B — .sql/.mdf/.dmp)
infra/
    Dockerfile.langflow
    Dockerfile.konverter                konverter-nyalakan.sh
    Dockerfile.konverter-mssql          konverter-mssql-nyalakan.sh
    Dockerfile.konverter-oracle         konverter-oracle-nyalakan.sh
    docker-compose.server.yml
    docker-compose.infra.yml    (HANYA server baru — PG & SeaweedFS di compose)
    .env.server.example        -> disalin jadi .env
    migrate.py seed.py
    db/migrasi/*.sql            (perubahan bentuk basis data)
    db/seeder/*                 (data awal)
    matching_queries.json       (hanya kalau matching dipakai)
    flow_util.py
    buat_flow_grading.py buat_flow_config.py
    buat_flow.py                (hanya kalau matching dipakai)
```

`konverter/` dan ketiga Dockerfile-nya boleh ikut walaupun jalur B belum
dipakai: tanpa `--profile`, container `.mdf`/`.dmp` tidak dinyalakan sama
sekali. Yang TIDAK boleh ketinggalan adalah `konverter-*-nyalakan.sh` — ketiga
image memakainya sebagai entrypoint, dan build-nya gagal kalau berkasnya tidak
ada.

`docker-compose.infra.yml` hanya relevan di server baru, tempat PostgreSQL dan
SeaweedFS ikut dijalankan sebagai container. Di server lama keduanya sudah
terpasang di host.

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
  lib/ components/ konverter/ \
  infra/Dockerfile.langflow \
  infra/Dockerfile.konverter infra/konverter-nyalakan.sh \
  infra/Dockerfile.konverter-mssql infra/konverter-mssql-nyalakan.sh \
  infra/Dockerfile.konverter-oracle infra/konverter-oracle-nyalakan.sh \
  infra/docker-compose.server.yml infra/docker-compose.infra.yml \
  infra/.env.server.example infra/migrate.py infra/seed.py \
  infra/db/ \
  infra/matching_queries.json infra/flow_util.py \
  infra/buat_flow_grading.py infra/buat_flow_config.py infra/buat_flow.py \
  user@172.16.12.98:/opt/synchrono/langflow-synchrono/
```

Untuk server baru, ganti host tujuannya jadi `192.168.2.107`. Isi berkasnya
sama — yang membedakan cuma `.env`.

---

## 3. Menyalakan

Di server, dari folder `infra/`:

```bash
cp .env.server.example .env
# WAJIB: isi PG_PASSWORD di .env. Ganti juga LANGFLOW_SUPERUSER_PASSWORD.
nano .env

docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

**Migrasi dan seeding berjalan otomatis** lewat service `skema`, yang jalan
lebih dulu dan ditunggu Langflow sampai selesai. Ia menunggu PostgreSQL siap
(12 kali, jeda 5 detik).

Keduanya aman diulang tiap `up`, dan itu bukan kebetulan:

* **`migrate.py`** mengubah *bentuk* basis data. Tiap berkas migrasi dicatat di
  `schema_migrations` dan tidak pernah dijalankan dua kali.
* **`seed.py`** mengisi *data awal* dengan `ON CONFLICT DO NOTHING` — mengisi
  yang belum ada, tidak pernah menimpa yang sudah ada.

Jadi ambang yang sudah kamu setel lewat API config **tidak akan kembali ke nilai
bawaan** saat deploy berikutnya. Sudah diuji: nilai yang diubah tetap bertahan
setelah migrate + seed dijalankan ulang.

Kalau `skema` gagal — misalnya kata sandi salah — **Langflow sengaja tidak ikut
menyala**. Itu disengaja: lebih baik gagal terang-terangan daripada menyala
dengan seluruh endpoint balas 500. Lihat sebabnya di:

```bash
docker compose -f docker-compose.server.yml logs skema
```

Cek Langflow hidup:

```bash
curl -s http://localhost:7860/health_check    # harus 200
docker compose -f docker-compose.server.yml logs langflow --tail 30
```

---

### Mengulang deploy sesudah `git pull`

```bash
cd ~/development/synchrono-langflow/langflow-synchrono/infra

# 1. matikan yang lama
docker compose -f docker-compose.server.yml --env-file .env down

# 2. bangun ulang image + naikkan (migrasi & seeding jalan sendiri)
docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

`--build` WAJIB kalau `Dockerfile.langflow` ikut berubah; tanpa itu Docker
memakai image lama dan perubahannya tidak berlaku.

#### 3. Kalau yang berubah ada di `components/`, BANGUN ULANG FLOW-nya

```bash
docker compose -f docker-compose.server.yml --env-file .env \
  exec langflow sh -c "cd /synchrono/infra && python buat_flow_grading.py && python buat_flow_config.py"
```

**Langkah ini mudah terlewat dan kegagalannya tidak bersuara.** Langflow tidak
menjalankan berkas di `components/`; ia menjalankan **salinan kode yang
tersimpan di dalam flow** saat flow itu dibangun. Jadi `git pull` lalu restart
akan tetap menjalankan kode LAMA: tidak ada galat, tidak ada peringatan, dan
endpointnya tetap menjawab 200 dengan perilaku sebelumnya. Satu-satunya tanda
adalah perubahanmu seperti tidak berefek apa-apa.

Yang perlu diingat:

* Aman diulang. Kedua skrip idempoten, dan **node id-nya tetap** —
  `GradingStatus-3cc03` dan kawan-kawan tidak berubah, jadi portal dan koleksi
  Postman tidak perlu disentuh.
* **API key tidak ikut terhapus.** Kunci tidak disimpan di dalam flow.
* `buat_flow.py` (matching) **jangan** ikut dijalankan kecuali memang perlu:
  node id matching masih acak, dan membangunnya ulang mengubah id-nya sehingga
  pemanggilnya harus disesuaikan.
* Folder `infra` di-mount read-only di server. Itu tidak masalah — kedua skrip
  membangun flow lewat API, dan berkas contoh Postman-nya dilewati diam-diam.

Kalau yang berubah hanya `lib/`, langkah ini **tidak** perlu: folder itu
di-mount dan diimpor lewat `PYTHONPATH`, jadi restart container sudah cukup.

**`.env` tidak ikut tertimpa `git pull`** (ia di-gitignore), jadi kata sandi dan
setelanmu aman. Yang berubah hanya `.env.server.example`; bandingkan sendiri
kalau ada kunci baru di sana.

#### Kapan perlu `down -v` (menghapus volume)

`-v` menghapus volume `langflow-data` — **beserta seluruh flow dan API key**.
Hanya perlu dalam dua keadaan:

* **Sekali saja, kalau deploy pertama gagal dengan `PermissionError` pada
  `/app/langflow-data/secret_key`.** Volume itu terlanjur dibuat milik root, dan
  perbaikan di `Dockerfile.langflow` hanya berlaku untuk volume yang masih
  kosong. Tidak ada yang hilang — Langflow belum sempat menulis apa pun.
* Kalau memang ingin memulai Langflow dari nol.

```bash
docker compose -f docker-compose.server.yml --env-file .env down -v
docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

Sesudahnya flow harus dibangun ulang (langkah 5) dan API key dibuat ulang
(langkah 6) — keduanya ikut terhapus bersama volume.

**Basis data TIDAK ikut terhapus.** PostgreSQL ada di luar compose ini, jadi
`down -v` tidak menyentuhnya. Migrasi yang sudah tercatat tetap tercatat, dan
`up` berikutnya hanya melewatinya.

#### Memeriksa hasilnya

```bash
# migrasi & seeding — ini yang pertama dilihat kalau ada yang aneh
docker compose -f docker-compose.server.yml logs skema

# Langflow hidup?
curl -s http://localhost:7860/health_check
```

Kalau `skema` gagal, Langflow memang sengaja tidak menyala. Pesannya menunjuk
langsung ke `PG_HOST`/`PG_PORT`/`PG_PASSWORD` di `.env`.

---

## 4. Memeriksa basis data

Langkah 3 sudah menjalankan migrasi dan seeding. Ini hanya untuk memastikan:

```bash
# migrasi mana yang sudah diterapkan
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/migrate.py --status

# jalankan ulang keduanya secara manual (aman diulang)
docker compose -f docker-compose.server.yml run --rm skema
```

Pada pemasangan baru, isi tabelnya harus seperti ini:

```
   ref_grades          6      grade_rules         6
   ref_process         2      grade_bands         6
   ref_sync_statuses   3      grade_criteria      4
   ref_match_results   5      matching_queries    5
```

`grading_jobs` kosong itu benar — ia terisi saat job pertama masuk. `master`
kosong juga benar; grading tidak membutuhkannya.

### Menambah perubahan skema nanti

Jangan menyunting berkas migrasi yang sudah pernah diterapkan. `migrate.py`
membandingkan checksum dan akan memperingatkan, tapi sengaja tidak
menjalankannya ulang. Buat berkas baru bernomor lebih besar:

```
infra/db/migrasi/005_nama_perubahan.sql
```

`docker compose up -d` berikutnya menerapkannya sendiri.

### Mengembalikan satu tabel ke nilai bawaan

Seeder tidak pernah menimpa, jadi ini dua langkah sadar — bukan efek samping
deploy:

```bash
psql -h 172.16.12.98 -p 5430 -U root -d synchrono -c "DELETE FROM grade_rules;"
docker compose -f docker-compose.server.yml exec langflow \
    python /synchrono/infra/seed.py --hanya 002
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

Kata sandinya dibaca langsung dari `.env`, jadi tidak ada placeholder yang bisa
keliru tersalin apa adanya:

```bash
SANDI=$(grep -E '^LANGFLOW_SUPERUSER_PASSWORD=' .env | cut -d= -f2- | tr -d '"')
AKUN=$(grep -E '^LANGFLOW_SUPERUSER=' .env | cut -d= -f2- | tr -d '"')

TOKEN=$(curl -s -X POST http://localhost:7860/api/v1/login \
  -d "username=${AKUN:-admin}&password=$SANDI" | \
  python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:7860/api/v1/api_key/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"synchrono-portal"}'
```

Nilai `api_key` **hanya muncul sekali**. Portal memakainya di header `x-api-key`.

> Kalau muncul `KeyError: 'access_token'` lalu
> `{"detail":"No authentication credentials provided"}`, artinya login gagal dan
> `$TOKEN` kosong — hampir selalu karena kata sandinya salah, bukan karena
> pembuatan key-nya bermasalah. Periksa dengan:
>
> ```bash
> curl -s -X POST http://localhost:7860/api/v1/login \
>   -d "username=admin&password=$SANDI"
> ```

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

## 7b. Engine menerima CSV, bukan hanya parquet

Jalur yang dianjurkan tetap **parquet**: tipe kolomnya tersimpan, ukurannya jauh
lebih kecil, dan pembacaannya jauh lebih cepat pada berkas ratusan ribu baris.

Tapi engine tidak lagi menolak CSV. Kalau `parquetKey` (atau `csvKey`) menunjuk
berkas berakhiran `.csv`, `.tsv`, atau `.txt`, ia dibaca langsung — jadi satu
langkah konversi yang belum jadi di portal tidak memblokir seluruh grading.

```json
{ "fileId": "pop_...", "s3Bucket": "syncrono-uploads",
  "parquetKey": "uploads/pop_.../raw/data.csv" }
```

`csvKey` saja juga cukup; kalau `parquetKey` kosong, `csvKey` yang dipakai.

Diuji: kelima berkas uji A-E menghasilkan grade dan skor yang **sama persis**
dibaca sebagai CSV maupun sebagai parquet.

Yang ditangani sendiri oleh pembacanya: BOM dari Excel dibuang, pemisah titik
koma terdeteksi, dan seluruh kolom dibaca sebagai teks supaya NIK tidak berubah
jadi angka dan normalisasi tanggal tidak terlewat.

---

## 7c. Jalur B — unggahan `.sql`, `.mdf`, `.dmp`

Tiga format ini tidak dibaca melainkan **dipulihkan** ke mesin basis datanya.
Itu dikerjakan container terpisah, dan satu-satunya yang perlu diatur berbeda
antar server adalah **satu baris di `.env`**.

### Kenapa ada penerus S3, dan kenapa ia yang membuat ini fleksibel

Layanan konversi tinggal di jaringan `dalam` yang `internal: true`. Jaringan
seperti itu tidak punya rute ke mana pun selain container sejaring — diukur
dari dalam containernya:

```
container sejaring     TERJANGKAU
gateway jaringannya    GAGAL (TimeoutError)
host.docker.internal   GAGAL (gaierror)
internet               GAGAL (OSError)
```

Itu persis yang diinginkan: berkas tidak tepercaya yang dipulihkannya tidak
punya jalan mengirim apa pun keluar. Tapi konverter TETAP harus mengambil
berkasnya dari S3 — dan S3 di kedua server dijangkau lewat **IP host**, bukan
nama container:

| | SeaweedFS & PostgreSQL backend |
|---|---|
| Server lama `172.16.12.98` | dipasang di host, di luar Docker |
| Server baru `192.168.2.107` | container, di `docker-compose.infra.yml` (compose lain) |

Dari sisi jaringan Docker keduanya sama saja: **di luar jangkauan**. Karena itu
ada `s3-relay` — satu-satunya container yang berkaki di kedua jaringan,
meneruskan **satu port TCP ke satu alamat**. Konverter tetap tanpa jalan keluar;
yang bisa dicapainya cuma alamat yang ditulis di `.env`.

Penerusan **TCP mentah**, bukan proxy HTTP: tanda tangan SigV4 dihitung atas
header `Host` yang dikirim klien dan diverifikasi ulang SeaweedFS dari header
yang diterimanya. Proxy yang menulis ulang `Host` akan membatalkannya; socat
tidak menyentuh apa pun. Sudah diuji — unduh berkas maupun unggah parquet
lolos lewat penerus.

### Yang diisi di `.env`

```bash
# Server lama
S3_ENDPOINT=172.16.12.98:8333
S3_RELAY_TARGET=172.16.12.98:8333

# Server baru
S3_ENDPOINT=192.168.2.107:8355
S3_RELAY_TARGET=192.168.2.107:8355
```

Keduanya diisi terpisah karena compose tidak bisa memakai satu variabel sebagai
nilai bawaan variabel lain. Isinya hampir selalu sama.

### Menyalakan

```bash
# .sql saja (PostgreSQL) — paling ringan, cukup untuk sebagian besar kebutuhan
docker compose -f docker-compose.server.yml --env-file .env up -d --build

# tambah .mdf (SQL Server, image 2,34 GB di disk)
docker compose -f docker-compose.server.yml --env-file .env --profile mssql up -d --build

# tambah .dmp (Oracle, image 2,84 GB unduhan)
docker compose -f docker-compose.server.yml --env-file .env --profile oracle up -d --build
```

`.mdf` dan `.dmp` ada di balik **profil** dengan sengaja: mesinnya memegang
memori sepanjang container hidup, dipakai atau tidak. Selama belum ada instansi
yang benar-benar mengirim format itu, tidak ada alasan menyalakannya.

Tanpa profilnya, `.mdf`/`.dmp` gagal dengan pesan yang menyebutkan layanannya
tidak bisa dihubungi — bukan gagal diam-diam.

### Memeriksa

```bash
# dari dalam container langflow
docker exec synchrono-langflow python -c   "import urllib.request,json; print(json.load(urllib.request.urlopen('http://konverter:8390/sehat')))"
# -> {'ok': True, 'mesin': 'postgresql', 'antre': 0, 'maxConcurrent': 1}

# dan pastikan konverter TIDAK punya jalan keluar
docker exec synchrono-konverter python -c   "import socket; socket.create_connection(('1.1.1.1',53),timeout=4)"
# -> harus GAGAL. Kalau berhasil, jaringannya salah.
```

Pemeriksaan kedua sama pentingnya dengan yang pertama.

### Sesudah mengubah `lib/`

**Restart container Langflow.** Modul di `lib/` dimuat sekali saat proses
Langflow menyala dan disimpan di memori; mengubah berkasnya saja tidak
berpengaruh. Ini terbukti mahal: pada pengujian lewat API, `.mdf` dan `.dmp`
sempat dikirim ke konverter PostgreSQL karena proses Langflow masih memegang
versi lama `lib/_konversi.py`, dan pesan gagalnya menyesatkan.

Perubahan di `components/` lebih keras lagi: Langflow menyimpan **salinan kode
komponen di dalam flow**, jadi flow-nya harus dibangun ulang (§5).

---

## 8. Kalau bermasalah

| Gejala | Sebab paling sering |
|---|---|
| `PermissionError … /app/langflow-data/secret_key` | Named volume dibuat root, Langflow jalan sebagai uid 1000 — lihat di bawah |
| Semua endpoint 500 | Kata sandi PG salah/kosong di `.env`. Cek `logs \| grep -i postgres` |
| `NoSuchBucket` | `syncrono-uploads` belum ada, atau `parquetKey` salah |
| Grade jatuh ke E untuk data bagus | `WILAYAH_KECAMATAN_TEGAS=1` pada data dummy — set `0` |
| Flow hilang setelah recreate | `LANGFLOW_DATABASE_URL` tidak ke volume — sudah benar di compose ini |
| `Could not resolve host seaweedfs` | Container tidak bisa jangkau `172.16.12.98:8333` — cek jaringan server |

### PermissionError pada `/app/langflow-data/secret_key`

Muncul di server Linux, tidak di Docker Desktop. Image Langflow berjalan sebagai
user non-root (uid 1000), tapi Docker membuat named volume sebagai `root:root`,
sehingga Langflow tak bisa menulis ke `/app/langflow-data`.

`Dockerfile.langflow` sudah membuat folder itu dengan kepemilikan yang benar
(uid 1000, grup 0, group-writable) supaya **volume baru yang masih kosong**
mewarisinya. Kalau volume terlanjur dibuat root pada percobaan gagal, hapus dulu
lalu bangun ulang:

```bash
docker compose -f docker-compose.server.yml down -v      # -v menghapus volume kosong
docker compose -f docker-compose.server.yml --env-file .env up -d --build
```

`down -v` di compose ini hanya menghapus `langflow-data` (kosong karena Langflow
belum sempat menulis apa pun), jadi tidak ada yang hilang.

> **Catatan wilayah.** Selama data masih dummy, biarkan
> `WILAYAH_KECAMATAN_TEGAS=0`: kode kecamatan yang tak dikenal hanya dilaporkan,
> tidak menjatuhkan grade. Nyalakan `1` begitu data Dukcapil asli masuk.
