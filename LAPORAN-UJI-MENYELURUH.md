# Laporan Pengujian Menyeluruh: Langflow vs Service Python

**Tanggal pengujian:** 20 September 2026
**Rangkaian:** `beban/uji_menyeluruh.ps1` — sebelas tahap berurutan, satu jalan
**Catatan mentah:** `beban/hasil/menyeluruh-20260920-203430/`
**Menggantikan:** rangkaian 18 September 2026 (`menyeluruh-20260918-102928`).
Alasan penggantian dijelaskan pada bagian 6.

Seluruh angka pada laporan ini berasal dari **satu rangkaian pengujian yang
sama**, dijalankan berurutan tanpa jeda konfigurasi. Hal ini disengaja: angka
kapasitas yang diukur hari ini dan angka skala yang diukur besok tidak boleh
diletakkan pada tabel yang sama.

Rangkaian ini dijalankan setelah dua optimasi terakhir dipasang, yaitu
percepatan pencocokan kamus melalui Arrow dan materialisasi sumber CSV.
Keduanya dipasang pada folder `lib/` bersama, sehingga berlaku untuk kedua
sisi.

---

## 1. Ringkasan Eksekutif

| Aspek | Langflow | Service Python | Selisih |
|---|---:|---:|---|
| Waktu tanggap lapisan API (p95) | 14,0 ms | 4,0 ms | **3,5×** |
| Waktu tanggap polling, 2 rps (p95) | 648,0 ms | 107,3 ms | **6,0×** |
| Waktu tanggap polling, 10 rps (p95) | 36.517,4 ms | 93,0 ms | **392,6×** |
| Kapasitas maksimum | 4,46 rps | 73,45 rps | **16,5×** |
| Memori saat menganggur | 1,23 GB | 61 MB | **20×** |
| Ongkos tetap satu pemanggilan API | ~480 ms | ~41–72 ms | **7–12×** |
| Penilaian 10 juta baris | 311,7 detik | 127,3 detik | **2,45×** |
| Portal mengetahui hasil 10 juta baris | 313,9 detik | 128,8 detik | **2,44×** |

**Dua temuan utama.**

**Pertama, kegagalan pelaporan pada berkas besar tidak terulang.** Pada
rangkaian 18 September, penilaian 5 juta baris selesai tetapi portal tidak
pernah mengetahuinya selama 20 menit pengamatan, dan pada 10 juta baris kabar
selesainya baru sampai 17 menit setelah pekerjaannya rampung. Pada rangkaian
ini, ketiga ukuran berkas dilaporkan dalam **kurang dari 2,3 detik** setelah
penilaian selesai, dan 96–98% permintaan status terjawab. Perubahan yang
menyebabkannya bukan optimasi, melainkan **container yang dinyalakan ulang
sebelum tiap tahap diukur** (bagian 6.2).

**Kedua, selisihnya mengecil tetapi arahnya tidak berubah.** Pada kondisi
paling menguntungkan bagi Langflow — container segar, tanpa sisa beban tahap
sebelumnya — service Python tetap lebih cepat pada **seluruh** tahap, mulai
dari 3,5 kali pada endpoint paling ringan sampai 392,6 kali pada polling 10
permintaan per detik.

---

## 2. Metodologi

### 2.1 Lingkungan

| Komponen | Keterangan |
|---|---|
| Mesin | 4 inti CPU, memori 5,79 GB |
| Langflow | 1 proses pekerja, tracing dinonaktifkan |
| Service | FastAPI + uvicorn |
| Logika penilaian | Folder `lib/` yang sama, dipasang ke kedua container |
| Batas memori pengolah data | `DUCKDB_MEMORY_LIMIT=3GB` pada kedua sisi |
| Data master | 299.088 baris |
| Penyimpanan | SeaweedFS lokal |
| Pembangkit beban | k6, dijalankan dari container terpisah |
| Optimasi terpasang | Arrow pada pencocokan kamus, materialisasi sumber CSV — terverifikasi hidup di kedua container sebelum rangkaian dimulai |

### 2.2 Mode bergantian, dengan container disegarkan

Seluruh tahap dijalankan dengan container yang **tidak sedang diukur
dimatikan**, dan container yang **sedang diukur dinyalakan ulang** lebih dulu.

Dua alasannya:

1. Langflow menahan 1,23 GB memori meskipun menganggur. Apabila kedua container
   dibiarkan hidup, sisi service akan diukur dengan sisa memori yang lebih
   sedikit dibandingkan sebaliknya.
2. Tanpa penyegaran, sisi yang diukur belakangan selalu mendapat container baru
   — karena dimatikan lebih dulu — sedangkan sisi yang diukur duluan memakai
   container yang mungkin sudah hidup berjam-jam dan menanggung sisa beban
   tahap sebelumnya.

Butir kedua adalah perbaikan terhadap rangkaian 18 September, dan merupakan
penyebab terbesar perbedaan angka antara kedua rangkaian.

### 2.3 Daftar tahap

| # | Tahap | Beban | Durasi terukur |
|---|---|---|---|
| 1 | Garis dasar | 50 permintaan/detik | 154 dtk |
| 2 | Polling status | 2 permintaan/detik | 168 dtk |
| 3 | Polling status | 10 permintaan/detik | 192 dtk |
| 4 | Baca aturan grade | 5 permintaan/detik | 165 dtk |
| 5 | Campuran | 2 permintaan/detik | 198 dtk |
| 5b | Per endpoint grading | 2 permintaan/detik, berurutan | 543 dtk |
| 6 | Kurva kapasitas Langflow | 1 s.d. 141 penanya | 306 dtk |
| 7 | Kurva kapasitas Service | 1 s.d. 141 penanya | 164 dtk |
| 8 | Skala 1 juta baris | 1 berkas + 2 rps latar | 222 dtk |
| 9 | Skala 5 juta baris | 1 berkas + 2 rps latar | 330 dtk |
| 10 | Skala 10 juta baris | 1 berkas + 2 rps latar | 560 dtk |

Total 2.802 detik, atau sekitar 47 menit.

### 2.4 Validitas

| Risiko | Penanganan |
|---|---|
| Logika kedua sisi berbeda | Folder `lib/` yang sama dipasang ke kedua container; keberadaan kedua optimasi diperiksa di dalam container sebelum rangkaian dimulai |
| Versi komponen berbeda | DuckDB 1.5.5, Python 3.14.7, rapidfuzz 3.14.5, pyarrow 23.0.1 disamakan |
| Jaringan kantor ikut terukur | Seluruh rujukan dibaca dari penyimpanan lokal |
| Kegagalan terbaca sebagai keberhasilan | Pemeriksaan mengurai isi balasan, bukan kode status. Langflow membalas 200 untuk seluruh kondisi |
| Urutan pengujian memengaruhi hasil | Container disegarkan sebelum tiap pengukuran (bagian 2.2) |
| Beban tidak setara | Skenario 1–5 memakai laju kedatangan tetap, sehingga kedua sisi menerima jumlah permintaan yang sama |
| Angka mutlak bergantung keadaan mesin | Kapasitas service diukur dua kali pada hari yang sama: 73,45 dan 72,48 rps — selisih 1,3% |

---

## 3. Hasil Pengujian Lapisan API

### 3.1 Garis dasar — 50 permintaan/detik

Endpoint yang tidak menyentuh basis data. Yang terukur adalah ongkos lapisan
API secara murni.

| Pengukuran | Langflow | Service | Selisih |
|---|---:|---:|---|
| p95 | 14,0 ms | 4,0 ms | 3,5× |
| p99 | 21,9 ms | 5,0 ms | 4,4× |
| Rata-rata | 10,6 ms | 2,4 ms | 4,4× |
| Terlayani | 50,0 rps | 50,0 rps | sama |
| Berhasil | 100% | 100% | sama |
| CPU puncak | 62,9% | 32,3% | 1,9× |
| Memori proses | 1.177 MB | 62 MB | 19,0× |

Pada beban ini kedua sisi melayani seluruh permintaan. Perbedaannya pada waktu
tempuh, pemakaian CPU, dan konsumsi memori.

### 3.2 Polling status — 2 permintaan/detik

Beban yang setara dengan lima berkas ditunggu bersamaan oleh portal.

| Pengukuran | Langflow | Service | Selisih |
|---|---:|---:|---|
| p95 | 648,0 ms | 107,3 ms | 6,0× |
| p99 | 732,9 ms | 141,4 ms | 5,2× |
| Rata-rata | 520,3 ms | 68,7 ms | 7,6× |
| Terlayani | 2,0 rps | 2,0 rps | sama |
| CPU puncak | 154,3% | 30,8% | 5,0× |

Kedua sisi masih melayani seluruh permintaan. Namun CPU Langflow telah mencapai
154% dari 400% yang tersedia, hanya untuk melayani 2 permintaan per detik.

### 3.3 Polling status — 10 permintaan/detik

| Pengukuran | Langflow | Service | Selisih |
|---|---:|---:|---|
| p95 | 36.517,4 ms | 93,0 ms | **392,6×** |
| p99 | 40.841,8 ms | 111,2 ms | 367× |
| Rata-rata | 19.525,7 ms | 70,7 ms | 276× |
| Terlayani | 3,6 rps | 10,0 rps | — |

Langflow hanya mampu melayani 3,6 dari 10 permintaan per detik yang dikirim.
Sisanya mengantre, dan waktu tunggu p95 mencapai **36,5 detik**.

Inilah tahap dengan selisih terbesar pada seluruh rangkaian, dan bebannya
bukan beban yang tidak masuk akal: 10 permintaan status per detik setara
dengan sekitar 25 berkas yang sedang ditunggu portal bersamaan.

### 3.4 Baca aturan grade — 5 permintaan/detik

| Pengukuran | Langflow | Service | Selisih |
|---|---:|---:|---|
| p95 | 4.383,0 ms | 57,6 ms | **76,0×** |
| p99 | 6.088,3 ms | 78,5 ms | 77,6× |
| Terlayani | 4,3 rps | 5,0 rps | — |
| Iterasi dibuang | 8 | 0 | — |

Endpoint ini hanya membaca konfigurasi dan merupakan yang paling ringan dari
keenam API. Meskipun demikian, pada 5 permintaan per detik Langflow sudah tidak
sanggup melayani seluruhnya.

### 3.5 Campuran

Menirukan pemakaian sesungguhnya: pengiriman berkas, pemeriksaan status, dan
pembacaan aturan secara bergantian.

| Pengukuran | Langflow | Service | Selisih |
|---|---:|---:|---|
| p95 | 1.295,6 ms | 107,8 ms | 12,0× |
| p99 | 2.077,7 ms | 182,3 ms | 11,4× |
| Rata-rata | 861,3 ms | 68,9 ms | 12,5× |
| Terlayani | 2,9 rps | 3,0 rps | — |
| CPU puncak | 270,8% | 191,6% | 1,4× |

---

## 3A. Hasil Pengujian Per Endpoint Grading

Bagian ini mengukur **ongkos masing-masing endpoint** pada laju yang sama-sama
sanggup dilayani kedua sisi, sehingga yang terbaca adalah ongkos endpointnya,
bukan panjang antrean.

### 3A.1 Cara pengukuran

Kelima endpoint dijalankan **berurutan, bukan bersamaan**, masing-masing 25
detik. Menjalankannya bersamaan akan membuat endpoint berat menghabiskan CPU,
dan endpoint ringan ikut terlihat lambat.

| Pengaturan | Nilai |
|---|---|
| Laju empat endpoint ringan | 2 permintaan/detik |
| Laju endpoint `grading` sinkron | 1 permintaan tiap 2 detik |
| Jatah waktu tiap endpoint | 25 detik |
| Jarak antar endpoint | 45 detik |
| Berkas yang dipakai | berkas uji 3.000 baris |

Endpoint `config-rules-update` dijalankan dengan `dryRun` dan nilai yang sama
dengan yang tersimpan, sehingga konfigurasi grading tidak berubah.

### 3A.2 Hasil — p95

| Endpoint | n (LF/SVC) | Langflow | Service | Selisih |
|---|---:|---:|---:|---|
| `config-rules` | 50/51 | 641,7 ms | 54,1 ms | 11,9× |
| `config-rules-update` | 51/50 | 577,6 ms | 70,9 ms | 8,1× |
| `grading-status` | 51/50 | 574,8 ms | 93,0 ms | 6,2× |
| `grading-dispatch` | 50/50 | **859,7 ms** | 106,6 ms | **8,1×** |
| `grading` (sinkron) | —/13 | tidak terukur | 761,8 ms | — |

### 3A.3 Hasil — rata-rata

| Endpoint | Langflow | Service | Selisih |
|---|---:|---:|---|
| `config-rules` | 482,4 ms | 41,0 ms | 11,8× |
| `config-rules-update` | 479,8 ms | 54,8 ms | 8,8× |
| `grading-status` | 481,4 ms | 71,5 ms | 6,7× |
| `grading-dispatch` | 703,6 ms | 59,5 ms | 11,8× |
| `grading` (sinkron) | tidak terukur | 704,6 ms | — |

Sisi service mencatat 365 pemeriksaan lulus tanpa kegagalan. Sisi Langflow 351
lulus dengan 2 kegagalan, keduanya berasal dari satu permintaan
`grading-status` yang koneksinya diputus sepihak oleh Langflow
(*connection reset by peer*).

### 3A.4 Pembacaan

**Empat endpoint ringan Langflow berkumpul di sekitar 480 milidetik**, yaitu
482,4 — 479,8 — 481,4. Ketiganya mengerjakan hal yang sangat berbeda: membaca
konfigurasi, memvalidasi perubahan, dan membaca status pekerjaan. Bahwa
ketiganya memakan waktu yang hampir sama menunjukkan angka tersebut bukan
ongkos pekerjaannya, melainkan **ongkos tetap satu pemanggilan alur Langflow**.

Pengelompokan ini lebih rapat daripada rangkaian 18 September, yang mencatat
519,5 — 533,6 — 527,8 milidetik. Kedua rangkaian menunjuk kesimpulan yang sama.

Sisi service tidak menunjukkan pola itu: 41,0 — 54,8 — 71,5 milidetik, sesuai
berat pekerjaan masing-masing.

**`grading-dispatch` tetap yang paling mahal di Langflow**, yaitu 703,6 ms
rata-rata atau 11,8 kali. Endpoint ini mencatat pekerjaan lalu melepasnya ke
proses latar, sehingga seharusnya menjadi salah satu yang paling ringan. Di
sisi service memang demikian, yaitu 59,5 ms.

> **Yang tidak terukur, dan itu sendiri sebuah temuan.** Endpoint `grading`
> sinkron pada sisi Langflow tidak menghasilkan satu pun iterasi. Pengukuran
> diulang secara terpisah terhadap Langflow saja, dan hasilnya sama: nol
> iterasi. Pada jendela 25 detik dengan satu permintaan tiap 2 detik, sisi
> service menuntaskan 13 penilaian sinkron berkas 3.000 baris; sisi Langflow
> tidak menuntaskan satu pun, bahkan setelah tambahan 30 detik masa tenggang.
> Ini sejalan dengan Langflow yang hanya memiliki satu proses pekerja: ketiga
> belas permintaan itu dikerjakan berurutan, bukan bersamaan.
>
> Rangkaian 18 September mencatat endpoint ini pada 956,7 ms p95 berbanding
> 765,8 ms. Perbedaan sebesar itu antara dua rangkaian belum dapat dijelaskan,
> sehingga angka 18 September tidak dipakai dan tidak dicantumkan sebagai
> hasil.
>
> Ulangan tersebut juga menegaskan keempat endpoint lainnya: rata-rata
> `config-rules-update` 569,6 ms, `grading-status` 553,4 ms, dan
> `grading-dispatch` 777,4 ms — urutan dan besarannya sama dengan rangkaian
> utama.

---

## 4. Kurva Kapasitas

Jumlah penanya dinaikkan bertahap sampai throughput berhenti bertambah.

### 4.1 Langflow

| Penanya | rps | Rata-rata | p95 |
|---:|---:|---:|---:|
| 1 | 2,12 | 471,8 ms | 566,6 ms |
| 2 | 3,60 | 552,2 ms | 679,1 ms |
| 4 | 4,38 | 905,4 ms | 1.061,8 ms |
| **8** | **4,46** | 1.744,8 ms | 2.372,3 ms |
| 16 | 4,33 | 3.556,2 ms | 6.784,8 ms |
| 32 | 4,39 | 6.682,0 ms | 14.312,1 ms |
| 64 | 4,13 | 13.598,3 ms | 25.953,5 ms |
| 141 | **2,63** | 26.397,9 ms | 29.549,6 ms |

### 4.2 Service Python

| Penanya | rps | Rata-rata | p95 |
|---:|---:|---:|---:|
| 1 | 17,65 | 56,4 ms | 70,7 ms |
| 4 | 52,55 | 75,6 ms | 93,6 ms |
| 16 | 71,94 | 221,1 ms | 323,0 ms |
| **64** | **73,45** | 856,7 ms | 1.316,7 ms |
| 141 | 73,41 | 1.866,3 ms | 2.642,1 ms |

Kurva ini diukur ulang pada hari yang sama dan menghasilkan 16,32 — 52,30 —
69,45 — 72,48 — 69,45 rps. Selisih pada puncaknya 1,3%.

### 4.3 Perbandingan

| | Langflow | Service |
|---|---:|---:|
| Kapasitas maksimum | 4,46 rps | 73,45 rps |
| Perilaku pada 141 penanya | turun ke 2,63 rps | bertahan di 73,41 rps |
| Waktu tunggu pada 141 penanya | 29.549,6 ms | 2.642,1 ms |

**Selisih kapasitas: 16,5 kali.**

Perbedaan penting terletak pada perilaku setelah jenuh. Service bertahan datar
di sekitar 73 rps dari 16 sampai 141 penanya. Langflow menurun, dan pada 141
penanya throughput-nya tinggal 2,63 rps, yaitu 59% dari kapasitasnya sendiri.

Kedua sisi lebih baik dalam hal ini dibandingkan rangkaian 18 September, ketika
Langflow anjlok sampai 0,30 rps. Yang membaik adalah perilaku Langflow saat
kelebihan beban, bukan kapasitas puncaknya.

---

## 5. Hasil Pengujian Skala Dukcapil

Satu berkas besar dikirim, kemudian statusnya ditanyakan berulang sampai
selesai, sambil 2 permintaan latar per detik tetap berjalan.

### 5.1 Ikhtisar

| Baris | Sisi | Penilaian selesai | Portal mengetahui | Jeda | Waktu tanggap p95 | Permintaan berhasil |
|---|---|---:|---:|---:|---:|---:|
| 1 juta | Langflow | 101,6 dtk | 103,4 dtk | 1,7 dtk | 1.058,4 ms | 96,4% |
| 1 juta | Service | 10,9 dtk | 12,4 dtk | 1,5 dtk | 135,8 ms | 100% |
| 5 juta | Langflow | 169,0 dtk | 171,2 dtk | 2,2 dtk | 8.038,6 ms | 97,7% |
| 5 juta | Service | 49,2 dtk | 49,8 dtk | 0,5 dtk | 147,3 ms | 100% |
| 10 juta | Langflow | 311,7 dtk | 313,9 dtk | 2,2 dtk | 2.595,9 ms | 98,5% |
| 10 juta | Service | 127,3 dtk | 128,8 dtk | 1,5 dtk | 149,0 ms | 100% |

Kolom "penilaian selesai" adalah `engine` yang dilaporkan mesin penilaian,
kolom "portal mengetahui" adalah saat permintaan status pertama kali berhasil
membawa kabar selesai.

### 5.2 Kegagalan pelaporan tidak terulang

Ini perubahan terbesar dibandingkan rangkaian 18 September.

| | 18 September | 20 September |
|---|---|---|
| Jeda lapor, 5 juta baris | tidak pernah sampai dalam 20 menit | 2,2 detik |
| Jeda lapor, 10 juta baris | 1.008 detik | 2,2 detik |
| Permintaan terjawab, 5 juta | 9,2% | 97,7% |
| Permintaan terjawab, 10 juta | 6,1% | 98,5% |

Penyebabnya bukan optimasi pada logika penilaian. Optimasi tersebut
mempercepat pekerjaannya, bukan memperbaiki lapisan API.

Penyebabnya adalah **container Langflow disegarkan sebelum diukur**. Pada
rangkaian 18 September, tahap 9 dan 10 dijalankan oleh container Langflow yang
sudah hidup lebih dari satu jam dan telah melewati delapan tahap beban. Pada
rangkaian ini, tiap tahap berangkat dari container yang baru dinyalakan.

Hal ini menguatkan temuan yang sudah dicatat pada laporan sebelumnya:
**jejak memori dan kondisi Langflow memburuk seiring lama hidupnya**, dan
pemburukan itulah — bukan ukuran berkasnya — yang membuat lapisan API berhenti
melaporkan hasil. Untuk penerapan di server, Langflow perlu dinyalakan ulang
secara berkala atau dipantau pemakaian memorinya.

### 5.3 Waktu penilaian

| Baris | Langflow | Service | Selisih | Langflow per juta baris | Service per juta baris |
|---|---:|---:|---|---:|---:|
| 1 juta | 101,6 dtk | 10,9 dtk | **9,29×** | 101,6 dtk | 10,9 dtk |
| 5 juta | 169,0 dtk | 49,2 dtk | **3,43×** | 33,8 dtk | 9,8 dtk |
| 10 juta | 311,7 dtk | 127,3 dtk | **2,45×** | 31,2 dtk | 12,7 dtk |

Logika penilaiannya identik, sehingga selisih ini bersumber dari perebutan CPU:
lapisan API Langflow memakai daya yang seharusnya dipakai penilaian. Selama
tahap ini berlangsung 2 permintaan latar per detik tetap dikirim, dan tiap
permintaan itu memakan sekitar 480 ms ongkos tetap di sisi Langflow.

**Sisi service naik hampir lurus terhadap jumlah baris**, yaitu 9,8 sampai 12,7
detik per juta baris. **Sisi Langflow tidak**: satu juta baris memakan 101,6
detik per juta, sedangkan sepuluh juta baris hanya 31,2 detik per juta.
Artinya sekitar **70 detik** pada angka 1 juta baris bukan ongkos pengolahan
data, melainkan ongkos tetap yang dibayar sekali di awal — pemanasan alur
setelah container disegarkan. Pada berkas besar ongkos itu tenggelam; pada
berkas kecil ia mendominasi.

### 5.4 Kebutuhan memori

Yang ditampilkan adalah **memori proses**, dibaca dari `anon` pada cgroup
container, bukan angka gabungan `docker stats` yang mencakup cache berkas.

| Baris | Langflow | Service | Selisih |
|---|---:|---:|---:|
| 1 juta | 3.439 MB | 2.017 MB | +1.422 MB |
| 5 juta | 4.058 MB | 3.239 MB | +819 MB |
| 10 juta | 4.057 MB | 3.353 MB | +704 MB |

Dua pola yang terbaca:

**Keduanya mendatar di atas 5 juta baris.** `DUCKDB_MEMORY_LIMIT` disetel 3 GB
pada kedua sisi; di atas itu pengolah data menumpahkan ke disk, bukan menambah
memori. Service berhenti di 3,2–3,4 GB, Langflow di sekitar 4,06 GB.

**Selisihnya adalah jejak dasar Langflow.** Langflow memakai batas memori yang
sama untuk pengolahan datanya, tetapi menumpuk jejak platformnya sendiri di
atas itu. Pada 10 juta baris Langflow memakai 68% dari 5,79 GB yang tersedia,
sementara service 56%.

Untuk perencanaan kapasitas server: sediakan kurang lebih **0,7 sampai 1,4 GB
lebih banyak** apabila Langflow yang melayani, untuk pekerjaan yang identik.

### 5.5 Catatan pengukur memori

Pengukur memori diperbaiki sebelum rangkaian 18 September dilaporkan; dua
cacatnya adalah nilai yang ditimpa alih-alih diambil maksimumnya, dan jendela
pengamatan yang hanya 70 detik untuk jalan yang berlangsung puluhan menit.
Rangkaian ini memakai pengukur yang sudah diperbaiki sejak awal, sehingga
seluruh angka pada tabel 5.4 adalah puncak sesungguhnya sepanjang jalan.

---

## 6. Perbedaan Terhadap Rangkaian 18 September

### 6.1 Angka yang berubah

| Pengukuran | 18 Sep | 20 Sep | Arah |
|---|---:|---:|---|
| Garis dasar p95, Langflow | 44,0 ms | 14,0 ms | Langflow membaik |
| Polling 2 rps p95, Langflow | 1.400,6 ms | 648,0 ms | Langflow membaik |
| Polling 10 rps, selisih | 520,0× | 392,6× | menyempit |
| Baca aturan, selisih | 359,7× | 76,0× | menyempit |
| Campuran, selisih | 48,8× | 12,0× | menyempit |
| Kapasitas Langflow | 3,11 rps | 4,46 rps | Langflow membaik |
| Kapasitas Service | 98,94 rps | 73,45 rps | Service menurun |
| Selisih kapasitas | 31,8× | 16,5× | menyempit |
| Penilaian 10 juta, Langflow | 386,0 dtk | 311,7 dtk | membaik |
| Penilaian 10 juta, Service | 133,4 dtk | 127,3 dtk | membaik sedikit |
| Jeda lapor 10 juta, Langflow | 1.008 dtk | 2,2 dtk | membaik jauh |

### 6.2 Penyebabnya: cara mengukur, bukan optimasi

Penyempitan selisih ini **tidak boleh dibaca sebagai hasil optimasi.**
Optimasi yang dipasang di antara kedua rangkaian menyentuh logika penilaian,
yaitu pencocokan kamus dan pembacaan sumber CSV. Tahap 1 sampai 7 tidak
menjalankan penilaian sama sekali, sehingga tidak mungkin terpengaruh olehnya.

Yang berubah adalah **container yang diukur kini dinyalakan ulang lebih dulu**.
Pada rangkaian 18 September, tahap 4 dijalankan oleh Langflow yang baru saja
menanggung tahap 3, yaitu 10 permintaan per detik selama 30 detik dengan
antrean yang p95-nya 45 detik. Sisa antrean itu masih dikerjakan ketika tahap 4
mulai mengukur.

Dugaan ini diperiksa secara terpisah. Skenario baca aturan dijalankan tiga kali
berturut-turut terhadap Langflow yang sama tanpa penyegaran:

| Putaran | p95 | p99 |
|---:|---:|---:|
| 1 | 3.140,2 ms | 4.032,6 ms |
| 2 | 2.946,8 ms | 3.817,0 ms |
| 3 | **5.310,5 ms** | **8.216,5 ms** |

Waktu tanggap Langflow memburuk 69% setelah lima menit beban ringan tanpa
penyegaran. Beban tahap 3 jauh lebih berat daripada percobaan ini, sehingga
arah dan besaran penyempitan pada tabel 6.1 konsisten dengan penjelasan ini.

Penurunan kapasitas service dari 98,94 ke 73,45 rps juga bukan regresi
perangkat lunak: diukur dua kali pada hari yang sama, hasilnya 73,45 dan 72,48
rps. Angka mutlak kapasitas bergantung pada keadaan mesin saat itu.

### 6.3 Rangkaian mana yang dipakai

**Rangkaian 20 September.** Cara pengukurannya lebih setara: kedua sisi selalu
berangkat dari container yang baru dinyalakan, sehingga tidak ada sisi yang
menanggung sisa beban tahap sebelumnya.

Perlu ditekankan bahwa rangkaian ini adalah **kondisi terbaik bagi Langflow**.
Pada penggunaan sesungguhnya, Langflow tidak dinyalakan ulang tiap beberapa
menit. Angka yang dialami portal akan berada di antara kedua rangkaian ini,
dan bergeser ke arah rangkaian 18 September seiring lamanya Langflow hidup.

### 6.4 Efek optimasi, diukur terpisah

Karena rangkaian ini tidak bisa dipakai untuk mengukur efek optimasi, efeknya
diukur pada jalan tersendiri dengan berkas yang sama dan mesin yang sama:

| Optimasi | Sebelum | Sesudah | Selisih |
|---|---:|---:|---|
| Arrow pada pencocokan kamus, tahap `_lapis_kamus` | 17,93 dtk | 0,26 dtk | 68× |
| Arrow, seluruh pipeline normalisasi | 34,17 dtk | 11,64 dtk | 2,9× |
| Materialisasi sumber CSV, berkas 1 juta baris | 193,5 dtk | 35,9 dtk | 5,4× |

Ketiganya diverifikasi tidak mengubah hasil: 29 berkas uji menghasilkan
pemetaan kolom yang sama persis, dan berkas verif-A sampai verif-E tetap
menghasilkan grade A, B, C, D, E.

---

## 7. Kesimpulan

1. **Kedua sisi menghasilkan penilaian yang benar.** Seluruh berkas uji
   menghasilkan grade yang sama, dan muatan hasilnya identik.

2. **Service Python lebih cepat pada seluruh tahap**, dari 3,5 kali pada
   endpoint paling ringan sampai 392,6 kali pada polling 10 permintaan per
   detik — dan itu pada kondisi yang paling menguntungkan Langflow.

3. **Selisih kapasitas 16,5 kali.** Langflow jenuh pada 4,46 permintaan per
   detik, service pada 73,45.

4. **Ongkos tetap satu pemanggilan alur Langflow sekitar 480 milidetik**,
   terlepas dari seberapa ringan pekerjaan endpointnya. Tiga endpoint yang
   pekerjaannya sangat berbeda tercatat pada 482, 480, dan 481 milidetik.

5. **Penilaian berkas besar 2,45 sampai 9,29 kali lebih lama di Langflow**,
   meskipun logikanya sama persis. Selisihnya paling besar pada berkas kecil,
   karena ongkos pemanasan alur mendominasi di sana.

6. **Langflow memburuk seiring lama hidupnya.** Waktu tanggapnya naik 69%
   setelah lima menit beban ringan, dan pada rangkaian sebelumnya kondisi ini
   membuat lapisan API berhenti melaporkan hasil penilaian sama sekali.
   Apabila Langflow tetap dipakai melayani portal, ia perlu dinyalakan ulang
   secara berkala.

7. **Endpoint teringan pun terdampak.** Pembacaan aturan grade pada 5
   permintaan per detik sudah melampaui kapasitas Langflow, bahkan dengan
   container yang baru disegarkan.

### Rekomendasi

Langflow digunakan pada tahap penyusunan dan penelusuran alur, sedangkan
service Python digunakan untuk melayani portal. Logika penilaian tetap berada
pada satu folder bersama sebagaimana sekarang, sehingga perpindahan tidak
memerlukan penulisan ulang.

---

## 8. Menjalankan Ulang

```powershell
cd synchrono-service/beban

# Seluruh rangkaian, sebelas tahap, bergantian
./uji_menyeluruh.ps1

# Tanpa berkas 5 dan 10 juta baris
./uji_menyeluruh.ps1 -LewatiBesar

# Mengulang satu tahap saja
./uji_menyeluruh.ps1 -HanyaTahap 7-kapasitas-service
```

Catatan per tahap tersimpan di `beban/hasil/menyeluruh-<stempel>/`, beserta
`RINGKASAN.txt` yang memuat seluruh angka pada laporan ini.

> Berkas catatan ditulis PowerShell dalam UTF-16. Untuk membacanya dengan
> perkakas baris perintah, gunakan `Get-Content` atau tentukan encoding-nya
> secara eksplisit.
