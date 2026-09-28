# Rencana Optimasi Grading

**Disusun:** 19 September 2026
**Pemicu:** unggahan 1 juta baris di server memakan **±3 menit** untuk digrading.
Penyebabnya sudah ditemukan dan direproduksi — lihat bagian 1.

**Status: usulan 4A dan 3A sudah dikerjakan dan diukur di mesin pengembangan.**
Hasilnya di bagian 1A. Usulan sisanya belum dikerjakan.

Syarat yang mengikat seluruh usulan di sini: **akurasi tidak boleh berubah**.
Bagian 6 menjelaskan cara membuktikannya.

---

## 1. Penyebabnya sudah ditemukan: sumbernya CSV

**Terukur 19 September 2026, bukan lagi dugaan.**

Berkas 1 juta baris yang sama digrading dua kali, satu-satunya perbedaan
adalah bentuk sumbernya:

| Sumber | Total | G2 | G3 | G5 |
|---|---:|---:|---:|---:|
| **parquet** | **11,6 dtk** | 4,25 | 3,51 | 3,22 |
| **CSV** | **193,5 dtk** | **124,07** | **59,38** | 8,59 |
| Selisih | **16,6×** | 29× | 17× | 2,7× |

**193,5 detik ≈ 3 menit** — cocok dengan yang dilaporkan terjadi di server.

Dengan demikian selisih 15 kali itu **bukan** soal perangkat keras server,
bukan jarak ke PostgreSQL, dan bukan kekurangan memori. Penyebabnya satu:
portal mengirimkan CSV, dan pipeline mengurai CSV itu berulang kali.

Satu pemindaian penuh berkas CSV 341 MB dari penyimpanan objek terukur
**11,3 detik**. Pipeline melakukannya sekitar delapan kali — perinciannya di
bagian 3.

## 1A. Hasil setelah 4A dan 3A dikerjakan

**Terukur 19 September 2026, berkas 1 juta baris dari sumber CSV.**

| Keadaan | Total | Dibanding awal |
|---|---:|---:|
| Sebelum perubahan | **193,5 dtk** | — |
| Setelah 4A saja, sampel diambil sekali | **160,8 dtk** | 1,2× |
| Setelah 4A + 3A, sumber CSV dimaterialkan | **35,9 dtk** | **5,4×** |

Rincian per tahap sesudah keduanya:

| Tahap | Sebelum | Sesudah |
|---|---:|---:|
| G1 | 0,53 | 0,55 |
| **G2** | **124,07** | **18,10** |
| **G3** | **59,38** | **10,22** |
| G4 | 0,79 | 0,37 |
| G5 | 8,59 | 6,53 |
| G6 | 0,09 | 0,15 |
| **Total** | **193,5** | **35,9** |

**Akurasi tidak berubah.** 29 berkas uji memetakan kolom sama persis, dan
grade tetap: verif-A sampai verif-E menghasilkan A, B, C, D, E; samar-c C,
samar-e F, uji-f E, csv-uji-A B. Berkas 1 juta baris tetap grade B, skor 88,
anomali 140.000 — sama dengan sebelum perubahan.

### Pembagian jasa antara keduanya

Kontribusi 4A diisolasi dengan mematikan 3A sementara. Hasilnya 160,8 detik,
sehingga:

- **4A menghapus ±33 detik** — setara dua kali pengurai CSV, persis seperti
  yang diperkirakan
- **3A menghapus ±125 detik lagi** — sisa enam pemindaian

Jadi hasil terbesar datang dari 3A, dan 4A bekerja seperti yang diperkirakan.

### Risiko memori usulan 3A: diuji, tidak terwujud

Materialisasi berkas CSV besar sebagai tabel DuckDB ditandai sebagai risiko
yang wajib diukur. Sudah diuji pada berkas **10 juta baris, CSV 3,4 GB**:

| | Hasil |
|---|---|
| Status | **selesai, tidak OOM** |
| Total | 427,2 dtk |
| RSS puncak | 3.492 MB dari 5,79 GB |
| Grade | B, 10.000.000 baris |

`DUCKDB_MEMORY_LIMIT=3GB` bekerja sebagaimana mestinya: kelebihannya
ditumpahkan ke disk, bukan memaksa proses dibunuh. Jadi usulan 3A **tidak
memerlukan penjaga ukuran berkas**.

Meskipun demikian, pada 10 juta baris sumber CSV masih **±3 kali lebih lambat**
daripada parquet (427 detik lawan ±139 detik). Materialisasi memperbaiki
keadaan, tetapi tidak menghapus seluruh ongkos CSV — G2 tetap 205 detik karena
mengurai 3,4 GB teks sekali pun tetap mahal.

**Itu memperkuat usulan 3B:** untuk berkas berukuran jutaan baris, konversi ke
parquet di sisi portal tetap jalan yang paling cepat, dan 3A adalah jaring
pengaman ketika yang datang tetap CSV.

### Yang TIDAK terbukti

4A diperkirakan menghemat ±0,9 detik pada sumber **parquet**. Diukur tiga
kali, G2 pada parquet tercatat 4,39 / 4,66 / 4,53 detik, dibandingkan 4,25
detik sebelumnya — **selisihnya tenggelam di dalam derau**. Penghematan itu
nyata hanya pada sumber CSV, tempat satu pemanggilan sampel berarti satu kali
mengurai seluruh berkas.

> **Catatan pengukuran.** Total pada sumber parquet tercatat ±17,4 detik
> (17,73 / 17,65 / 16,78), lebih tinggi daripada 11,64 detik yang tercatat
> sebelumnya. Yang bergeser G3 dan G5 — dua tahap yang tidak disentuh
> perubahan ini sama sekali. Penyebabnya keadaan mesin, bukan perubahan kode:
> Docker baru dinyalakan ulang dan container lain ikut hidup. Ini pengingat
> yang sama seperti pada laporan sebelumnya, bahwa **yang layak dikutip
> rasionya, bukan angka mutlaknya**.

---

## 2. Yang sudah terukur, sebagai titik berangkat

Rincian per tahap, 1 juta baris × 35 kolom, sumber **parquet**, mesin
pengembangan 4 inti, sesudah optimasi Arrow:

| Tahap | Waktu | Porsi |
|---|---:|---:|
| G1 buka sesi | 0,32 dtk | 3% |
| G2 kenali kolom | 4,25 dtk | **37%** |
| G3 bersihkan & tandai | 3,51 dtk | 30% |
| G4 skor & grade | 0,23 dtk | 2% |
| G5 tulis hasil | 3,22 dtk | 28% |
| G6 susun muatan | 0,11 dtk | 1% |
| **Total** | **11,64 dtk** | |

Sebagai pembanding, sebelum optimasi Arrow G2 memakan 26,21 detik dari total
34,17 detik. Perbaikan itu menghapus 22 detik dari satu tahap saja, dan
ditemukan dengan membedah tahap tersebut — bukan dengan mengganti mesin
pengolah data.

---

## 3. Sebabnya: sumber CSV dipindai berulang kali

**Terkonfirmasi lewat pengukuran, dan terbaca dari kode.**

Pipeline membuka sumbernya sebagai **VIEW**, bukan tabel:

```
CREATE OR REPLACE VIEW raw_df AS SELECT * FROM read_csv_auto(
    '<berkas>', all_varchar = true, sample_size = -1)
```

Setiap kali `raw_df` dipindai, berkasnya **dibaca dan diurai ulang dari awal**.
Satu jalan grading memindainya sekitar delapan kali:

| # | Pemindaian | Letak |
|---|---|---|
| 1 | `SELECT count(*) FROM raw_df` | `muat_raw` |
| 2 | `ambil_sampel` untuk lapis 3 | `petakan_kolom` |
| 3 | `ambil_sampel` untuk lapis 4 | `petakan_kolom` |
| 4 | `ambil_sampel` untuk lapis 5 | `petakan_kolom` |
| 5 | `deteksi_konvensi` — tanggal DD-MM atau MM-DD | `bangun_view` |
| 6 | `ada_huruf` | `bangun_view` |
| 7 | `deteksi_serial_excel` | `bangun_view` |
| 8 | Materialisasi `vonis_df` | G3 |

Delapan kali 11,3 detik mendekati 90 detik, dan itu sejalan dengan G2 yang
terukur 124 detik ditambah sebagian G3.

Pada sumber **parquet** hal yang sama murah: formatnya kolumnar, DuckDB hanya
membaca kolom yang dipakai, dan strukturnya sudah tercatat di berkas. Pada
**CSV** tiap pemindaian mengurai seluruh 341 MB dan 35 kolom sebagai teks.
Ditambah lagi `sample_size = -1` menyuruh DuckDB membaca seluruh berkas hanya
untuk menebak strukturnya, pada tiap pemanggilan.

### Dua obat, keduanya sudah diukur ongkosnya

| Usulan | Cara | Ongkos sekali | Perkiraan total | Dibanding sekarang |
|---|---|---:|---:|---:|
| **3A** | `raw_df` jadi TEMP TABLE bila sumbernya CSV | 12,5 dtk | ±24 dtk | **±8× lebih cepat** |
| **3B** | Portal mengirim parquet | 15,6 dtk konversi | 11,6 dtk grading | **±16× lebih cepat** |

Setelah dimaterialkan, pemindaian berikutnya terukur **0,00 detik** — itulah
yang menghapus tujuh pemindaian sisanya.

**Usulan 3A — materialkan sumber satu kali.**

```
CREATE OR REPLACE TEMP TABLE raw_df AS SELECT * FROM read_csv_auto(...)
```

Untuk sumber parquet, VIEW dipertahankan: di sana pemindaian ulang memang
murah, dan materialisasi justru menambah pemakaian memori tanpa manfaat.

- **Perkiraan ±24 detik** adalah 12,5 detik materialisasi ditambah waktu
  pipeline yang terukur pada parquet. Bagian keduanya **turunan, bukan
  terukur** — pipeline di atas TEMP TABLE belum dijalankan karena itu
  memerlukan perubahan kode
- **Risiko:** pemakaian memori naik sebesar isi berkas dalam bentuk tabel
  DuckDB. Untuk 10 juta baris ini wajib diukur, bukan diasumsikan aman
- **Akurasi:** tidak berubah, isi tabel sama persis dengan isi view

**Usulan 3B — portal mengirim parquet.** Jalur yang memang dianjurkan sejak
awal dan sudah tertulis di komentar `_sql_sumber`. Konversinya 15,6 detik
sekali di sisi portal, dan setelah itu grading kembali ke 11,6 detik.

- **Hasil terbesar**, dan menghilangkan seluruh persoalan ini, bukan
  menguranginya
- **Risiko:** menyentuh portal, bukan engine
- Berkasnya juga jauh lebih kecil: parquet 69 MB lawan CSV 341 MB

**Keduanya tidak saling meniadakan.** 3A membuat engine tahan terhadap
unggahan CSV siapa pun; 3B menghapus ongkosnya sejak awal. Mengerjakan 3A
lebih dulu masuk akal karena tidak bergantung pada perubahan di portal.

## 4. Empat pekerjaan berulang yang bisa dihapus

Ketiganya **terbaca dari kode dan sebagian sudah terukur**, dan berlaku baik
pada sumber CSV maupun parquet.

### 4A — Sampel diambil tiga kali padahal hasilnya identik

`ambil_sampel` dipanggil ulang untuk lapis 3, 4, dan 5. Sampelnya memakai
`REPEATABLE (42)`, jadi **ketiganya menghasilkan 300 baris yang sama persis**.
Yang berbeda hanya cara memakainya.

Terukur pada mesin pengembangan: 0,57 + 0,37 + 0,40 = **1,34 detik** dari 4,25
detik G2, seluruhnya terbuang.

- **Usulan:** ambil sekali, oper ke ketiga lapis
- **Perkiraan hasil:** sekitar 0,9 detik pada parquet 1 juta baris; jauh lebih
  besar pada CSV, karena tiap pemanggilan berarti satu pengurai penuh
- **Risiko:** rendah. Sampelnya memang sudah deterministik
- **Akurasi:** tidak berubah, nilainya identik

### 4B — Kamus master ditarik ulang tiap jalan

`_siapkan_kamus` membangun tabel sementara berisi **324.151 nilai unik** dari
tabel master PostgreSQL, dan itu terjadi pada **setiap** berkas yang digrading.

Terukur pada mesin pengembangan: **2,12 detik**. Di server, PostgreSQL berada
di host yang sama tetapi melalui jaringan container — angkanya perlu diukur
sendiri dan bisa jauh lebih besar.

- **Usulan:** simpan kamus di luar sesi grading, misalnya sebagai tabel
  material di PostgreSQL yang disegarkan terjadwal, atau cache di proses
- **Perkiraan hasil:** menghapus 2 detik atau lebih dari tiap jalan
- **Risiko:** kamus menjadi basi bila master berubah. Perlu aturan penyegaran
  yang jelas
- **Akurasi:** tidak berubah selama kamusnya masih mewakili master

### 4C — Rujukan wilayah dibaca ulang tiap jalan

`_wilayah.muat` membaca parquet rujukan 7.265 kecamatan dari S3 dan membuat
TEMP TABLE, setiap sesi grading. Ukurannya kecil, tetapi melibatkan satu
perjalanan ke penyimpanan objek.

- **Usulan:** sama seperti 4B
- **Perkiraan hasil:** kecil pada penyimpanan lokal, perlu diukur pada server
- **Risiko:** rendah

### 4D — Kolom yang tidak dipakai ikut dibaca

`ambil_sampel` memakai `SELECT *`, sehingga seluruh 35 kolom ditarik meskipun
yang diperiksa hanya kolom yang belum dikenali. Setelah lapis 1 mengenali 12
kolom lewat alias, 23 kolom sisanya saja yang sebenarnya dibutuhkan.

- **Perkiraan hasil:** kecil pada parquet, nyata pada CSV
- **Risiko:** rendah

---

## 5. Setelan yang perlu diperiksa di server

Belum tentu salah, tetapi belum pernah diperiksa. Tidak ada yang perlu diubah
sebelum diukur.

| Setelan | Yang perlu dilihat |
|---|---|
| `DUCKDB_MEMORY_LIMIT` | Disetel 3 GB. Bila memori server lebih besar, menaikkannya mengurangi tumpahan ke disk |
| Jumlah inti CPU | G3 dan G5 memakai seluruh inti. Server dengan inti lebih sedikit akan lebih lambat secara sebanding |
| `temp_directory` | Bila tumpahan jatuh ke disk yang lambat, waktunya melonjak |
| Kompresi parquet keluaran | Saat ini bawaan DuckDB. Kompresi yang lebih ringan mempercepat G5 dengan harga berkas lebih besar |
| Jumlah pekerja Langflow | Dibahas di LAPORAN-UJI-MENYELURUH.md bagian 1f. Menaikkannya memperbaiki waktu tanggap polling, bukan waktu penilaian |

---

## 6. Menjaga akurasi

Setiap usulan di atas wajib melewati pemeriksaan yang sama sebelum diterima:

```powershell
cd synchrono-service/beban

# 36 berkas uji: pemetaan kolom dan skor pesaing tiap keputusan dibandingkan
docker exec synchrono-service python /synchrono/beban/regresi_kamus.py

# grade A-E harus tetap A-E
docker exec synchrono-service python /synchrono/beban/uji_skala.py verif-A --tanpa-matching
```

Perkakas ini sudah dipakai untuk menerima optimasi Arrow, dan sudah terbukti
menangkap pergeseran sekecil apa pun: yang dibandingkan bukan hanya kolom mana
yang terpilih, tetapi juga skor pesaing tiap keputusan.

**Optimasi yang mempercepat tetapi mengubah satu saja dari 36 hasil itu,
ditolak.**

---

## 7. Urutan kerja yang disarankan

Penyebab utamanya sudah diketahui, sehingga pengukuran di server **bukan lagi
prasyarat**. Yang masih perlu dipastikan hanya satu hal, dan jawabannya
menentukan apakah 3B bisa dipakai.

### Tahap 1 — Satu hal yang masih perlu dipastikan di server

```bash
cd ~/development/synchrono-langflow/langflow-synchrono/infra

docker compose -f docker-compose.server.yml logs langflow   | grep -E "dibaca sebagai (CSV|parquet)"
```

- Bila **CSV**: gejalanya sama persis dengan yang direproduksi di sini, dan
  seluruh usulan bagian 3 berlaku
- Bila **parquet**: dugaan bagian 3 tidak berlaku untuk server, dan
  penyebabnya harus dicari ulang lewat rincian `[G1]`–`[G6]` pada log

Perintah pelengkap bila jawabannya parquet:

```bash
docker compose -f docker-compose.server.yml logs --tail 200 langflow   | grep -E "\[G[1-6]\]|gradingDurationMs"
nproc && free -g
```

### Tahap 2 — Urutan pengerjaan

| Urutan | Pekerjaan | Perkiraan hasil | Risiko |
|---|---|---|---|
| 1 | **4A** — sampel diambil sekali | besar pada CSV, ±0,9 dtk pada parquet | rendah |
| 2 | **3A** — materialkan sumber CSV | 193,5 → ±24 dtk | memori naik, wajib diuji di 10 juta |
| 3 | **3B** — portal mengirim parquet | ±24 → 11,6 dtk | menyentuh portal |
| 4 | **4B** — kamus master di-cache | ±2 dtk tiap jalan | kamus bisa basi |
| 5 | **4C, 4D** | kecil | rendah |
| 6 | Setelan bagian 5 | belum diketahui | perlu diukur dulu |

4A didahulukan karena risikonya paling rendah, hasilnya pasti, dan ia
mengurangi jumlah pemindaian dari delapan menjadi enam bahkan sebelum 3A
dikerjakan.

### Tahap 3 — Ukur ulang dengan cara yang sama

Grading berkas 1 juta baris yang sama dari sumber CSV, lalu bandingkan rincian
per tahapnya dengan tabel di bagian 1. Perbaikan yang tidak terlihat pada
rincian per tahap bukan perbaikan.

---

## 8. Yang tidak disarankan, beserta alasannya

| Usulan | Alasan ditolak |
|---|---|
| Mengganti DuckDB dengan Polars | Sudah diuji. Lebih lambat pada penulisan, memori 2,5 kali, dan gagal pada 5 juta baris. Rinciannya di PERBANDINGAN.md bagian 1d |
| Memakai pola DuckDB + Polars seperti proyek sebelah | Sudah diuji. 10 sampai 18 kali lebih lambat dari perhitungan di dalam basis data |
| Menambah pekerja Langflow untuk mempercepat penilaian | Sudah diuji. Memperbaiki waktu tanggap polling, bukan waktu penilaian, dan membuat kanvas Langflow tidak dapat dipakai |
| Mengurangi cakupan pemeriksaan anomali | Mempercepat dengan mengurangi ketelitian. Bertentangan dengan syarat yang ditetapkan |

---

## 9. Perkiraan hasil

| Keadaan | Waktu 1 juta baris |
|---|---:|
| Sekarang, sumber CSV | **193,5 dtk** |
| Setelah 4A saja | belum diukur, berkurang dua pemindaian dari delapan |
| Setelah 3A | **±24 dtk** (turunan: 12,5 materialisasi + 11,6 pipeline) |
| Setelah 3B | **11,6 dtk** (terukur) |
| Batas bawah tahap yang tersisa | G2 4,25 + G3 3,51 + G5 3,22 |

Angka 11,6 detik adalah yang **sudah terukur hari ini** pada sumber parquet.
Jadi 3B bukan perkiraan optimistis, melainkan keadaan yang sudah dicapai
begitu bentuk sumbernya benar.

**Yang belum diukur dan tidak boleh dijanjikan:**

- Pipeline di atas TEMP TABLE. Angka ±24 detik adalah penjumlahan dua
  pengukuran terpisah, bukan satu jalan utuh
- Pemakaian memori usulan 3A pada 5 dan 10 juta baris. Materialisasi berkas
  3,4 GB sebagai tabel DuckDB berpotensi menembus batas memori container
- Perilaku di server. Seluruh angka di dokumen ini berasal dari mesin
  pengembangan 4 inti

Setelah usulan bagian 3 dikerjakan, tahap yang tersisa kembali seimbang —
G2, G3, dan G5 masing-masing sekitar sepertiga. Perbaikan berikutnya setelah
itu akan jauh lebih kecil hasilnya, dan sebaiknya diputuskan dari pengukuran
baru, bukan dari daftar ini.
