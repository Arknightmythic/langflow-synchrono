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
| `UDF_JAR_URL` | `http://192.168.2.107:7870/udf/synchrono-udf.jar` — alamat jar UDF yang bisa dijangkau StarRocks; kosong = skor Pass 3 di DuckDB |

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
- `stack.sh new` mengisi `UDF_JAR_URL` dengan IP host (`hostname -I`); tetapkan sendiri
  dengan `UDF_HOST=192.168.2.107` bila IP pertama host bukan alamat yang dijangkau StarRocks.
- Bersihkan semuanya: `bash bench/stack.sh clean`.

## C. Benchmark di server kantor (serverai, 172.16.12.98)

StarRocks kantor (4.0.8, 24 core, ±70 GB untuk BE) dipakai setiap hari; database
`synchrono` di sana milik aplikasi lain. Uji ini hanya membuat dan memakai
`synchrono_service`, `synchrono_kl`, `synchrono_portal`, `synchrono_master`.

### 1. Cara StarRocks dipasang

Langsung di host, `/opt/starrocks/fe` dan `/opt/starrocks/be`, dijalankan root.
Saat boot, `starrocks.service` (oneshot) menjalankan `start_fe.sh --daemon` lalu
`start_be.sh --daemon`. BE sudah memuat JVM (`libjvm.so`, folder `udf`,
`udf-runtime`, `jni-packages`), jadi Java UDF hanya perlu diizinkan di FE.
Unit `starrocks-fe.service` / `starrocks-be.service` tidak aktif — **jangan
dinyalakan** (`Restart=always`, akan membuat FE/BE dobel).

### 2. Nyalakan Java UDF (sekali)

`enable_udf` tidak bisa diubah saat jalan: FE harus di-restart. Selama ±1 menit
aplikasi lain tidak bisa query; data tidak terpengaruh dan BE tidak di-restart.
Lakukan di luar jam sibuk. Tanpa UDF, service tetap benar tetapi skor Pass 3
dihitung di DuckDB (kandidat ditarik keluar dari StarRocks — lambat untuk C/D/E).

```bash
CONF=/opt/starrocks/fe/conf/fe.conf
cp $CONF $CONF.bak-$(date +%F)
if grep -q '^[[:space:]]*enable_udf' $CONF; then
  sed -i 's/^[[:space:]]*enable_udf.*/enable_udf = true/' $CONF
else
  printf '\nenable_udf = true\n' >> $CONF
fi
grep -n 'enable_udf' $CONF

# restart FE saja, dengan perintah yang sama dengan starrocks.service
cd /opt/starrocks/fe/bin
./stop_fe.sh
sleep 5
ps -ef | grep StarRocksFE | grep -v grep     # harus kosong sebelum start
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
./start_fe.sh --daemon

sleep 40
ps -ef | grep StarRocksFE | grep -v grep
ss -ltn | grep -E ':(9030|8030|9010)\b'
tail -n 20 /opt/starrocks/fe/log/fe.warn.log
```

Bila FE tidak naik: kembalikan `cp $CONF.bak-<tanggal> $CONF`, lalu jalankan lagi
`./start_fe.sh --daemon`.

### 3. Kode, image, dan data uji

```bash
mkdir -p /opt/synchrono-uji/bench-data && cd /opt/synchrono-uji
git clone -b service-master https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service
git clone -b service-starrocks https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service-starrocks
cd synchrono-service && docker build -t synchrono-service:2.0.0 . && cd ..
ss -ltn | grep -E ':(58101|58102)\b'      # kosong = port bebas; bila terpakai, export OLD_PORT/NEW_PORT
```

Dari laptop (Git Bash), salin data uji dan master 2 juta:

```bash
scp -r /d/ISGS/PROJECT/synchrono/test-data-csv/uji-master-ae root@172.16.12.98:/opt/synchrono-uji/bench-data/
scp /d/ISGS/PROJECT/synchrono/1790325476460_23223dc0_master.parquet root@172.16.12.98:/opt/synchrono-uji/bench-data/
```

### 4. Jalankan

Semua di satu sesi shell (variabel dipakai ulang oleh `run_bench.sh`):

```bash
cd /opt/synchrono-uji/synchrono-service-starrocks
read -rsp 'Password root StarRocks: ' STARROCKS_PASSWORD; echo; export STARROCKS_PASSWORD
export STARROCKS_HOST=172.16.12.98 STARROCKS_PORT=9030 STARROCKS_USER=root
export STARROCKS_STREAM_LOAD_URL=http://172.16.12.98:8040   # BE langsung: BE terdaftar sebagai 127.0.0.1
export UDF_HOST=172.16.12.98
export TEST_DATA=/opt/synchrono-uji/bench-data/uji-master-ae
export MASTER_PARQUET=/opt/synchrono-uji/bench-data/1790325476460_23223dc0_master.parquet

bash bench/stack.sh all       # S3, Postgres, harness, service DuckDB & StarRocks, unggah data uji
bash bench/stack.sh master    # master 2 juta -> synchrono_master (um-master)

# kesetaraan baris per baris dengan service DuckDB
P="docker run --rm --network synchrono-shared --env-file .env.bench -e WORK_DIR=/work -e DUCKDB_TEMP_DIR=/work/spill -v $PWD:/srv:ro -v $PWD/bench/.data/work-new:/work -w /srv synchrono-service-starrocks:dev python"
$P bench/parity_grading.py
$P bench/parity_matching.py A B C D E

# kecepatan, bergantian
bash bench/run_bench.sh new A,B,C,D,E 3
bash bench/run_bench.sh old A,B,C,D,E 3
python3 bench/summarize.py
```

- UDF terpakai bila callback matching memuat `candidatePullMs: 0`; bila gagal,
  `docker logs srb-new-worker-matching 2>&1 | grep UDF` menyebut sebabnya.
- Selesai: `bash bench/stack.sh clean` (kontainer uji saja; database `synchrono_*` di
  StarRocks tetap ada).
