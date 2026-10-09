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
  Database `syncrono_starrocks` (tabel service, K/L, portal) dan `syncrono_master`
  sudah ada di cluster ini (dibuat saat pengujian 2 Okt 2026), termasuk master
  `um-master` (2 juta baris).
- **Bila service lama masih berjalan** (DB `syncrono_service/_kl/_portal`): ia memakai
  `syncrono_master` yang sama. Sejak 9 Okt 2026, `syncrono_master.dictionary` di 107
  sudah punya kolom audit. Kode lama (sebelum commit 2e537c2) mengosongkan tabel itu lalu
  mengisinya tanpa daftar kolom, sehingga muat master lewat service lama akan gagal dan
  meninggalkan kamus kosong. Muat master hanya lewat versi ini, atau matikan service lama.
- Semua kolom waktu berisi WIB tanpa zona. Portal juga harus menulis `created_at`,
  `updated_at`, dan `reviewed_at` dalam WIB.
- Deploy versi ini ke cluster yang masih memakai skema 9 Okt 2026 pagi (ada `created_date`)
  sekaligus memigrasi skemanya: kolom waktu digeser ke WIB satu kali, dan `created_date`
  menjadi `created_at`. Kode lama yang menulis `created_date` berhenti bekerja setelah migrasi,
  jadi pasang kode dan skema bersamaan.
- Memuat master lain:
  `docker compose run --rm api python tools/load_master.py <masterId> s3://<bucket>/<key>`
- **Catatan:** hasil matching versi ini disimpan di `syncrono_starrocks.syncrono_portal_matching_results` (StarRocks),
  bukan di PostgreSQL portal. Portal yang sekarang tidak akan menampilkannya.

Redeploy setelah kode berubah: `docker compose up -d --build`.

## B. Mode benchmark (terisolasi dari produksi)

Menjalankan salinan **kedua** service dengan penyimpanan sendiri — SeaweedFS
`srb-s3`, PostgreSQL `srb-pg` (berisi tiruan tabel portal), service DuckDB `srb-old`,
penerima callback `srb-cb`, dan service StarRocks `srb-new-*`. Tidak menyentuh
SeaweedFS, PostgreSQL, maupun portal produksi. Hasil StarRocks tetap masuk ke
database `syncrono_starrocks` dan `syncrono_master` di cluster yang sama.

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
`syncrono_starrocks` dan `syncrono_master`.

### 1. Cara StarRocks dipasang

Langsung di host, `/opt/starrocks/fe` dan `/opt/starrocks/be`, dijalankan root.
Saat boot, `starrocks.service` (oneshot) menjalankan `start_fe.sh --daemon` lalu
`start_be.sh --daemon` tanpa `JAVA_HOME`. Akibatnya BE memakai `libjvm.so` pengganti
di `be/lib` dan semua fitur Java-nya, termasuk UDF, mati ("env 'JAVA_HOME' is not
set"). Java UDF butuh dua hal: diizinkan di FE (langkah 2) dan `JAVA_HOME` di BE
(langkah 2b). Unit `starrocks-fe.service` / `starrocks-be.service` tidak aktif —
**jangan dinyalakan** (`Restart=always`, akan membuat FE/BE dobel).

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
# FE jalan di Java 21: CREATE FUNCTION memasang SecurityManager, yang sejak Java 18
# harus diizinkan; tanpa ini muncul "The Security Manager is deprecated ..."
grep -q 'java.security.manager=allow' $CONF || \
  sed -i '/^JAVA_OPTS.*udf_security\.policy/ s|udf_security\.policy|udf_security.policy -Djava.security.manager=allow|' $CONF
grep -n '^JAVA_OPTS' $CONF

# restart FE saja, dengan perintah yang sama dengan starrocks.service
cd /opt/starrocks/fe/bin
./stop_fe.sh
sleep 5
ps -ef | grep StarRocksFE | grep -v grep     # harus kosong sebelum start
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
./start_fe.sh --daemon

sleep 40
ps -ef | grep StarRocksFE | grep -v grep | grep -o 'java.security.manager=allow'
ss -ltn | grep -E ':(9030|8030|9010)\b'
tail -n 20 /opt/starrocks/fe/log/fe.warn.log
```

### 2b. `JAVA_HOME` untuk BE (sekali; BE di-restart)

Selama BE restart (±1 menit) semua kueri dan load gagal; data aman. Lakukan di luar
jam sibuk. `JAVA_HOME` di `be.conf` ikut terbaca saat boot oleh `starrocks.service`.

```bash
BECONF=/opt/starrocks/be/conf/be.conf
cp $BECONF $BECONF.bak-$(date +%F)
grep -q '^[[:space:]]*JAVA_HOME' $BECONF || printf '\nJAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64\n' >> $BECONF
grep -n 'JAVA_HOME' $BECONF

cd /opt/starrocks/be/bin
./stop_be.sh
sleep 10
ps -ef | grep 'lib/starrocks_be' | grep -v grep    # harus kosong sebelum start
./start_be.sh --daemon

sleep 30
BEPID=$(pgrep -f 'lib/starrocks_be' | head -1)
tr '\0' '\n' < /proc/$BEPID/environ | grep JAVA_HOME
grep -m1 libjvm /proc/$BEPID/maps                   # harus dari /usr/lib/jvm/..., bukan be/lib
ss -ltn | grep -E ':(8040|9060|9050|8060)\b'
```

Fungsi UDF dibuat otomatis oleh service pada job matching pertama
(`SHOW FUNCTIONS FROM syncrono_starrocks`). Bila gagal, alasannya ada di
`docker logs srb-new-worker-matching 2>&1 | grep -i udf`; worker mencoba lagi setelah 5 menit.

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
bash bench/stack.sh master    # master 2 juta -> syncrono_master (um-master)

# kesetaraan baris per baris dengan service DuckDB
P="docker run --rm --network synchrono-shared --env-file .env.bench -e WORK_DIR=/work -e DUCKDB_TEMP_DIR=/work/spill -v $PWD:/srv:ro -v $PWD/bench/.data/work-new:/work -w /srv synchrono-service-starrocks:dev python"
$P bench/parity_grading.py
$P bench/parity_matching.py A B C D E

# kecepatan, bergantian
bash bench/run_bench.sh new A,B,C,D,E 3
bash bench/run_bench.sh old A,B,C,D,E 3
python3 bench/summarize.py
```

- Batas memori per kueri untuk service ini saja (aplikasi lain di cluster tidak
  terpengaruh): `export STARROCKS_QUERY_MEM_LIMIT=16GB` sebelum `stack.sh new` /
  `run_bench.sh new`. Melewati batas → spill ke disk, bukan langsung gagal.
- UDF terpakai bila callback matching memuat `candidatePullMs: 0`; bila gagal,
  `docker logs srb-new-worker-matching 2>&1 | grep UDF` menyebut sebabnya.
- Selesai: `bash bench/stack.sh clean` (kontainer uji saja; database `syncrono_starrocks`
  dan `syncrono_master` di StarRocks tetap ada).

## D. Uji setara di server kosong (ai-master-db, 172.16.13.158)

Kedua versi diuji bergantian di server yang sama (16 vCPU, 31 GB RAM): fase 1 DuckDB,
server dibersihkan, lalu fase 2 StarRocks. Data, skrip, dan jumlah putaran sama
(master 2, 20, dan 30 juta: 3 putaran; 100 juta: 1 putaran). Memori memakai bawaan mesin datanya,
sama-sama 25 GB (±80% RAM): DuckDB `OLD_MEMORY=25GB`; BE StarRocks `mem_limit = 27777777778`,
karena BE hanya memakai 90% dari `mem_limit` (`MemLimit` di `SHOW BACKENDS` = 23.283GB = 25 GB).
`run_bench.sh new` mencatat proses FE/BE di host sebagai `sr-fe`/`sr-be` (core dan MB,
sama dengan kontainer). `total` di ringkasan adalah jumlah semua komponen service.

### Fase 1: DuckDB

```bash
docker --version || curl -fsSL https://get.docker.com | sh
mkdir -p /opt/synchrono-uji/bench-data && cd /opt/synchrono-uji
git clone -b service-master https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service
git clone -b service-starrocks https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service-starrocks
(cd synchrono-service && docker build -t synchrono-service:2.0.0 .)
(cd synchrono-service-starrocks && docker build -t synchrono-service-starrocks:dev .)
# laptop: scp uji-master-ae/ dan master 2 juta ke /opt/synchrono-uji/bench-data/

cd /opt/synchrono-uji/synchrono-service-starrocks
cp .env.example .env.bench && mkdir -p bench/results
export TEST_DATA=/opt/synchrono-uji/bench-data/uji-master-ae
export MASTER_PARQUET=/opt/synchrono-uji/bench-data/1790325476460_23223dc0_master.parquet
export OLD_MEMORY=25GB
bash bench/stack.sh storage && bash bench/stack.sh harness && bash bench/stack.sh old && bash bench/stack.sh data
nohup bash -c 'bash bench/run_bench.sh old A,B,C,D,E 3; python3 bench/summarize.py' > bench/results/duckdb-2m.log 2>&1 &
# selesai: cp bench/results/summary.json bench/results/summary-duckdb-2m.json

docker run --rm -v /opt/synchrono-uji/bench-data:/d -v "$PWD/bench:/bench:ro" synchrono-service:2.0.0 \
  python /bench/make_master.py /d/1790325476460_23223dc0_master.parquet /d/master-100m.parquet 100000000
export MASTER_PARQUET=/opt/synchrono-uji/bench-data/master-100m.parquet
bash bench/stack.sh data
nohup bash -c 'bash bench/run_bench.sh old A,B,C,D,E 1; python3 bench/summarize.py' > bench/results/duckdb-100m.log 2>&1 &
# selesai: cp bench/results/summary.json bench/results/summary-duckdb-100m.json

# master 20 dan 30 juta, berurutan dalam satu proses
nohup bash -c '
set -e
for n in 20 30; do
  docker run --rm -v /opt/synchrono-uji/bench-data:/d -v "$PWD/bench:/bench:ro" synchrono-service:2.0.0 \
    python /bench/make_master.py /d/1790325476460_23223dc0_master.parquet /d/master-${n}m.parquet ${n}000000
  export MASTER_PARQUET=/opt/synchrono-uji/bench-data/master-${n}m.parquet
  bash bench/stack.sh data
  bash bench/run_bench.sh old A,B,C,D,E 3
  python3 bench/summarize.py
  cp bench/results/summary.json bench/results/summary-duckdb-${n}m.json
done
echo SELESAI' > bench/results/duckdb-20m-30m.log 2>&1 &
```

Sebelum server dibersihkan, catat versi (sha256 berkas hanya sebagai catatan; lihat catatan
`content fingerprint` di fase 2), lalu salin hasilnya ke laptop:

```bash
cd /opt/synchrono-uji
{ date; nproc; free -g; df -h /opt
  git -C synchrono-service log --oneline -1
  git -C synchrono-service-starrocks log --oneline -1
  docker run --rm synchrono-service:2.0.0 python -c "import duckdb; print('duckdb', duckdb.__version__)"
  sha256sum bench-data/*.parquet
} > synchrono-service-starrocks/bench/results/fase1-info.txt 2>&1
# laptop: scp -r root@172.16.13.158:/opt/synchrono-uji/synchrono-service-starrocks/bench/results <tujuan>/duckdb
```

### Fase 2: StarRocks

Server dibersihkan lalu di-reboot, supaya memori dan cache mulai dari nol seperti fase 1.
Swap (7 GB) dibiarkan seperti fase 1, walaupun StarRocks menyarankan swap dimatikan.

**1. Paket dan StarRocks 4.0.8** (tarball yang sama dengan kantor; bila tidak bisa,
`wget https://releases.starrocks.io/starrocks/StarRocks-4.0.8-ubuntu-amd64.tar.gz`)

```bash
docker --version || curl -fsSL https://get.docker.com | sh
apt-get update && apt-get install -y openjdk-17-jdk-headless mysql-client
scp root@172.16.12.98:/root/StarRocks-4.0.8-ubuntu-amd64.tar.gz /root/
mkdir -p /opt/starrocks
tar -xzf /root/StarRocks-4.0.8-ubuntu-amd64.tar.gz -C /opt/starrocks --strip-components=1
mkdir -p /opt/starrocks/fe/meta /opt/starrocks/be/storage

cat >> /opt/starrocks/fe/conf/fe.conf <<'CONF'

# uji setara ai-master-db
JAVA_HOME = /usr/lib/jvm/java-17-openjdk-amd64
priority_networks = 172.16.13.0/24
enable_udf = true
CONF
cat >> /opt/starrocks/be/conf/be.conf <<'CONF'

# uji setara ai-master-db: BE memakai 0,9 x mem_limit = 25 GB, sama dengan DuckDB di fase 1
JAVA_HOME = /usr/lib/jvm/java-17-openjdk-amd64
priority_networks = 172.16.13.0/24
mem_limit = 27777777778
CONF

export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ulimit -n 655350
/opt/starrocks/fe/bin/start_fe.sh --daemon
sleep 40
mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW FRONTENDS\G' | grep -E ' IP:|Alive:|Version:'
mysql -h127.0.0.1 -P9030 -uroot -e 'ALTER SYSTEM ADD BACKEND "172.16.13.158:9050"'
/opt/starrocks/be/bin/start_be.sh --daemon
sleep 40
mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW BACKENDS\G' | grep -E ' IP:|Alive:|CpuCores:|MemLimit:'   # MemLimit: 23.283GB
grep -m1 libjvm /proc/$(pgrep -f lib/starrocks_be | head -1)/maps    # harus dari /usr/lib/jvm/...

# password root: huruf dan angka saja (dipakai sed di stack.sh)
read -rsp 'Password baru root StarRocks: ' STARROCKS_PASSWORD; echo; export STARROCKS_PASSWORD
mysql -h127.0.0.1 -P9030 -uroot -e "ALTER USER root IDENTIFIED BY '$STARROCKS_PASSWORD'"
export MYSQL_PWD="$STARROCKS_PASSWORD"
mysql -h127.0.0.1 -P9030 -uroot -e 'SELECT current_version()'
```

Agar FE dan BE menyala sendiri setiap server boot, pasang `starrocks.service` (oneshot, sama
dengan cara server kantor). Skripnya hanya menyalakan yang belum jalan, jadi aman dijalankan
saat FE/BE sudah hidup:

```bash
cat > /opt/starrocks/start-all.sh <<'EOS'
#!/bin/bash
# FE lalu BE host ini, masing-masing hanya bila belum jalan (dipanggil starrocks.service).
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ulimit -n 655350
pgrep -f com.starrocks.StarRocksFE >/dev/null || /opt/starrocks/fe/bin/start_fe.sh --daemon
for i in $(seq 1 90); do (echo > /dev/tcp/127.0.0.1/9030) 2>/dev/null && break; sleep 2; done
pgrep -f /opt/starrocks/be/lib/starrocks_be >/dev/null || /opt/starrocks/be/bin/start_be.sh --daemon
EOS
cat > /opt/starrocks/stop-all.sh <<'EOS'
#!/bin/bash
/opt/starrocks/be/bin/stop_be.sh
/opt/starrocks/fe/bin/stop_fe.sh
EOS
chmod +x /opt/starrocks/start-all.sh /opt/starrocks/stop-all.sh
cat > /etc/systemd/system/starrocks.service <<'EOS'
[Unit]
Description=StarRocks FE + BE (/opt/starrocks)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
LimitNOFILE=655350
ExecStart=/opt/starrocks/start-all.sh
ExecStop=/opt/starrocks/stop-all.sh
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
EOS
systemctl daemon-reload
systemctl enable --now starrocks.service
```

**2. Kode, image, dan data** (`synchrono-service` dikunci ke commit fase 1, karena
`make_master.py` harus menghasilkan master 20, 30, dan 100 juta yang sama persis)

```bash
mkdir -p /opt/synchrono-uji/bench-data && cd /opt/synchrono-uji
git clone -b service-master https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service
git clone -b service-starrocks https://github.com/Arknightmythic/langflow-synchrono.git synchrono-service-starrocks
git -C synchrono-service checkout e993bb3                  # commit fase 1 (fase1-info.txt)
(cd synchrono-service && docker build -t synchrono-service:2.0.0 .)     # hanya alat uji; srb-old tidak dinyalakan
# laptop: scp uji-master-ae/ dan master 2 juta ke /opt/synchrono-uji/bench-data/ (sama dengan fase 1)
```

**3. Jalankan** (satu sesi shell; sesi baru = `read`/`export` diulang)

```bash
cd /opt/synchrono-uji/synchrono-service-starrocks
export MYSQL_PWD="$STARROCKS_PASSWORD"       # sesi baru: jalankan dulu baris read di langkah 1
export STARROCKS_HOST=172.16.13.158 STARROCKS_PORT=9030 STARROCKS_USER=root
export STARROCKS_STREAM_LOAD_URL=http://172.16.13.158:8040 UDF_HOST=172.16.13.158
export TEST_DATA=/opt/synchrono-uji/bench-data/uji-master-ae
export MASTER_PARQUET=/opt/synchrono-uji/bench-data/1790325476460_23223dc0_master.parquet
mkdir -p bench/results
bash bench/stack.sh storage && bash bench/stack.sh harness && bash bench/stack.sh new
bash bench/stack.sh data && bash bench/stack.sh master
curl -s -o /dev/null -w '%{http_code}\n' http://172.16.13.158:58102/udf/synchrono-udf.jar   # 200

nohup bash -c 'bash bench/run_bench.sh new A,B,C,D,E 3; python3 bench/summarize.py' > bench/results/starrocks-2m.log 2>&1 &
# selesai: cp bench/results/summary.json bench/results/summary-starrocks-2m.json
grep -o "candidatePullMs': [0-9]*" bench/results/starrocks-2m.log | sort | uniq -c   # semua 0 = UDF terpakai

# Master 20 dan 30 juta. Tabel master dikosongkan sebelum setiap muat, supaya tiap ukuran
# mulai dari tabel bersih seperti DuckDB yang membaca parquet baru (DELETE di load_master
# meninggalkan baris lama di disk sampai compaction). sha256 berkas master berbeda di setiap
# pembuatan (baris ditulis paralel); isi yang sama terlihat dari "content fingerprint" di log
# make_master.py dan dari hitungan AUTO/REVIEW/UNMATCH/CONFLICT yang identik dengan fase 1.
nohup bash -c '
set -e
for n in 20 30; do
  docker run --rm -v /opt/synchrono-uji/bench-data:/d -v "$PWD/bench:/bench:ro" synchrono-service:2.0.0 \
    python /bench/make_master.py /d/1790325476460_23223dc0_master.parquet /d/master-${n}m.parquet ${n}000000
  export MASTER_PARQUET=/opt/synchrono-uji/bench-data/master-${n}m.parquet
  bash bench/stack.sh data
  mysql -h127.0.0.1 -P9030 -uroot -e "TRUNCATE TABLE syncrono_master.persons"
  bash bench/stack.sh master
  bash bench/run_bench.sh new A,B,C,D,E 3
  python3 bench/summarize.py
  cp bench/results/summary.json bench/results/summary-starrocks-${n}m.json
done
echo SELESAI' > bench/results/starrocks-20m-30m.log 2>&1 &

docker run --rm -v /opt/synchrono-uji/bench-data:/d -v "$PWD/bench:/bench:ro" synchrono-service:2.0.0 \
  python /bench/make_master.py /d/1790325476460_23223dc0_master.parquet /d/master-100m.parquet 100000000
export MASTER_PARQUET=/opt/synchrono-uji/bench-data/master-100m.parquet
bash bench/stack.sh data
mysql -h127.0.0.1 -P9030 -uroot -e 'TRUNCATE TABLE syncrono_master.persons'
nohup bash bench/stack.sh master > bench/results/load-master-100m.log 2>&1 &
# selesai bila log memuat "rows": 100000000; cek sisa disk: df -h /opt
nohup bash -c 'bash bench/run_bench.sh new A,B,C,D,E 1; python3 bench/summarize.py' > bench/results/starrocks-100m.log 2>&1 &
# selesai: cp bench/results/summary.json bench/results/summary-starrocks-100m.json
```

Terakhir, catat `fase2-info.txt` seperti fase 1, ditambah `SELECT current_version()`,
`MemLimit` dari `SHOW BACKENDS`, dan `java -version`. Setelah itu salin `bench/results`
ke laptop.

## E. Cluster dua node (R&D): BE kedua di serverai

FE dan BE pertama tetap di 158. BE kedua dipasang terpisah di serverai (172.16.12.98), di
samping StarRocks kantor: folder, port, dan memori sendiri, dan hanya terdaftar ke FE 158.
StarRocks kantor (`/opt/starrocks`, `starrocks.service`, port 9050/9060/8040/8060) tidak
disentuh dan tetap satu node. BE kedua dikunci ke 16 core (8-23), sama dengan 158.

**1. Di serverai: BE uji** (tarball yang sama dengan bagian D)

```bash
mkdir -p /opt/sr-uji
tar -xzf /root/StarRocks-4.0.8-ubuntu-amd64.tar.gz -C /opt/sr-uji --strip-components=1 StarRocks-4.0.8-ubuntu-amd64/be
mkdir -p /opt/sr-uji/be/storage
cat >> /opt/sr-uji/be/conf/be.conf <<'CONF'

# BE uji untuk cluster 158, terpisah dari /opt/starrocks: port, folder, dan memori sendiri
be_port = 19060
be_http_port = 18040
heartbeat_service_port = 19050
brpc_port = 18060
starlet_port = 19070
storage_root_path = /opt/sr-uji/be/storage
priority_networks = 172.16.12.0/24
JAVA_HOME = /usr/lib/jvm/java-21-openjdk-amd64
mem_limit = 27777777778
# Tanpa dua baris ini BE menganggap mesin 24 core dan mengikat ulang thread-nya ke semua
# core, sehingga lolos dari taskset (uji pertama: puncak 22,7 core). Dengan ini BE tetap di
# core 8-23 dan menyesuaikan jumlah thread serta DOP untuk 16 core, sama dengan 158.
enable_resource_group_bind_cpus = false
num_cores = 16
CONF
cd /opt/sr-uji/be
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ulimit -n 655350
taskset -c 8-23 ./bin/start_be.sh --daemon
curl -s http://127.0.0.1:18040/varz | grep -E '^(enable_resource_group_bind_cpus|num_cores)='
```

Agar BE uji menyala sendiri setiap serverai boot, pasang unit terpisah `sr-uji-be.service`.
`starrocks.service` milik StarRocks kantor tidak diubah.

```bash
cat > /opt/sr-uji/start-be.sh <<'EOS'
#!/bin/bash
# BE uji cluster 158 (bukan StarRocks kantor di /opt/starrocks); hanya bila belum jalan.
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ulimit -n 655350
cd /opt/sr-uji/be
pgrep -f /opt/sr-uji/be/lib/starrocks_be >/dev/null || taskset -c 8-23 ./bin/start_be.sh --daemon
EOS
chmod +x /opt/sr-uji/start-be.sh
cat > /etc/systemd/system/sr-uji-be.service <<'EOS'
[Unit]
Description=StarRocks BE uji untuk cluster 158 (/opt/sr-uji, bukan StarRocks kantor)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
LimitNOFILE=655350
ExecStart=/opt/sr-uji/start-be.sh
ExecStop=/opt/sr-uji/be/bin/stop_be.sh
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOS
systemctl daemon-reload
systemctl enable --now sr-uji-be.service
```

**2. Di 158: daftarkan, lalu cek jaringan dua arah**

```bash
mysql -h127.0.0.1 -P9030 -uroot -e 'ALTER SYSTEM ADD BACKEND "172.16.12.98:19050"'
mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW BACKENDS\G' | grep -E ' IP:|Alive:|TabletNum:'
for p in 19050 19060 18040 18060; do timeout 3 bash -c "</dev/tcp/172.16.12.98/$p" && echo "$p ok"; done
# di serverai: 9020 9060 8040 8060 (StarRocks 158) dan 58102 (jar UDF) harus terbuka ke 158
```

**3. Uji 20, 30, dan 100 juta** (di 158). Penyeimbang tablet dimatikan selama uji: disk
serverai sudah 80% terpakai oleh aplikasi lain, sehingga penyeimbang bisa memindahkan
tablet master kembali ke 158 di tengah pengukuran. Setelan ini kembali ke bawaan saat FE
di-restart. Master dikosongkan lalu dimuat ulang, supaya tablet barunya terbagi rata ke kedua
BE (log menampilkan `ADMIN SHOW REPLICA DISTRIBUTION`, harus 50/50). BE di
serverai dicatat sampler sebagai `be@172.16.12.98` dari `SHOW BACKENDS`: CPU proses BE
(dalam % satu core) dan memori yang dicatat BE (sekitar 75% dari RSS).

```bash
cd /opt/synchrono-uji/synchrono-service-starrocks
export STARROCKS_PASSWORD=$(grep '^STARROCKS_PASSWORD=' .env.bench | cut -d= -f2-)
export MYSQL_PWD=$STARROCKS_PASSWORD
export STARROCKS_HOST=172.16.13.158 STARROCKS_PORT=9030 STARROCKS_USER=root
export STARROCKS_STREAM_LOAD_URL=http://172.16.13.158:8040 UDF_HOST=172.16.13.158
export TEST_DATA=/opt/synchrono-uji/bench-data/uji-master-ae SAMPLE_BACKENDS=172.16.12.98
mysql -h127.0.0.1 -P9030 -uroot -e 'ADMIN SET FRONTEND CONFIG ("tablet_sched_disable_balance" = "true")'
nohup bash -c '
set -e
for n in 20 30 100; do
  test "$(mysql -h127.0.0.1 -P9030 -uroot -e "SHOW BACKENDS\G" | grep -c "Alive: true")" = 2 ||
    { echo "BERHENTI: BE yang hidup bukan 2"; exit 1; }
  export MASTER_PARQUET=/opt/synchrono-uji/bench-data/master-${n}m.parquet
  [ -f "$MASTER_PARQUET" ] || docker run --rm -v /opt/synchrono-uji/bench-data:/d -v "$PWD/bench:/bench:ro" \
    synchrono-service:2.0.0 python /bench/make_master.py \
    /d/1790325476460_23223dc0_master.parquet /d/master-${n}m.parquet ${n}000000
  bash bench/stack.sh data
  mysql -h127.0.0.1 -P9030 -uroot -e "TRUNCATE TABLE syncrono_master.persons"
  bash bench/stack.sh master
  mysql -h127.0.0.1 -P9030 -uroot -e "ADMIN SHOW REPLICA DISTRIBUTION FROM syncrono_master.persons"
  rounds=3; [ "$n" = 100 ] && rounds=1
  bash bench/run_bench.sh new A,B,C,D,E $rounds
  python3 bench/summarize.py
  cp bench/results/summary.json bench/results/summary-cluster-${n}m.json
done
echo SELESAI' > bench/results/cluster.log 2>&1 &
```

Bandingkan `summary-cluster-*.json` dengan `summary-starrocks2c-*.json` (fase 2c: satu node,
kode yang sama).
