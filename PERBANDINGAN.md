# Mengukur: Service vs Langflow

Cara menjalankan benchmark, cara membaca hasilnya, dan angka yang sudah
terukur di mesin pengembangan.

---

## 1. Hasil yang sudah terukur

Mesin: Windows 11, Docker Desktop, 5,8 GB memori untuk Docker, PostgreSQL di
host lewat DBngin. Semua berkas dibaca dari SeaweedFS **lokal**. AI normalisasi
**mati** di kedua sisi.

### Sebelum satu permintaan pun dikirim

| | Service | Langflow | |
|---|---:|---:|---|
| ukuran image | **433 MB** | 3,67 GB | 8,5× |
| memori saat idle | **59–65 MB** | 1,23–1,26 GB | ~20× |

Angka memori idle itu tidak berubah seberapa pun sepinya lalu lintas. Untuk
pertanyaan "muat berapa banyak di satu server", justru inilah yang paling
menentukan — bukan latency.

### Garis dasar: `/health`, 50 rps, 30 detik

Tidak menyentuh PostgreSQL maupun S3. Ini harga **lapisan HTTP-nya saja**.

| | Service | Langflow | |
|---|---:|---:|---|
| p95 | **4,2 ms** | 69,9 ms | 16,8× |
| p99 | **6,9 ms** | 126,4 ms | 18,3× |
| rata-rata | **2,6 ms** | 19,2 ms | 7,4× |
| rps terlayani | 50,0 / 50 | 50,0 / 50 | sama |
| CPU puncak | 65,4% | 87,3% | |

Keduanya **sanggup** melayani 50 rps di sini. Bedanya murni waktu tempuh per
permintaan.

### Polling status, 2 rps, 45 detik

Laju rendah — sengaja, supaya **keduanya belum jenuh** dan yang terbandingkan
benar-benar latency, bukan panjang antrean. Menyentuh PostgreSQL.

| | Service | Langflow | |
|---|---:|---:|---|
| p95 | **99,2 ms** | 1.247,8 ms | 12,6× |
| p99 | **147,5 ms** | 1.369,5 ms | 9,3× |
| rata-rata | **64,6 ms** | 979,6 ms | 15,2× |
| rps terlayani | 2,0 / 2 | 2,0 / 2 | sama |
| CPU puncak | 56,4% | **244,4%** | |

Perhatikan CPU-nya: pada **2 permintaan per detik**, Langflow sudah memakai
lebih dari dua inti penuh.

### Kurva kapasitas — angka yang paling menjelaskan

Skenario arrival-rate menjawab "berapa cepat pada beban sekian". Ia TIDAK
menjawab "berapa batasnya", dan tanpa itu angka rps mudah disalahbaca.

`kapasitas.ps1` menjawabnya dengan closed loop: sejumlah penelepon tetap, tiap
satu mengirim lagi hanya setelah dijawab. Antrean tidak pernah menumpuk, jadi
rps yang keluar adalah kapasitas sesungguhnya pada tingkat konkurensi itu.

| penelepon serentak | Langflow rps | Langflow avg | Service rps | Service avg |
|---:|---:|---:|---:|---:|
| 1 | 1,39 | 719 ms | 19,3 | 52 ms |
| 2 | 2,03 | 980 ms | — | — |
| 4 | 2,57 | 1.531 ms | 51,9 | 76 ms |
| 8 | 2,66 | 2.920 ms | — | — |
| 16 | **2,68** | 5.549 ms | **85,8** | 185 ms |
| 32 | 2,63 | 11.090 ms | — | — |
| 64 | 2,10 | 18.572 ms | 67,3 | 931 ms |
| 141 | **0** — tak satu pun selesai dalam 20 detik | | 49,5 | 2.743 ms |

Dua hal terbaca sekaligus:

**Puncaknya jauh berbeda.** Langflow mentok di **2,68 rps**, service di
**85,8 rps** — **32×**. Itu angka yang paling jujur untuk dikutip, karena ia
tidak bergantung pada laju yang kebetulan dipilih saat menguji.

**Keduanya MEMBURUK setelah puncak, bukan sekadar mendatar.** Ini bukan
kelainan Langflow; begitulah perilaku sistem mana pun yang dipaksa melewati
batasnya. Bedanya letak puncaknya, dan seberapa terjal turunnya: pada 141
penelepon, service masih melayani 49,5 rps sementara Langflow tidak
menyelesaikan satu pun permintaan dalam 20 detik.

### Polling status, 10 rps, 30 detik — dan kenapa angkanya 1,5

| | Service | Langflow |
|---|---:|---:|
| p95 | **84,8 ms** | 40.285 ms |
| rps terlayani | **10,0 / 10** | **1,5 / 10** |
| iterasi dibuang | 0 | 121 |
| VU yang dipakai k6 | 20 | **141** |
| CPU puncak | 77,4% | 341% |

Angka 1,5 itu sering ditanyakan: kenapa pada 2 rps Langflow sanggup melayani
2,0, tapi pada 10 rps justru hanya 1,5 — lebih sedikit? Jawabannya tiga
lapis, dan hanya lapis ketiga yang benar-benar menarik.

**Satu: 10 rps memang di atas batasnya.** Plafonnya 2,7 rps. 2 rps ada di
bawah plafon, jadi terlayani penuh. 10 rps hampir empat kali lipat plafon,
jadi selisihnya tidak terlayani — ia mengantre.

**Dua: sebagian selisihnya cuma pembagian.** k6 menghitung rps sebagai
`permintaan selesai ÷ durasi seluruh tes`. Jalan 10 rps berlangsung 60 detik
(30 detik beban + 30 detik graceful stop menghabiskan antrean) dan
menyelesaikan 93 permintaan: 93 ÷ 60 = 1,55. Jalan 2 rps berlangsung 45 detik
dan menyelesaikan 91: 91 ÷ 45 = 2,0. Penyebutnya berbeda.

**Tiga — dan inilah yang sesungguhnya:** k6 memakai `constant-arrival-rate`,
yang berarti ia MENJAGA JADWAL, bukan menunggu. Karena balasan tidak kunjung
datang, ia terus menambah penelepon sampai **141**. Dan di 141 penelepon,
lihat kurva di atas: Langflow tidak menyelesaikan apa pun dalam 20 detik.
Throughput-nya bukan mendatar di 2,7 — ia **jatuh**, karena 141 eksekusi flow
yang menganggur bersamaan masing-masing memegang memori dan giliran CPU pada
mesin yang hanya punya 4 inti.

Jadi 1,5 bukan berarti "tiap permintaan jadi lebih lambat". Artinya Langflow
terdorong ke wilayah di mana ia menghasilkan LEBIH SEDIKIT daripada saat
dibebani ringan. Membebani lebih berat justru memberi hasil lebih sedikit —
dan itu sebabnya menaikkan beban bukan cara menaikkan throughput.

### Apa artinya untuk portal

Portal melakukan polling tiap 2–3 detik per berkas yang sedang digrading. Lima
berkas diunggah bersamaan berarti sekitar 2 rps — **sudah 74% dari plafon
Langflow (2,7 rps)**. Tujuh berkas melewatinya, dan begitu terlewati, latency
tidak naik sedikit demi sedikit melainkan meledak: antreannya memanjang lebih
cepat daripada kemampuannya menghabiskan.

Pada service, 2 rps adalah 2% dari plafonnya.

---

## 1b. Skala Dukcapil: jutaan baris, kolom lebar

Benchmark di bagian 1 mengukur **lapisan API** — berapa permintaan per detik
yang bisa dilayani. Bagian ini mengukur hal yang sama sekali berbeda: **mesin
pengolahnya**, pada data sebesar yang akan dipadankan di Dukcapil.

Berkas uji: **35 kolom** menyerupai tabel Dukcapil (6 elemen inti + no_kk,
alamat, RT/RW, agama, status kawin, pekerjaan, pendidikan, NIK ayah/ibu, nomor
akta, dan seterusnya), NIK dibuat dari **kode wilayah sungguhan** dan konsisten
dengan tanggal lahir serta jenis kelamin, dengan ~14% anomali yang disuntikkan.

### Temuan yang paling penting: satu setelan memisahkan mati dan hidup

| baris | tanpa `memory_limit` | dengan `memory_limit=3GB` |
|---:|---|---|
| 1 juta | 29,7 dtk — RSS 2.605 MB | 30,3 dtk — RSS 2.425 MB |
| 2 juta | 39,4 dtk — RSS 3.709 MB | — |
| 3 juta | 46,6 dtk — RSS 3.961 MB | — |
| 5 juta | **MATI — OOM, exit 137** | **73,6 dtk — RSS 3.447 MB** |
| 10 juta | — | **137–149 dtk — RSS 3.480–3.540 MB** |

Bawaan DuckDB adalah 80% RAM **yang ia lihat**, dan di dalam container ia
melihat RAM mesin, bukan batas cgroup-nya. Jadi ia mengalokasi melewati batas
container lalu kernel membunuhnya — **exit code 137, tanpa satu pun pesan galat
dari DuckDB**. Gejalanya: proses hilang begitu saja.

Yang mengejutkan: dengan batas disetel, memorinya justru **lebih rendah**
(3,4 GB pada 5 juta baris, dibanding 3,9 GB pada 3 juta tanpa batas) dan
**mendatar** dari 5 ke 10 juta. Batas ini tidak mengorbankan apa pun — ia
membuat DuckDB mengelola buffer-nya alih-alih menimbun. Tumpahan ke disk
bahkan nol; ia tidak pernah sampai perlu menumpah.

`DUCKDB_MEMORY_LIMIT` dan `DUCKDB_TEMP_DIR` kini ada di ketiga berkas compose
dengan bawaan 3GB. **Setel ~60% batas memori container** di server.

### Ke mana waktunya pergi — 10 juta baris

| tahap | detik | |
|---|---:|---|
| G1 buka sesi | 0,3 | |
| **G2 kenali kolom + normalisasi tanggal** | **37,4** | 25% |
| **G3 bersihkan NIK + tandai anomali** | **56,9** | 38% |
| G4 skor & grade | 4,7 | 3% |
| **G5 tulis enriched parquet** | **49,4** | 33% |
| G6 susun muatan | 0,5 | |

**G2 sebagian besar biaya TETAP, bukan per baris.** Pada 1, 2, 3, dan 5 juta
baris ia memakan 22,3 / 25,5 / 24,7 / 26,3 detik — hampir tidak bergerak
meski barisnya lima kali lipat. Baru di 10 juta ia naik ke 37,4.

Sebabnya: pengenalan kolom bekerja atas **sampel**, bukan seluruh baris, dan
dengan 35 kolom ada 29 kolom tak dikenal yang harus melewati lima lapis
pengenalan — termasuk lapis kamus yang memindai tabel master. Jadi biayanya
mengikuti **jumlah kolom**, bukan jumlah baris.

Akibat praktisnya: **berkas kecil berkolom lebar justru boros.** Berkas 3.000
baris × 35 kolom menanggung biaya G2 yang hampir sama dengan berkas 1 juta
baris. Kalau nanti perlu dioptimalkan, di sinilah tempatnya.

### Laju yang stabil

| baris | grading | baris/detik | enriched parquet |
|---:|---:|---:|---:|
| 1 juta | 30,3 dtk | 33.019 | 118 MB |
| 2 juta | 39,4 dtk | 50.787 | — |
| 3 juta | 46,6 dtk | 64.405 | — |
| 5 juta | 73,6 dtk | 67.905 | — |
| 10 juta | 137,7 dtk | **72.603** | ~1,2 GB |

Lajunya **naik** seiring berkas membesar — justru karena biaya tetap G2
terbagi ke lebih banyak baris. Di atas 5 juta ia mendatar di sekitar
**70.000 baris/detik** pada 4 inti.

### Matching bukan leher botolnya

Diukur dengan pola `padan` — NIK diambil dari master, jadi kecocokannya nyata:

| baris | matching | baris/detik | hasil |
|---:|---:|---:|---|
| 1 juta | 2,01 dtk | 498.036 | 900.000 cocok / 100.000 tidak |
| 5 juta | **4,45 dtk** | **1.124.560** | 4.500.000 cocok / 500.000 tidak |

Matching **15 kali lebih cepat** daripada grading pada berkas yang sama.
Join-nya terhadap master 299 ribu baris, dan DuckDB menyelesaikannya dalam
hitungan detik. Untuk pemadanan Dukcapil, yang perlu dipikirkan adalah
grading — bukan matching.

### Perkiraan untuk skala nasional

Dengan **70.000 baris/detik** pada 4 inti dan memori mendatar di 3,5 GB:

| jumlah | perkiraan grading |
|---:|---|
| 10 juta | ~2,5 menit |
| 50 juta | ~12 menit |
| 280 juta (skala nasional) | **~67 menit** |

Itu ekstrapolasi linear, dan dua hal bisa membuatnya tidak berlaku: berkas
tunggal 280 juta baris belum pernah diuji, dan parquet keluarannya akan
sekitar 33 GB — masih jauh di bawah 918 GB disk yang tersedia, tapi
penulisannya (G5, sepertiga waktu) bergantung pada kecepatan tulis S3.

**Yang sudah pasti**: memori tidak akan jadi penghalang selama
`DUCKDB_MEMORY_LIMIT` disetel. Itu terbukti mendatar dari 5 ke 10 juta baris.

### Menjalankan sendiri

```powershell
cd synchrono-service/beban

# Sapuan grading — 1, 2, 5, 10 juta baris
.\skala.ps1 -TanpaMatching

# Sapuan matching — NIK yang benar-benar ada di master
.\skala.ps1 -Baris 1000000,5000000 -Pola padan

# Membuktikan dinding OOM-nya: kosongkan batas memori
.\skala.ps1 -Baris 5000000 -BatasMemori "" -TanpaMatching
```

Tiap ukuran dijalankan dalam **proses terpisah**. `ru_maxrss` hanya naik dan
tidak pernah turun, jadi menguji 1 juta lalu 10 juta dalam satu proses akan
membuat angka pertama terbawa ke yang kedua.

Satu berkas saja:

```powershell
docker exec -e BESAR_POLA=konsisten synchrono-service `
    python /synchrono/beban/buat_data_besar.py 5000000
docker exec -e DUCKDB_MEMORY_LIMIT=3GB synchrono-service `
    python /synchrono/beban/uji_skala.py besar-5000k
```

---

## 1c. Langflow vs Service pada berkas jutaan baris

Bagian 1 mengukur endpoint dengan berkas 3.000 baris, dan di sana platform
sangat menentukan — 32x selisih kapasitas. Bagian ini mengulang pertanyaan yang
sama untuk berkas **1 juta baris**, dan jawabannya ternyata **berbeda**.

Skenario: satu berkas 1 juta baris x 35 kolom dikirim, lalu dipolling tiap
2 detik sampai selesai — persis yang portal lakukan. Sambil itu, polling berkas
LAIN berjalan pada 2 permintaan/detik, mewakili unggahan lain yang juga
ditunggu. Itulah sumber rebutan CPU-nya, pada mesin 4 inti yang sama.

Kedua platform memakai `DUCKDB_MEMORY_LIMIT=3GB` yang identik.

### Hasilnya: total waktunya hampir sama (kedua container hidup)

| | Service | Langflow | |
|---|---:|---:|---|
| Memori saat idle | **111 MB** | 1.261 MB | 11x |
| Dispatch | **70 ms** | 929 ms | 13x |
| Grading menurut engine | 25.659 ms | **23.568 ms** | Langflow 8% lebih cepat |
| **Tuntas (kirim sampai selesai)** | **26.715 ms** | 27.293 ms | **selisih 2%** |
| Polling rata-rata | **56,3 ms** | 966,4 ms | 17x |
| Polling p95 | **88,6 ms** | 1.888,1 ms | **21,3x** |
| Polling p99 | **150,3 ms** | 2.235,3 ms | 15x |
| Polling maks | **241,5 ms** | 2.329,0 ms | 10x |
| Permintaan gagal | 1 dari 136 | 2 dari 128 | |

**Waktu total praktis sama: 26,7 detik lawan 27,3 detik.** Dan grading-nya
sendiri justru sedikit LEBIH CEPAT di Langflow (23,6 lawan 25,7 detik) —
selisih yang masuk akal disebut derau, bukan keunggulan.

### Kenapa begitu, dan kenapa itu masuk akal

Grading dikerjakan `lib/` yang **sama persis** di thread latar pada kedua
platform. Lapisan API hanya menerima job, melepas thread, lalu menjawab
polling. Pada berkas 3.000 baris pekerjaan itu selesai dalam milidetik,
sehingga yang tersisa hampir seluruhnya biaya platform. Pada 1 juta baris,
pekerjaan itu makan 25 detik — dan biaya platform tenggelam di dalamnya.

Jadi dua hasil ini tidak bertentangan; keduanya benar untuk pertanyaan yang
berbeda:

* **Berkas kecil, banyak permintaan** — platform menentukan. Plafon Langflow
  2,68 rps lawan 85,8 rps (bagian 1).
* **Berkas besar, sedikit permintaan** — pekerjaannya yang menentukan.
  Platform nyaris tidak terasa.

### Yang tetap berbeda, dan tetap penting

**Responsivitas polling: 21x.** Portal menunggu 1,9 detik tiap kali bertanya
"sudah selesai belum?" di Langflow, lawan 89 milidetik. Itu tidak memperlambat
grading, tapi terasa di UI — dan mengalikannya dengan jumlah berkas yang
ditunggu bersamaan.

**Langflow menjatuhkan permintaan saat menggrading.** Terekam di log k6:

```
level=warning msg="Request Failed"
  error="Post .../grading-status?stream=false: EOF"
```

Pada jalan sebelumnya dengan berkas 5 juta baris, kegagalannya beruntun:

```
Request Failed  ... read: connection reset by peer
Request Failed  ... read: connection reset by peer
Request Failed  ... EOF
Request Failed  ... read: connection reset by peer
```

Portal yang menerima ini akan menganggap job-nya bermasalah, padahal grading
berjalan normal. Sisi service tidak pernah menunjukkan gejala ini.

**Memori idle 11x.** Tidak berubah seberapa pun sepinya lalu lintas.

### Diukur bergantian: hanya satu platform yang hidup

Tabel di atas diambil saat **kedua container hidup bersamaan** — yang diukur
satu, tapi yang lain tetap memakan memori dan sedikit CPU. Karena Langflow
menganggur di 1,26 GB dari 5,79 GB yang ada, service sebenarnya diukur dengan
memori tersisa lebih sedikit daripada sebaliknya. Jalan ini mengulang
pengukuran yang sama dengan `-Bergantian`: container yang tidak diukur
**dimatikan**, Langflow diukur duluan, baru service.

| | Bersamaan | Bergantian | |
|---|---:|---:|---|
| **Langflow** tuntas | 27.293 ms | **23.407 ms** | −14% |
| **Langflow** grading engine | 23.568 ms | **21.685 ms** | −8% |
| **Langflow** dispatch | 929 ms | **866 ms** | −7% |
| **Langflow** polling p95 | 1.888,1 ms | **1.272,3 ms** | **−33%** |
| **Langflow** polling p99 | 2.235,3 ms | **1.494,6 ms** | −33% |
| **Service** tuntas | 26.715 ms | **24.704 ms** | −8% |
| **Service** grading engine | 25.659 ms | **23.893 ms** | −7% |
| **Service** dispatch | 70 ms | 85 ms | +21% (derau) |
| **Service** polling p95 | 88,6 ms | **78,6 ms** | −11% |
| **Service** polling p99 | 150,3 ms | **101,6 ms** | −32% |
| **Selisih polling p95** | **21,3x** | **16,2x** | |

Tiga hal yang dijawab jalan ini:

**Rebutannya nyata, dan Langflow yang paling dirugikan.** Dengan tetangganya
mati, p95 polling Langflow turun sepertiga. Itu jauh lebih besar daripada
perbaikan yang didapat service (−11%), dan masuk akal: Langflow-lah yang
hidup di ambang memori.

**Tapi urutan kesimpulannya tidak berubah.** Selisih polling menyempit dari
21,3x ke 16,2x — menyempit, bukan hilang. Waktu total tetap praktis sama
(23,4 lawan 24,7 detik, selisih 5%), persis seperti saat bersamaan. Yang
sebelumnya disimpulkan tetap berdiri.

**Putusnya koneksi bukan ulah tetangga.** Ini yang paling layak dicatat.
Dengan synchrono-service dimatikan sepenuhnya, Langflow **tetap** menjatuhkan
satu permintaan status saat menggrading:

```
level=warning msg="Request Failed"
  error="Post .../grading-status?stream=false: read: connection reset by peer"
```

Jadi itu perilaku Langflow sendiri di bawah beban grading, bukan akibat
rebutan sumber daya dengan container sebelah. Sisi service tetap nol kegagalan
koneksi di kedua mode.

### Urutan dibalik: cache bukan penjelasannya

Jalan di atas punya satu lubang: SeaweedFS dan PostgreSQL tetap hidup untuk
keduanya, jadi platform yang jalan **belakangan** menikmati cache parquet yang
sudah hangat. Di jalan itu urutannya Langflow dulu, maka keuntungan itu jatuh
ke service. Jalan ketiga membalik urutannya — service duluan, Langflow
belakangan — supaya keuntungan cache berpindah ke Langflow.

| | Langflow duluan | Service duluan | |
|---|---:|---:|---|
| Langflow polling p95 | 1.272,3 ms | 1.379,3 ms | |
| Service polling p95 | 78,6 ms | 82,6 ms | |
| **Selisih p95** | **16,2x** | **16,7x** | **praktis sama** |
| Langflow tuntas | 23.407 ms | 25.904 ms | |
| Service tuntas | 24.704 ms | 24.895 ms | |
| Langflow dispatch | 866 ms | 1.755 ms | |
| Service dispatch | 85 ms | 316 ms | |
| Langflow koneksi putus | 1 | 2 | |
| Service koneksi putus | 0 | 0 | |

**16,2x lawan 16,7x.** Urutan tidak menjelaskan apa pun. Pada jalan pertama
Langflow diukur dengan cache dingin, pada jalan kedua dengan cache hangat, dan
selisihnya tetap di tempat yang sama. Lubang pengukurannya tertutup: selisih
polling itu sifat platformnya, bukan artefak urutan.

Dua hal lain yang terlihat dari sepasang jalan ini:

**Waktu tuntas service jauh lebih stabil.** 24.704 dan 24.895 ms — selisih
kurang dari 1%. Langflow: 23.407 dan 25.904 ms, selisih 11%. Untuk portal yang
menampilkan perkiraan waktu selesai, itu perbedaan yang terasa.

**Koneksi putus terjadi di kedua jalan, selalu hanya di Langflow**, selalu saat
container-nya sendirian. Satu kali di jalan pertama, dua kali di jalan kedua.
Sisi service: nol, di ketiga jalan.

Log lengkapnya: **`beban/hasil/log-skala-bergantian-k6.txt`** (Langflow duluan)
dan **`beban/hasil/log-skala-bergantian-balik-k6.txt`** (service duluan).

### Batas pengukuran ini

Satu iterasi per platform per jalan — tiga jalan, jadi **enam pengukuran**,
masih sebaran yang tipis. Selisih 2-5% pada waktu total ada di dalam derau dan
tidak boleh dibaca sebagai "Langflow lebih cepat" maupun sebaliknya. Dispatch
pun bergoyang cukup jauh (service 70/85/316 ms; Langflow 929/866/1.755 ms),
jadi angkanya hanya kuat sebagai orde besaran: sekitar 10x.

Yang berdiri kokoh di atas derau, karena terulang di ketiga jalan dan tidak
berubah oleh urutan maupun oleh matinya container tetangga: **polling
(16-21x)**, **memori idle (11x)**, dan **koneksi putus yang hanya terjadi di
Langflow**. Waktu total, sebaliknya, memang praktis sama — dan itu kesimpulan,
bukan kekurangan pengukuran.

### Menjalankan sendiri

```powershell
cd synchrono-service/beban

# Bangkitkan berkasnya dulu (sekali saja)
docker exec synchrono-service python /synchrono/beban/buat_data_besar.py 1000000

# Bandingkan kedua platform
./jalankan.ps1 -Skenario skala_jutaan -BerkasBesar besar-1000k -Bucket bucket-test -Bising 2

# Tanpa rebutan CPU, sebagai pembanding
./jalankan.ps1 -Skenario skala_jutaan -BerkasBesar besar-1000k -Bucket bucket-test -Bising 0

# Bergantian: yang tidak diukur DIMATIKAN, lalu dinyalakan lagi di akhir
./jalankan.ps1 -Skenario skala_jutaan -Target langflow,service -Bergantian `
  -BerkasBesar besar-1000k -Bucket bucket-test -Bising 2 -BatasMenit 10

# Lalu balik urutannya, supaya keuntungan cache berpindah sisi
./jalankan.ps1 -Skenario skala_jutaan -Target service,langflow -Bergantian `
  -BerkasBesar besar-1000k -Bucket bucket-test -Bising 2 -BatasMenit 10
```

Log k6 lengkap: **`beban/hasil/log-skala-jutaan-k6.txt`** (bersamaan),
**`beban/hasil/log-skala-bergantian-k6.txt`** (bergantian, Langflow duluan),
**`beban/hasil/log-skala-bergantian-balik-k6.txt`** (bergantian, service duluan).
Hasil mentah per platform: `beban/hasil/*-skala_jutaan-*.json`.

> Berkas `TIDAK-SAH-*.json` di folder yang sama adalah jalan pertama yang
> dibuang: container-nya belum dibuat ulang setelah `DUCKDB_MEMORY_LIMIT`
> ditambahkan ke compose, sehingga grading 5 juta baris tercatat 425 detik
> (lawan 73,6 detik saat berdiri sendiri). Angka itu mencampur rebutan CPU
> dengan ketiadaan batas memori, jadi tidak mengukur apa pun yang bisa
> ditafsirkan. Disimpan sebagai pengingat, bukan sebagai hasil.

---

## 1d. Apakah Polars membuat grading lebih cepat?

Jawabannya **tidak** — dan mencarinya menemukan sesuatu yang jauh lebih besar.

### Batas atasnya sudah ditentukan sebelum Polars diuji

Grading 1 juta baris x 35 kolom, per tahap:

| tahap | detik | |
|---|---:|---|
| G1 buka sesi | 0,35 | |
| **G2 kenali kolom + normalisasi** | **26,21** | **77%** |
| G3 bersihkan NIK + tandai anomali | 3,84 | 11% |
| G4 skor & grade | 0,24 | |
| G5 tulis enriched parquet | 3,45 | 10% |
| G6 susun muatan | 0,09 | |
| **total** | **34,17** | |

Yang bisa disentuh mesin dataframe hanya G3 dan G5 — 7,3 detik dari 34,2.
Andai Polars membuat keduanya **seketika**, totalnya turun 21%. Itu plafonnya,
sebelum satu baris Polars pun ditulis.

### Diadu langsung di G3 dan G5

`beban/uji_polars.py` menyalin ekspresi G3 satu per satu ke Polars —
pembersihan NIK (notasi ilmiah, float utuh, buang non-digit), panjang 16, kode
provinsi dan kecamatan diadu ke tabel rujukan yang sama, hari/bulan/tahun dari
digit 7-12, kelamin dari hari > 40, gelar dan patronimik, duplikasi lewat
window, lalu perakitan `anomaly_type`. Tiap mesin jalan di **prosesnya
sendiri**, karena RSS puncak adalah tanda air yang tidak pernah turun.

| baris | DuckDB | RSS | Polars | RSS | Polars streaming | RSS |
|---|---:|---:|---:|---:|---:|---:|
| 1 juta | **1,72 s** | **511 MB** | 1,86 s | 1.298 MB | 2,07 s | 1.213 MB |
| 5 juta | **5,02 s** | **1.262 MB** | **OOM** | — | 15,82 s | 4.317 MB |
| 10 juta | **9,42 s** | **2.135 MB** | **OOM** | — | **OOM** | — |

Hasil kedua mesin dibandingkan kolom per kolom atas 200.000 baris: **sama
persis**. Jadi angka di atas memang membandingkan hal yang sama.

Tiga hal yang keluar:

**Polars menang tipis di hitungan, kalah di tulis.** Pada 1 juta baris G3-nya
0,97 lawan 1,16 detik, tapi menulis parquet 0,89 lawan 0,56 detik. Bersih-
bersih, ia kalah.

**Memorinya 2,5x lipat,** dan itu yang menentukan. DuckDB menyelesaikan 10 juta
baris dengan 2,1 GB. Polars mati di 5 juta.

**Mode streaming bertahan lebih lama, tapi tetap kalah.** `sink_parquet`
menyelamatkan yang 5 juta — 15,82 detik lawan 5,02 detik, jadi **3,2x lebih
lambat** dengan memori 3,4x lipat — lalu ikut mati di 10 juta.

Untuk pemadanan Dukcapil, mati di 5 juta baris bukan kekurangan kecepatan.
Itu diskualifikasi.

### Yang ditemukan sambil mencari: 20 dari 34 detik ada di satu fungsi

G2 memakan 77%, jadi ia dibedah (`beban/profil_g2.py`):

| bagian G2 | detik |
|---|---:|
| buka view + DESCRIBE + count | 0,01 |
| **`_lapis_kamus`** | **20,17** |
| `_siapkan_kamus` | 2,12 |
| `ambil_sampel` (3x) | 1,34 |
| `deteksi_konvensi`, `ada_huruf`, `deteksi_serial_excel` | 0,23 |
| `bangun_view` | 0,26 |

`_lapis_kamus` adalah lapis keempat pengenalan kolom: untuk tiap kolom yang
belum dikenali, 300 nilai contohnya dicocokkan ke kamus tabel master.
Pencocokannya sendiri **3 milidetik**. Sisanya ongkos memasukkan nilainya.

Enam bentuk pernyataan dicoba dan **tidak satu pun menolong** —
`executemany`, satu `INSERT` ber-300 parameter, satu parameter berisi list,
tabel dipakai ulang, tanpa tabel sama sekali, bahkan satu pernyataan untuk
seluruh 22 kolom. Semuanya di kisaran 16-20 detik. Sebabnya baru terlihat
setelah diukur per nilai, di koneksi DuckDB polos:

| nilai diseberangkan | waktu | per nilai |
|---:|---:|---:|
| 5 | 12,7 ms | 2,55 ms |
| 50 | 173,4 ms | 3,47 ms |
| 300 | 1.004,9 ms | 3,35 ms |
| 1.000 | 2.003,6 ms | 2,00 ms |

Ongkosnya **per nilai yang menyeberang dari Python ke DuckDB**, lurus, sekitar
2-3 ms. Bentuk pernyataannya tidak berpengaruh karena jumlah nilainya tetap
sama. 23 kolom x 300 nilai — di situlah 20 detiknya.

Yang janggal: nilai-nilai itu **asalnya dari DuckDB**. `ambil_sampel`
menariknya keluar dengan `fetchall()`, lalu `_lapis_kamus` mengirimkannya
kembali masuk.

### Obatnya, dan bukti hasilnya tidak berubah

Tahan sampelnya di dalam DuckDB, cocokkan seluruh kolom dalam satu `UNPIVOT`.
Tidak ada satu nilai pun yang menyeberang.

```
sekarang (per kolom, lewat Python) :  18,31 detik
UNPIVOT (semua di dalam DuckDB)    :   0,47 detik
                                        39x lebih cepat

Kesamaan keputusan atas 23 kolom: SAMA SEMUA
```

Dampaknya ke seluruh pipeline pada 1 juta baris: **34,2 -> sekitar 16,3 detik,
2,1x.** Bandingkan dengan plafon Polars yang 21% — dan yang ini berlaku untuk
Langflow dan service sekaligus, karena keduanya memakai `lib/` yang sama.

> **Sudah diterapkan, tapi bukan dengan UNPIVOT.** Bagian 1e menemukan cara
> yang lebih cepat lagi — jembatan Arrow, 68x — jadi itu yang dipakai. Angka
> UNPIVOT di atas tetap dicatat karena ia tidak menambah dependensi apa pun,
> dan berguna kalau pyarrow suatu saat tidak tersedia.

### Menjalankan sendiri

```powershell
# Polars tidak ada di requirements.txt — sengaja, karena ia kalah.
docker exec synchrono-service pip install polars

docker exec synchrono-service python /synchrono/beban/uji_polars.py besar-1000k
docker exec synchrono-service python /synchrono/beban/uji_polars.py besar-5000k --hanya duckdb
docker exec synchrono-service python /synchrono/beban/uji_polars.py besar-5000k --hanya stream

# Pembedahan G2 dan lapis kamusnya
docker exec synchrono-service python /synchrono/beban/profil_g2.py besar-1000k
docker exec synchrono-service python /synchrono/beban/profil_kamus.py besar-1000k
```

---

## 1e. Kombinasi DuckDB + Polars (pola `data-matching`)

Pertanyaannya bukan "DuckDB atau Polars", melainkan **keduanya sekaligus** —
pola yang dipakai proyek `data-matching` di repo sebelah, yang katanya lebih
cepat. Jawabannya: **polanya kalah 10-18x di matching, tapi satu tekniknya
menang 68x di tempat lain** — dan teknik itu sudah diterapkan.

### Apa yang sebenarnya dikombinasikan

`data-matching/processing/matching_service.py`:

```python
con.register("incoming_df", incoming_df.to_arrow())   # Polars -> DuckDB lewat Arrow
joined = con.execute(SQL_JOIN).pl()                   # DuckDB -> Polars
for row in joined.iter_rows(named=True):              # skoring di Python
    score = JaroWinkler.similarity(row["nama_clean"], row["nama_master_clean"])
```

DuckDB mengerjakan JOIN, Polars jadi pembawa data, dan skoring fuzzy dikerjakan
loop Python dengan rapidfuzz. Kombinasinya ada **karena** rapidfuzz tidak bisa
dipanggil dari SQL — bukan karena dua mesin lebih cepat dari satu.

Matching kita tidak punya loop itu: `jaro_winkler_similarity` milik DuckDB
sudah diverifikasi identik bit-per-bit dengan rapidfuzz, jadi skoring dan
pemilihan kandidat (`QUALIFY`) selesai di dalam SQL.

### Diadu langsung, join-nya dibuat sama

`beban/uji_hibrida.py` menjalankan join yang SAMA sekali, lalu mencabangkannya
ke dua cara skoring — supaya yang terukur memang bagian yang berbeda saja.

| grade | skor di SQL (kita) | skor di Python (hibrida) | |
|---|---:|---:|---|
| 1 (nama) | **0,30 s** | 3,08 s | **10,3x** |
| 3 (nama+lahir) | **1,53 s** | 28,17 s | **18,4x** |
| 5 (+ wilayah) | **3,00 s** | 52,71 s | **17,5x** |

Skor kedua sisi diadu baris per baris: **1.000.000 sama, 0 berbeda.** Memori
puncak hibrida 2,1x lipat (4.315 MB lawan 1.982 MB pada grade 5).

Menarik ongkosnya terpisah pada grade 5: memindahkan hasil join ke Polars 4,81
detik, loop skoringnya 47,90 detik. Jadi bukan jembatannya yang mahal —
loop-nya.

> Pada grade 5, 15.178 baris tidak ikut terbandingkan karena blocking-nya
> INNER JOIN: baris tanpa kandidat sama sekali tidak muncul di hasil join, dan
> hanya ada di sisi kita lewat jaring pengaman di n6. Itu selisih cakupan yang
> menguntungkan kita, bukan ketidakcocokan skor.

### Tekniknya tetap berharga — hanya bukan di situ

`con.register(...to_arrow())` menyeberangkan data lewat buffer Arrow, tanpa
menyalin nilai satu per satu. Itu persis obat untuk leher botol di bagian 1d:
`_lapis_kamus` menghabiskan 18 detik hanya untuk memindahkan 23 x 300 nilai
contoh dari Python ke DuckDB.

Diukur pada berkas yang sama:

```
sekarang (per kolom, lewat Python) :  17,93 detik
Arrow (register, nol salinan)      :   0,26 detik    68x
UNPIVOT (semua di dalam DuckDB)    :   0,56 detik    32x
```

Arrow mengalahkan UNPIVOT, jadi Arrow yang dipakai.

### Diterapkan, dan buktinya

`lib/_normalisasi.py` sekarang punya `_skor_kamus()` yang menghitung seluruh
kolom sisa dalam satu pernyataan lewat Arrow, dengan fallback ke jalur lama
kalau pyarrow tidak ada. Satu berkas, dua platform: Langflow dan service
mem-bind-mount `lib/` yang sama.

Grading 1 juta baris x 35 kolom, berdiri sendiri:

| tahap | sebelum | sesudah |
|---|---:|---:|
| G1 | 0,32 | 0,32 |
| **G2** | **26,21** | **4,25** |
| G3 | 3,84 | 3,51 |
| G4 | 0,24 | 0,23 |
| G5 | 3,45 | 3,22 |
| G6 | 0,11 | 0,11 |
| **total** | **34,17 detik** | **11,64 detik** |
| laju | 29.267 baris/detik | **85.919 baris/detik** |

Regresi: **36 berkas uji memetakan kolom sama persis** — dari 800 baris sampai
10 juta, 6 kolom sampai 35 kolom, yang bersih maupun yang sengaja disamarkan
(`samar-*`). Yang diadu bukan hanya `peta`-nya, tapi juga `jejak`-nya, yang
memuat skor pesaing tiap keputusan. Pengenalan kolomnya sendiri 202,69 -> 59,89
detik untuk 36 berkas (3,4x). Grade juga tidak bergeser: verif-A sampai verif-E
tetap A, B, C, D, E.

| berkas | Arrow | jalur lama |
|---|---:|---:|
| besar-10000k | 12,46 s | 28,90 s |
| besar-5000k | 7,28 s | 25,05 s |
| besar-1000k | 3,82 s | 21,00 s |
| samar-e | 2,33 s | 7,56 s |
| **36 berkas** | **59,89 s** | **202,69 s** |

### Dan di sini platformnya jadi terlihat telanjang

Sesudah mesinnya dua kali lebih cepat, benchmark bergantian diulang. Hasilnya
memisahkan mesin dari platform dengan sangat bersih:

| | Langflow | Service |
|---|---:|---:|
| engine, **tanpa** polling | 12.513 ms | 12.042 ms |
| engine, **dengan** polling 2 rps | **21.538 ms** | **12.123 ms** |
| kemunduran akibat polling | **+72%** | **+0,7%** |
| engine sebelum Arrow (polling 2 rps) | 21.685 ms | 23.893 ms |

Baca baris terakhirnya pelan-pelan: **Langflow tidak mendapat apa-apa dari
percepatan ini.** Mesinnya memang jadi 12,5 detik — terbukti saat polling
dimatikan — tapi begitu ada 2 permintaan status per detik, seluruh keuntungan
itu habis dimakan lapisan HTTP-nya sendiri, dan angkanya kembali ke tempat
semula. Service menyimpan hampir seluruhnya.

Leher botolnya pindah. Sebelumnya ongkos platform Langflow tersembunyi di
dalam grading yang memang lama; sekarang grading-nya separuh, dan ongkos itu
jadi bagian terbesar yang tersisa. Karena itu selisih p95 justru melebar —
**22,6x** dengan polling, dari 16,2x sebelum Arrow.

### Menjalankan sendiri

```powershell
docker exec synchrono-service python /synchrono/beban/uji_hibrida.py besar-1000k-padan --grade 5
docker exec synchrono-service python /synchrono/beban/profil_kamus.py besar-1000k
docker exec synchrono-service python /synchrono/beban/regresi_kamus.py

# Memisahkan mesin dari platform: jalankan dua kali, dengan dan tanpa polling
./jalankan.ps1 -Skenario skala_jutaan -Target langflow,service -Bergantian `
  -BerkasBesar besar-1000k -Bucket bucket-test -Bising 2 -BatasMenit 10
./jalankan.ps1 -Skenario skala_jutaan -Target langflow,service -Bergantian `
  -BerkasBesar besar-1000k -Bucket bucket-test -Bising 0 -BatasMenit 10
```

Log k6: **`beban/hasil/log-arrow-bergantian-k6.txt`** (dengan polling) dan
**`beban/hasil/log-arrow-tanpa-bising-k6.txt`** (tanpa polling).

---

## 1f. Apakah Langflow membatasi rps, dan bisakah dinaikkan?

**Limiternya ada, tapi bukan itu yang membatasi.** `rate_limit_enabled` menyala
secara bawaan dengan `rate_limit_per_minute: 5`, tapi ditelusuri di kodenya ia
cuma dipasang di tiga tempat:

| berkas | endpoint | batas |
|---|---|---|
| `api/v1/login.py` | login | `rate_limit_per_minute` = 5 |
| `api/v2/workflow_public.py` | workflow publik | `public_flow_rate_limit_per_minute` = 20 |
| `api/v1/chat.py` | build flow PUBLIC | `public_flow_rate_limit_per_minute` = 20 |

Jalur yang dipakai portal — `POST /api/v1/run/<flow>` di `api/v1/endpoints.py`
— tidak disentuh sama sekali. Angkanya sendiri sudah membuktikannya: 5 per
menit itu 0,083 rps, sementara yang terukur 4,6 rps.

### Yang membatasi: satu worker uvicorn

```
workers                = 1          <- ini
background_max_concurrency = 5
pool_size              = 20
database_url           = sqlite:////app/langflow-data/langflow.db
deactivate_tracing     = False
```

Semua permintaan API antre di SATU proses Python. Menaikkannya aman untuk
deployment ini karena status job hidup di tabel `grading_jobs` PostgreSQL,
bukan di memori proses — polling boleh mendarat di worker mana pun.

### Dinaikkan, lalu diukur

Kurva kapasitas, VU 1-16, 20 detik per tingkat:

| konfigurasi | plafon rps | p95 @ VU8 | memori idle |
|---|---:|---:|---:|
| 1 worker, tracing nyala | 4,38 | 3.163 ms | 1,22 GB |
| 1 worker, tracing mati | 4,62 | 2.614 ms | 1,22 GB |
| **4 worker**, tracing mati | **4,84** | **2.222 ms** | 3,08 GB |
| 8 worker, tracing mati | 4,92 | 2.752 ms | 4,40 GB |

Ketiga knob itu diuji terpisah, karena mengubah dua hal sekaligus lalu mengaku
tahu mana yang bekerja bukan pengukuran. Tracing menyumbang +5,5%, worker
menyumbang +4,8% lagi. Totalnya **+10%** — kecil.

**Plafonnya bukan soal konkurensi.** Satu permintaan status memakan ~465 ms CPU
di Langflow lawan ~50 ms di service; pada 4 inti, 4,8 rps sudah menghabiskan
seluruhnya. Menambah worker tidak menambah inti. 8 worker membuktikannya:
+1,7% rps, dengan memori 3,6x lipat.

### Tapi di beban sesungguhnya, bedanya jauh lebih besar

Kurva kapasitas hanya memukul endpoint status. Yang sebenarnya terjadi di
portal: satu berkas besar sedang digrading SELAMA menit-menit itu, dan polling
harus tetap dilayani. Diukur begitu:

| | 1 worker | 4 worker | |
|---|---:|---:|---|
| p95 polling saat grading **5 juta** | 6.155 ms | **1.528 ms** | **4,0x** |
| p95 polling saat grading **10 juta** | 2.177 ms | **1.224 ms** | **1,8x** |
| grading 5 juta | **167,3 s** | 179,1 s | |
| grading 10 juta | 318,8 s | **280,5 s** | |
| memori puncak | **1,48 GB** | 3,09 GB | |

Waktu grading-nya saling bertukar arah — 5 juta lebih lambat 7%, 10 juta lebih
cepat 12% — jadi itu derau tumpahan disk, bukan tren. Yang konsisten
**responsivitas polling**, dan itulah yang dirasakan orang yang menunggu di
depan portal.

### Kenapa defaultnya tetap 1

Dengan lebih dari satu worker, **kanvas dan playground Langflow berhenti
bekerja**. Langflow mencetaknya sendiri saat start:

```
THIS CONFIGURATION IS UNSUPPORTED WITH THE LANGFLOW UI AND WITH MCP OVER SSE.
  * The v1 /build editor and playground flows do not work. Build queues live in
    one worker's memory and the follow-up GET /api/v1/build/<job_id>/events
    request is round-robined to a different worker.
  * Rate limits are counted per worker ... the effective limit is multiplied by
    the worker count.
```

Jadi harganya bukan cuma memori. Kalau flow masih disunting lewat browser di
`:7860`, jangan dinaikkan.

Kalau deployment-nya API saja, naikkan begini:

```powershell
LANGFLOW_WORKERS=4 docker compose up -d langflow
```

Dan periksa memorinya dulu: 4 worker menahan 3,08 GB sementara
`DUCKDB_MEMORY_LIMIT` disetel 3 GB. Di mesin 5,79 GB keduanya muat — 10 juta
baris terbukti selesai tanpa OOM — tapi marginnya tipis. Obat yang benar untuk
mesin yang lebih kecil adalah `LANGFLOW_JOB_QUEUE_TYPE=redis`, yang membuat
multi-worker didukung penuh sekaligus mengembalikan UI-nya.

Log lengkap kedelapan jalan (empat kurva kapasitas, empat beban sesungguhnya):
**`beban/hasil/log-worker-langflow-k6.txt`**.

---

## 2. Menjalankan sendiri

### Sekali saja, untuk menyiapkan

```powershell
# Stack Langflow (menyediakan SeaweedFS + jaringan infra_default)
cd langflow-synchrono\infra
docker compose up -d

# Service
cd ..\..\synchrono-service
docker compose up -d --build
docker compose exec synchrono-service python infra/siapkan_seaweed.py

# Arahkan Langflow ke rujukan wilayah LOKAL — lihat bagian 4, ini penting
docker compose -f ..\langflow-synchrono\infra\docker-compose.yml `
               -f compose.langflow-lokal.yml up -d

# Pengamatan
cd beban
docker compose up -d --build
```

### Buktikan dulu bahwa keduanya setara

```powershell
cd ..
docker compose exec synchrono-service python infra/uji_asap.py
```

**Jangan lewati langkah ini.** Ia menjalankan grading di kedua sisi lalu
membandingkan 61 field muatan hasilnya. Kalau ada yang berbeda, angka apa pun
yang keluar dari k6 tidak bisa dipakai — service yang diam-diam melewatkan
pemeriksaan wilayah akan tampak lebih cepat, dan itu bukan kemenangan
melainkan pengukuran yang salah.

Harapannya: `19/19 pemeriksaan lulus` dan `IDENTIK — benchmark-nya sah.`

### Menjalankan beban

```powershell
cd beban

# Garis dasar: lapisan HTTP saja
.\jalankan.ps1 -Skenario dasar -Rps 50 -Detik 30

# Polling status pada laju yang keduanya masih sanggup
.\jalankan.ps1 -Skenario status -Rps 2 -Detik 45

# Sampai salah satu menyerah
.\jalankan.ps1 -Skenario status -Mode batas

# Campuran yang menyerupai pemakaian sesungguhnya
.\jalankan.ps1 -Skenario campuran -Rps 5 -Detik 120

# Satu sisi saja
.\jalankan.ps1 -Skenario status -Target service

# Kurva kapasitas: berapa batas atasnya, dan di mana ia mulai memburuk
.\kapasitas.ps1 -Target langflow
.\kapasitas.ps1 -Target service -Vu 1,4,16,64,141
```

Grafana: <http://localhost:3005/d/synchrono-beban> — masuk otomatis, tanpa login.

Hasil mentah tiap jalan tersimpan di `beban/hasil/` sebagai JSON.

---

## 3. Empat skenario, dan apa yang dijawab masing-masing

| Skenario | Menyentuh | Menjawab |
|---|---|---|
| `dasar` | tidak ada | Berapa harga lapisan HTTP-nya saja? |
| `status` | PostgreSQL | Endpoint yang paling sering dipanggil — ini yang paling penting |
| `aturan` | PostgreSQL | Biaya menyusun balasan besar (enam grade, tiga tabel) |
| `campuran` | semuanya | Apakah API tetap melayani selagi grading berjalan di latar? |
| `kapasitas` | PostgreSQL | **Berapa batas atasnya?** — punya skrip sendiri |

`kapasitas` berdiri terpisah karena bentuknya berbeda: closed loop, bukan
arrival rate. Empat yang pertama menjawab "berapa cepat pada beban sekian";
yang ini menjawab "berapa beban yang sanggup ditanggung". Kalau sebuah angka
rps terlihat aneh, kurva kapasitaslah yang biasanya menjelaskannya — termasuk
kenapa 10 rps bisa menghasilkan lebih sedikit daripada 2 rps.

`status` adalah yang paling mewakili. Portal memanggilnya berulang-ulang tiap
2–3 detik per berkas, sementara dispatch hanya sekali per unggahan. Kalau cuma
sempat menjalankan satu, jalankan yang itu.

`campuran` **menulis**: tiap dispatch menambah baris di `grading_jobs` dan
menimpa enriched.parquet berkas yang bersangkutan. Aman diulang, tapi jangan
dijalankan sambil ada yang memakai data hasil grading untuk hal lain.

---

## 4. Lima hal yang membuat angkanya bisa dipercaya

Tanpa kelimanya, benchmark ini akan tetap menghasilkan angka — angka yang
salah, dan tidak ada yang memberi tahu.

**1. Logikanya satu, bukan dua salinan.** Folder `lib/` yang sama di-mount ke
kedua container. Apa pun selisih yang terukur, ia datang dari lapisan yang
membungkus, bukan dari perbedaan logika.

**2. Versinya disamakan.** DuckDB 1.5.5, Python 3.14.7, rapidfuzz 3.14.5 di
kedua sisi — Python-nya sempat berbeda (3.12 vs 3.14) dan itu sudah diperbaiki,
karena "sebagian selisihnya mungkin selisih penafsir Python" adalah bantahan
yang wajar dan mudah dihindari.

**3. Rujukan wilayah dibaca dari SeaweedFS lokal di kedua sisi.** Bawaannya,
Langflow membacanya dari server lewat VPN. Kalau dibiarkan, tiap job grading di
sisi Langflow menanggung satu perjalanan lintas jaringan yang tidak ditanggung
sisi service — dan selisih yang terbaca sebagian besar adalah selisih VPN.
Itulah gunanya `compose.langflow-lokal.yml`.

**4. Beban diatur per laju kedatangan, bukan per jumlah VU.** Dengan jumlah VU
tetap, tiap VU menunggu balasan sebelum mengirim lagi — jadi platform yang
lebih lambat otomatis menerima lebih sedikit permintaan, dan keduanya tampak
sama-sama sanggup. Yang sesungguhnya berbeda justru tidak terukur.

**5. Check mengurai badan balasan, bukan cuma melihat kode status.** Langflow
membalas 200 untuk apa pun, termasuk eksekusi yang gagal. Skenario yang hanya
memeriksa `status === 200` akan melaporkan Langflow 100% sehat sambil tidak
satu pun permintaannya berhasil — dan latency kegagalan selalu tampak bagus.

---

## 5. Dua hal yang sengaja TIDAK disamakan

Disebutkan terus terang, supaya bisa ditutup kalau memang ingin diukur
seketat mungkin.

**Log akses.** Langflow menuliskan satu baris per permintaan; service ini tidak
(`ACCESS_LOG=0`). Pada beban ribuan permintaan per menit, biaya itu tidak nol.
Setel `ACCESS_LOG=1` di `docker-compose.yml` untuk menutupnya.

**Koneksi DuckDB dipakai ulang.** Langflow membuka koneksi baru tiap eksekusi
node dan menutupnya lagi — memang tidak ada tempat menyimpannya, node hanya
hidup selama satu eksekusi. Service punya proses yang hidup terus, jadi ia
memakai kolam.

Ini bukan kecurangan: ia justru salah satu keuntungan nyata dari meninggalkan
Langflow. Tapi untuk memisahkan berapa yang dihemat karena **meninggalkan
Langflow** dan berapa karena **berhenti membuka koneksi berulang**, setel:

```yaml
DUCKDB_MODE: "per_request"
```

lalu jalankan ulang. Selisihnya terbaca sendiri.

---

## 6. Perkakas: kenapa begini

**k6 + Prometheus + Grafana**, ditambah satu pengukur kecil buatan sendiri.

Tiga sumber angka, masing-masing menjawab hal berbeda, pada satu sumbu waktu:

| Sumber | Menjawab |
|---|---|
| k6 | latency dan throughput, dari sisi **pemanggil** |
| pengukur | CPU dan memori per container, dari sisi **yang dipanggil** |
| Grafana | keduanya berdampingan |

Sumbu waktu yang sama itulah kuncinya: latency yang memanjang **bersamaan**
dengan memori yang naik menceritakan sesuatu yang tidak bisa diceritakan oleh
salah satunya sendirian.

k6 mengirim hasilnya ke Prometheus lewat **remote write**, bukan di-scrape. k6
hidup sebentar lalu mati; kalau menunggu di-scrape, sebagian besar angkanya
hilang bersama prosesnya.

### Kenapa bukan cAdvisor

cAdvisor adalah pilihan pertama dan sudah dicoba. Di Docker Desktop mesin ini
ia **tidak bisa menghitung container sama sekali** — yang keluar hanya
`container_memory_working_set_bytes{id="/"}`, cgroup akar, tanpa satu pun
container, sementara Prometheus tetap melaporkan targetnya `up`. Penyebabnya
ada di lognya: Docker Desktop memakai containerd image store, jadi folder
`/var/lib/docker/image/overlayfs/layerdb` yang dicari cAdvisor memang tidak ada.
Dicoba juga dengan `/sys/fs/cgroup` ditambahkan dan tanpa `/var/lib/docker` —
hasilnya sama.

Penggantinya (`beban/pengukur/`) membaca Docker API langsung, delapan puluh
baris tanpa dependency, dan mengekspor dengan **nama metrik yang sama persis**
seperti cAdvisor. Jadi dasbor Grafana-nya tidak berubah, dan kalau nanti
dipasang di server Linux sungguhan, cAdvisor tinggal menggantikannya tanpa satu
pun panel yang perlu disentuh. Angkanya sama dengan `docker stats`, karena
sumbernya memang endpoint yang sama.

---

## 7. Kalau ada yang tidak beres

**Semua endpoint balas 500 di kedua sisi** → PostgreSQL mati. Nyalakan DBngin.
Ini selalu tersangka pertama. Di sisi Langflow gejalanya 500 berbadan kosong
dan penyebabnya hanya terlihat di `docker compose logs langflow`.

**Panel latency di Grafana terbaca seribu kali lebih kecil** → unit panelnya
salah. k6 mengirim durasi ke Prometheus dalam **detik**, berbeda dari ringkasan
yang dicetaknya di terminal (milidetik). Panel latency harus memakai unit `s`.

**Panel memori/CPU kosong** → pengukur tidak terjangkau, atau Prometheus belum
memuat ulang konfigurasinya. `docker compose restart prometheus`, lalu periksa
<http://localhost:9090/targets>.

**Grafik k6 kosong padahal k6 jalan** → `K6_PROMETHEUS_RW_TREND_STATS` tidak
disetel, jadi p95 tidak pernah dikirim; atau Prometheus dijalankan tanpa
`--web.enable-remote-write-receiver` dan menolak dengan 404 tanpa k6 mengeluh.
Keduanya sudah disetel di repo ini.

**`jalankan.ps1` penuh galat "Unexpected token"** → encoding-nya hilang BOM.
Windows PowerShell 5.1 membaca `.ps1` tanpa BOM sebagai ANSI.
`Get-Content jalankan.ps1 -Encoding Byte -TotalCount 3` harus menjawab
`239 187 191`.

**p99 terbaca 0,0 ms di ringkasan k6** → p99 tidak ada di bawaan k6
(`avg, min, med, max, p90, p95`), dan ketiadaannya tidak menimbulkan galat —
ia hanya muncul sebagai nol. Sudah ditutup lewat `summaryTrendStats`.

**Langflow menolak koneksi tepat setelah jalan sebelumnya** → ia masih
menghabiskan antrean eksekusi. `jalankan.ps1` sudah menunggu sasaran siap;
kalau masih kurang, naikkan `-Jeda`.
