# Deploy synchrono-service-starrocks ke server

Server 192.168.2.107 menjalankan StarRocks di host yang sama, jadi service ini
sebaiknya ikut berjalan di sana: Stream Load, penarikan kandidat Pass 3, dan setiap
kueri kecil ke StarRocks menjadi lalu lintas lokal, bukan lewat VPN (±2 MB/detik
dari laptop).

Taruh foldernya **bersebelahan** dengan synchrono-service, misalnya
`~/syncrono/service/engine/synchrono-service-starrocks` (skrip benchmark mencari
`../synchrono-service`).

```bash
# dari laptop (atau lewat git, kalau folder ini sudah masuk repo)
scp -r synchrono-service-starrocks administrator@192.168.2.107:~/syncrono/service/engine/
# jangan ikut menyalin bench/.data/, bench/results/, .env, .env.bench
```

## A. Mode service (berdampingan dengan synchrono-service)

```bash
cd ~/syncrono/service/engine/synchrono-service-starrocks
cp .env.example .env
nano .env
```

| Variabel | Isi |
|---|---|
| `STARROCKS_PASSWORD` | password `root` StarRocks (Secret `starrocks-root` di cluster) |
| `STARROCKS_HOST` / `_PORT` | `192.168.2.107` / `30930` |
| `STARROCKS_STREAM_LOAD_URL` | `http://192.168.2.107:30888` — **port BE**, bukan FE `30830` |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | sama dengan `.env` synchrono-service: `grep '^S3_' ../synchrono-service/.env` |
| `SERVICE_API_KEY` | kunci `x-api-key` (boleh sama dengan service lama, dipisah koma bila lebih dari satu) |
| `SERVICE_SUPERUSER`, `SERVICE_SUPERUSER_PASSWORD`, `SERVICE_SECRET_KEY` | login `/api/v1/login` untuk membuat API key — salin dari `.env` service lama |
| `NORMALISASI_AI*`, `OLLAMA_API_KEY` | AI pengenalan kolom — salin dari `.env` service lama |
| `REASONING_AI_*` | AI penghalusan reasoning — salin dari `.env` service lama |
| `WILAYAH_PARQUET`, `WILAYAH_KECAMATAN_TEGAS` | rujukan wilayah NIK (bawaan sama dengan service lama) |
| `KONVERTER_*_URL` | layanan konversi dump `.sql/.mdf/.dmp` (bila dipasang) |

Cara cepat menyalin bagian yang sama dari service lama (baris yang ditambahkan belakangan menimpa yang di atasnya):

```bash
grep -E '^(S3_|SERVICE_API_KEY|SERVICE_SUPERUSER|SERVICE_SECRET_KEY|NORMALISASI_AI|OLLAMA_|REASONING_AI_|WILAYAH_|KONVERTER_)' ../synchrono-service/.env >> .env
```

```bash
docker compose up -d --build      # valkey, schema (sekali jalan), api, worker-grading, worker-matching
curl -s localhost:7870/health
curl -s -H "x-api-key: <kunci>" localhost:7870/health/db
```

- Port API `7870` (ubah lewat `API_PORT` di `.env`). Service lama tetap di `7860`.
- Skema StarRocks diterapkan otomatis oleh container `schema` dan aman diulang.
  Database `synchrono_service`, `synchrono_kl`, `synchrono_portal`, `synchrono_master`
  sudah ada di cluster ini (dibuat saat pengujian 2 Okt 2026), termasuk master
  `um-master` (2 juta baris).
- Memuat master lain:
  `docker compose run --rm api python tools/load_master.py <masterId> s3://<bucket>/<key>`
- **Catatan:** hasil matching versi ini disimpan di `synchrono_portal` (StarRocks),
  bukan di PostgreSQL portal. Portal yang sekarang tidak akan menampilkannya.

Redeploy setelah kode berubah: `docker compose up -d --build`.

## B. Mode benchmark (terisolasi dari produksi)

Menjalankan salinan **kedua** service dengan penyimpanan sendiri — SeaweedFS
`srb-s3`, PostgreSQL `srb-pg` (berisi tiruan tabel portal), service DuckDB `srb-old`,
penerima callback `srb-cb`, dan service StarRocks `srb-new-*`. Tidak menyentuh
SeaweedFS, PostgreSQL, maupun portal produksi. Hasil StarRocks tetap masuk ke
database `synchrono_*` di cluster yang sama.

Butuh: image `synchrono-service:2.0.0` (sudah ada dari deploy service lama), data uji
`test-data-csv/uji-master-ae/`, dan master `1790325476460_23223dc0_master.parquet`.

```bash
cd ~/syncrono/service/engine/synchrono-service-starrocks
export STARROCKS_PASSWORD='...'
export TEST_DATA=~/bench-data/uji-master-ae
export MASTER_PARQUET=~/bench-data/1790325476460_23223dc0_master.parquet

bash bench/stack.sh all                     # naikkan semua + unggah data uji & master ke srb-s3
bash bench/run_bench.sh old A,B,C,D,E 3     # k6 terhadap service DuckDB
bash bench/run_bench.sh new A,B,C,D,E 3     # k6 terhadap service StarRocks
python3 bench/summarize.py                  # ringkasan -> bench/results/summary.json
```

- Kedua service diuji **bergantian**: `run_bench.sh` menghentikan yang satu sebelum
  menguji yang lain.
- Port bawaan `58101` (DuckDB) dan `58102` (StarRocks); ubah lewat `OLD_PORT`/`NEW_PORT`
  kalau terpakai.
- Benchmark memakai CPU & memori host yang sama dengan produksi dan StarRocks —
  jalankan di luar jam sibuk.
- Bersihkan semuanya: `bash bench/stack.sh clean`.
