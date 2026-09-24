# Menerima Enam Format Unggahan

Rancangan untuk menerima **CSV, Excel, TXT, dump SQL, Oracle Data Pump, dan
MDF SQL Server** sebagai berkas unggahan yang bisa digrading.

Dokumen ini ditulis sebelum kodenya, dan sengaja begitu: tiga dari enam format
itu menuntut keputusan arsitektur dan keamanan yang jauh lebih mahal untuk
diperbaiki setelah terlanjur dibangun.

Branch: **`query-executable-upload`**.

---

## 0. Yang TIDAK berubah

Ini perlu dinyatakan lebih dulu supaya tidak ada yang merancang ulang hal yang
sudah jalan.

**Cara berkas masuk tidak berubah sama sekali.** Portal mengunggah ke S3
persis seperti yang sudah dilakukannya untuk CSV hari ini:

```
uploads/{fileId}/raw/<nama berkas asli>
```

Tidak ada jalur unggah baru, tidak ada endpoint unggah baru, dan tidak ada
perubahan pada cara portal menyimpan berkas. Kita tetap **mengambilnya dari
S3**, sama seperti sekarang.

**Muatan dispatch juga tidak berubah.** Yang dikirim portal hari ini sudah
memuat semua yang dibutuhkan — termasuk `rawSourceKey` yang menunjuk berkas
unggahan asli lengkap dengan ekstensinya.

Jadi yang ditambahkan dokumen ini **hanya satu hal**: apa yang terjadi pada
berkas itu SESUDAH diambil dari S3. Berkas yang sifatnya data dibaca langsung;
berkas yang sifatnya bisa dieksekusi dibelokkan ke jalur terpisah lebih dulu.

### Penentuan jalurnya

Dari ekstensi `rawSourceKey`:

| Ekstensi | Jalur |
|---|---|
| `.csv` `.tsv` `.txt` `.xlsx` | **A** — engine membaca langsung dari S3 |
| `.sql` `.dmp` `.mdf` | **B** — konversi lebih dulu jadi parquet |

**Yang membelokkan adalah dispatch, bukan portal.** Ini sudah diputuskan.

Portal tetap memanggil **satu endpoint yang sama untuk keenam format** — yang
sudah dipakainya hari ini:

```http
POST {{base_url}}/api/v1/run/grading-dispatch?stream=false
```

Muatannya juga sama: `tweaks.GradingDispatch-a3967.payload` berisi
`OutboundGradingJobPayload` apa adanya. Portal tidak tahu-menahu soal pembagian
jalur; dispatch yang memeriksa ekstensi `rawSourceKey` lalu menentukan
pekerjanya.

Konsekuensinya dua, dan keduanya menguntungkan:

* **Menambah format berikutnya tidak menyentuh portal sama sekali.** Tabel di
  atas tinggal ditambah satu baris, di satu tempat, di sisi kami. Tidak ada
  endpoint baru yang harus didaftarkan, tidak ada flow baru yang harus dibangun
  ulang di Langflow.
* **Satu `jobId` dari awal sampai akhir.** Konversi bukan job terpisah melainkan
  tahap pertama dari job yang sama, jadi `grading-status` dan callback-nya tetap
  berlaku apa adanya. Kolom `stage` yang sudah ada tinggal diisi `K1 unduh`,
  `K2 restore`, `K3 ekspor` lewat `detak()` yang sama dengan G1–G5 — pengguna
  melihat satu proses, bukan dua.

Tempatnya di `components/grading/api1_dispatch.py:85`, tepat sebelum
`lepas(job)`: yang berubah hanya pekerja mana yang dilepas.

### Tiga hal yang sudah diperiksa di kode, jangan diasumsikan ulang

* **`rawSourceKey` memang sudah sampai ke dispatch.** `susun_job()`
  (`lib/_jobs.py:98`) sudah mengambilnya, berdampingan dengan `csvKey`. Jadi
  `job` yang ada di tangan pada baris 85 sudah memuat ekstensinya — tidak perlu
  field baru, tidak perlu perubahan muatan dari portal.
* **Ekstensinya diambil dari `raw_source_key` ATAU `csv_key`,** yang mana pun
  terisi — aturan yang sama dengan `_pilih_sumber()`. Kalau keduanya kosong
  (pengujian manual lewat kolom form yang hanya mengisi `parquet_key`),
  jalurnya **A**. Bawaan yang aman: yang tidak dikenali dibaca seperti sekarang,
  bukan ditolak.
* **`raw_source_key` TIDAK tersimpan di `grading_jobs`.** `KOLOM_DAFTAR`
  (`lib/_jobs.py:75`) tidak memuatnya — ia hidup hanya di dict yang diteruskan
  ke `lepas(job)`. Cukup untuk pembelokan di dispatch, tapi kalau nanti jalurnya
  perlu diketahui lagi sesudah restart, kolomnya harus ditambahkan lebih dulu.

---

## 1. Satu pembagian yang menentukan segalanya

Keenam format itu **tidak setara**, dan menyamakannya adalah kesalahan pertama
yang harus dihindari.

| | Jalur A | Jalur B |
|---|---|---|
| Format | CSV, TXT, XLSX | SQL, DMP, MDF |
| Sifatnya | **data** | **konten yang bisa dieksekusi** |
| Cara dibaca | DuckDB langsung dari S3 | dipulihkan ke mesin basis data asalnya |
| Di mana dikerjakan | **di dalam engine grading** | **di layanan terpisah** |
| Ongkos | nol tambahan | satu instance basis data per job |

CSV tidak bisa "melakukan" apa pun — paling buruk isinya nilai aneh. Dump SQL,
`.dmp`, dan `.mdf` bisa **memerintah**: menjalankan perintah shell, membaca
berkas server, memasang trigger yang jalan sendiri.

Karena itu keduanya dipisah, dan pemisahannya bukan soal kerapian melainkan
soal radius ledakan.

---

## 2. Jalur A — dibaca langsung oleh engine

### Yang sudah jalan

CSV, TSV, dan TXT sudah didukung: `read_csv_auto(..., all_varchar = true,
sample_size = -1)`. Deteksinya di `lib/_grading.py`, konstanta `POLA_TEKS`.

### `.xlsx` — SUDAH DIKERJAKAN

Diimplementasikan dan diuji terhadap `data_dukcapil_gradeA.csv` (200.000 baris,
6 kolom). Rinciannya di `GRADING.md` §1; yang perlu dicatat di sini adalah
**kenapa ia tidak sesederhana dugaan awal dokumen ini.**

Dugaan awalnya: "tambahkan `.xlsx` ke deteksi format, dan satu cabang
`read_xlsx`". Itu salah, dan pengukuranlah yang menunjukkannya.

#### `all_varchar = true` benar untuk CSV, tapi hanya SETENGAH benar untuk xlsx

Diukur pada berkas yang sama, dua kolom, dua mode baca:

| | `all_varchar = true` | bawaan |
|---|---|---|
| NIK ditulis sebagai angka bulat | `'9707000210903957'` — **0 selisih** | `9707000210903956.0` — **200rb salah** |
| Tanggal ditulis sebagai tanggal Excel | `'33148.0'` — **semua salah** | `1990-10-02` — **benar** |

Tidak ada satu mode pun yang benar untuk keduanya. Kalau `.xlsx` sekadar
ditempelkan ke jalur CSV — yang memang memakai `all_varchar` — **setiap tanggal
lahir akan berubah jadi serial Excel**, dan tidak ada yang error. Grading tetap
selesai, angkanya tetap keluar, hanya saja salah.

Yang dipakai sekarang: baca sebagai teks (NIK 16 digit tidak pernah melewati
DOUBLE), lalu satu `DESCRIBE` menentukan kolom mana yang tipe aslinya tanggal,
dan hanya kolom itu yang ekor `.0`-nya dibuang. Bentuk `'33148'` yang tersisa
persis yang **sudah** ditangani `deteksi_serial_excel` di `lib/_normalisasi.py`
sejak sebelum ini — jadi penanganan serial Excel tidak ditulis ulang, hanya
diberi masukan dalam bentuk yang sudah dikenalinya.

Hasil: **0 selisih pada NIK maupun tanggal**, terhadap CSV sumber yang sama.

### Presisi NIK — dua koreksi, dan yang kedua membatalkan yang pertama

Dokumen ini semula menuntut engine **menolak** berkas yang kolom NIK-nya
numerik. Itu dua kali salah, dan keduanya baru ketahuan karena diukur.

**Koreksi pertama: `all_varchar` ternyata BISA menyelamatkan.** Dua berkas yang
DESCRIBE sama-sama melaporkan `DOUBLE` ternyata bernasib berbeda — yang
tersimpan sebagai bilangan bulat terbaca utuh; yang tersimpan sebagai pecahan
tidak. Jadi tipe kolom bukan pembedanya.

**Koreksi kedua: pemeriksaan penggantiku pun salah.** Aku memakai "ada titik
atau huruf E" sebagai tanda rusak. Diukur pada 200.000 baris yang SELURUH
NIK-nya tersimpan sebagai pecahan:

| | |
|---|---|
| ditandai pemeriksaan `[.eE]` | **200.000** |
| benar-benar rusak | **15.755** (7,9%) |
| utuh setelah ekor `.0` dibuang | **184.245** (92,1%) |

Ukuran pertamaku membandingkan teks `'3201015107700001.0'` dengan
`'3201015107700001'` dan menyebut keduanya berbeda — padahal yang berbeda hanya
ekornya. Menolak berkasnya berarti membuang 92% data yang sehat.

#### Batasnya struktural, bukan taksiran

Bilangan pecahan presisi ganda mewakili setiap bilangan bulat dengan tepat
hanya **sampai 2⁵³ = 9.007.199.254.740.992**. NIK 16 digit melintasi batas itu:
yang berawalan provinsi 90+ (Papua) jatuh di atasnya, sisanya di bawah.

Di atas batas itu sebuah `double` hanya bisa menyimpan bilangan **genap**. Jadi
kira-kira separuh NIK di sana — yang ganjil — bergeser satu, dan separuhnya
selamat. Terukur: 31.626 NIK di atas batas, 15.755 benar-benar rusak, yaitu
**49,8%**. Persis yang diperkirakan teorinya.

Dan justru karena itu, **tidak ada cara mengetahui yang mana**. Keduanya sudah
terbaca genap di dalam berkasnya.

#### Yang dilakukan: tidak tepercaya, bukan ditolak

NIK di atas batas itu ditandai `nik_trusted = false` dan beranomali
`EXCEL_PRECISION_NIK` — perlakuan yang sama dengan notasi ilmiah Excel, yang
memang sudah ditangani `lib/_grading.py` sejak sebelum ini.

Ini bukan kompromi, melainkan justru yang paling aman, karena `nik_trusted`
**sudah** dipakai matching sebagai penjaga kunci join
(`ON CASE WHEN i.nik_trusted THEN i.nik END = m.nik`). Baris yang tidak
tepercaya otomatis tidak pernah dicocokkan lewat NIK — jadi bahaya "tertaut ke
orang yang berbeda" tertutup di tempat yang memang mengurusnya, tanpa perlu
membuang seluruh berkasnya.

Diuji ujung ke ujung terhadap CSV sumber yang sama:

| Berkas uji | Selisih isi | `nik_trusted` | NIK salah yang lolos dipercaya |
|---|---|---|---|
| NIK & tanggal sebagai teks | **0** | 194.754 | **0** |
| tanggal sebagai tanggal Excel | **0** | 194.754 | **0** |
| NIK sebagai pecahan | 15.755 | 168.374 | **0** |

Baris kedua adalah buktinya: tanggal Excel menghasilkan grade, skor, dan jumlah
tepercaya yang **sama persis** dengan versi teksnya. Keempat format sumber diuji
berdampingan dan jatuh pada angka yang sama:

```
parquet        grade B  skor 89  trusted 194.754  anomali 176.810
CSV            grade B  skor 89  trusted 194.754  anomali 176.810
xlsx (teks)    grade B  skor 89  trusted 194.754  anomali 176.810
xlsx (tanggal) grade B  skor 89  trusted 194.754  anomali 176.810
```
 Kolom paling kanan adalah
yang paling penting — tidak satu pun NIK rusak lolos sebagai tepercaya.

### Extension `excel` — kenapa tidak di `buka_koneksi()`

Ia dipasang saat build image (`infra/Dockerfile.langflow`) bersama `httpfs` dan
`postgres`, tapi **tidak ikut di-`LOAD`** di `buka_koneksi()`.

Alasannya bukan kerapian: `INSTALL` mengunduh dari internet, sementara container
ini dirancang berjalan tanpa akses keluar. Kalau `excel` ikut di sana dan
unduhannya gagal, yang mati bukan hanya job xlsx melainkan **setiap koneksi** —
termasuk seluruh matching dan config, yang tidak ada urusannya dengan Excel.
`_muat_excel()` memuatnya hanya saat berkasnya memang `.xlsx`, sehingga
kegagalannya terkurung pada job yang memang membutuhkannya.

### `.xls` — belum bisa dipastikan

Extension `excel` membaca format OOXML (`.xlsx`). `.xls` adalah format biner
lama yang berbeda sama sekali, dan belum ada berkas contoh untuk mengujinya.

**Jangan janjikan `.xls` sebelum diuji dengan berkas sungguhan.** Kalau tidak
didukung, ia masuk jalur B sebagai kasus konversi — bukan jalur A.

---

## 3. Jalur B — layanan konversi terpisah

### Kenapa terpisah, dan kenapa ini tidak bisa ditawar

Engine grading hari ini berukuran 270 MB dan bergantung pada **satu** pustaka:
`duckdb`. Itu keputusan desain yang paling berharga di sistem ini — ia yang
membuat seluruh mesin bisa dipindahkan, diuji, dan dijalankan di mana saja.

Memasang klien Oracle dan SQL Server ke dalam container grading akan
menggelembungkannya jadi beberapa GB, menambah urusan lisensi, dan membatalkan
keputusan itu — demi tiga format yang mungkin jarang dipakai.

Layanan konversi yang terpisah membalik ongkosnya: yang gemuk hanya pekerja
konversi, dan ia hanya hidup saat benar-benar dibutuhkan.

### Alurnya

Sumber dan tujuannya tetap **S3, sama persis dengan CSV**. Yang terpisah hanya
pemrosesannya:

```
s3://‹bucket›/uploads/{fileId}/raw/x.dmp        ← portal mengunggah, seperti biasa
        │
        │  diambil orkestrator konverter (kredensial S3 yang sama)
        ▼
  [sandbox: DB sekali pakai, TANPA jaringan]   ← di sinilah bahayanya
        │
        │  ekspor hanya kolom kontrak
        ▼
s3://‹bucket›/uploads/{fileId}/source.parquet   ← batas kepercayaan berakhir di sini
        │
        ▼
  dispatch grading                             ← tidak bisa dibedakan dari job biasa
```

Sandbox-nya sendiri **tidak punya akses jaringan sama sekali** — ia bahkan tidak
tahu S3 itu ada. Berkas masuk dan parquet keluar lewat direktori yang di-mount,
dan orkestrator di luarlah yang menjembatani direktori itu dengan S3.

Itu detail internal, bukan kontraknya. Kontrak ke luar tetap satu kalimat:
**ambil dari S3, tulis ke S3** — sama seperti hari ini.

### Kenapa grading tidak perlu diubah sama sekali

Konverter menulis hasilnya ke `uploads/{fileId}/source.parquet`, lalu memanggil
**dispatch grading yang sudah ada** dengan `parquetKey` menunjuk ke situ.

`_pilih_sumber()` di `lib/_grading.py:138` sudah memakai `parquetKey` bila ia
benar-benar berkas lain dari tujuan enriched-nya. Tujuannya
`uploads/{fileId}/enriched.parquet`, sumbernya `uploads/{fileId}/source.parquet`
— berbeda, jadi terpilih dengan sendirinya.

Artinya dari sudut pandang pipeline grading, job hasil konversi **tidak bisa
dibedakan** dari job parquet biasa. G1–G5 tidak berubah satu baris pun, tidak
ada format baru yang harus dikenalinya, dan `POLA_TEKS` (`lib/_grading.py:283`)
tidak perlu disentuh untuk jalur B.

Satu-satunya tempat engine tahu jalur B ada adalah percabangan tipis di dispatch
(§0). Sesudah titik itu, jalur B tidak kelihatan lagi dari mana pun.

Ini yang membuat pemisahannya murah: jalur B menambah satu layanan **di samping**
sistem yang ada, bukan satu jalur pemrosesan kedua **di dalamnya**.

### Apa yang dibutuhkan tiap format

| Format | Mesin yang dibutuhkan | Catatan |
|---|---|---|
| `.sql` | sesuai dialeknya | PostgreSQL **sudah ada**; dialek lain butuh mesinnya sendiri |
| `.mdf` | SQL Server | image 0,58 GB unduhan; **`.ldf` juga dibutuhkan**; versi harus cocok |
| `.dmp` | Oracle Database | beberapa GB; batas data edisi Free & lisensinya perlu dipastikan sendiri |

Tiga hal yang sering baru ketahuan di lapangan:

* **Dialek `.sql` harus diketahui lebih dulu.** Dump MySQL, PostgreSQL, Oracle,
  dan SQL Server tidak saling kompatibel.
* **`.mdf` sendirian sering gagal di-attach** tanpa `.ldf`-nya. Kalau portal
  hanya menerima `.mdf`, sebagian berkas akan gagal dengan sebab yang sulit
  dijelaskan ke pengguna.
* **`.dmp` bisa dua format berbeda** dengan ekstensi yang sama: Data Pump
  (`impdp`) atau `exp` lama (`imp`).

### Keluarannya: kontrak yang tetap

Konverter menghasilkan **parquet dengan kolom yang sudah ditentukan**, bukan
salinan apa adanya dari sumbernya:

```
nik, nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu
```

Semua bertipe teks. Apa pun selain kolom itu — trigger, prosedur, tabel lain,
kolom tambahan — **tidak ikut keluar**.

Sesudah itu grading berjalan apa adanya, tanpa satu baris pun berubah, karena
ia sudah membaca parquet sejak awal.

---

## 4. Model keamanan

Prinsipnya satu: **jangan mendeteksi serangan, buat serangannya mustahil.**

Peran PostgreSQL non-superuser *tidak bisa* menjalankan `COPY ... FROM PROGRAM`
— bukan "kemungkinan besar tertangkap", memang tidak bisa, apa pun isi
berkasnya. Pertahanan seperti itu bekerja terhadap serangan yang belum pernah
ada sekalipun.

### Enam lapisan, berurutan menurut manfaatnya

**1. Peran basis data tanpa hak istimewa.** Pulihkan sebagai peran yang bukan
superuser, tanpa `pg_execute_server_program`, `pg_read_server_files`, maupun
`pg_write_server_files`. Ini memblokir vektor eksekusi perintah yang utama
secara mekanis. Pulihkan dalam satu transaksi yang berhenti di galat pertama,
supaya tidak ada keadaan setengah jadi.

Sebagian dump memang akan gagal karena butuh hak lebih tinggi. **Itu perilaku
yang benar** — tabel data kependudukan tidak membutuhkan extension apa pun.

**2. Sandbox tanpa jaringan keluar, dibuang setelah selesai.** Non-root,
capability dibuang, tidak boleh menaikkan hak istimewa, root filesystem
read-only dengan tmpfs untuk kerja sementara. Kalaupun ada kode yang berhasil
jalan, ia tidak bisa mengirim apa pun keluar.

**3. Instance kosong per job.** Bukan yang dipakai bersama. Instance bersama
membuat dump satu pengguna bisa membaca — atau mengubah — data pengguna lain,
dan sisa job sebelumnya bisa mencemari hasil berikutnya.

**4. Ekspor hanya kolom dan tabel yang ditunjuk.** Ini batas yang paling
menentukan. Keluarannya ditentukan oleh kita, bukan oleh berkasnya. Yang
menyeberang keluar sandbox hanyalah parquet — data mati yang tidak bisa
memerintah apa pun.

Pakai mekanisme ekspor **sisi klien**, bukan `COPY TO` sisi server — yang kedua
menuntut hak istimewa yang sengaja tidak diberikan di lapisan 1.

**5. Batas waktu, memori, disk, dan jumlah proses.** Batas waktunya harus
**membunuh container**, bukan sekadar timeout di dalam aplikasi.

**6. Penyaringan pola, dan AI hanya sebagai catatan.** Penyaringan pola murah
dan menangkap yang ceroboh, tapi ia daftar larangan — selalu bisa diakali
penyamaran. Lapisan tambahan, bukan pengganti lima di atas.

### Kenapa TIDAK ada gerbang AI

Sempat dipertimbangkan dan **ditolak dengan sengaja**. Tiga alasannya:

* **Ia menciptakan permukaan serangan baru.** Berkas `.sql` adalah teks yang
  dikendalikan penyerang; menyuapkannya ke LLM untuk dinilai membuka jalur
  injeksi prompt.
* **Ia daftar larangan yang menyamar** — kelemahan yang sama, ditambah
  ketidakpastian dan biaya.
* **Rasa aman palsu.** Kalau tim percaya "AI-nya sudah memeriksa", isolasi yang
  benar-benar bekerja cenderung dilonggarkan.

Ini juga konsisten dengan seluruh sistem: `lib/_llm.py` dipakai hanya sebagai
jaring terakhir untuk mengenali nama kolom, dan prompt reasoning **melarang**
LLM menilai sendiri apakah dua nilai berbeda. Polanya tetap: **kode yang
memutuskan, AI yang menjelaskan.**

AI boleh dipakai di tiga tempat, dan ketiganya punya sifat yang sama — kalau
salah, tidak ada yang bocor: meringkas isi dump supaya operator tahu tabel mana
yang berisi data penduduk, menerjemahkan alasan penolakan jadi kalimat yang
dimengerti, dan menandai konstruksi tidak lazim ke catatan audit.

---

## 5. Antrean dan sumber daya

Konversi **harus berantre**, dan batasnya jauh lebih kecil daripada grading.

Grading memakai `GRADING_MAX_CONCURRENT` (bawaan 2). Konversi butuh pola yang
sama, tapi tiap job menghidupkan **satu instance basis data utuh** — bukan
sekadar kueri DuckDB. Kalau satu container SQL Server butuh ~2 GB RAM, N=2
berarti 4 GB tersedot hanya untuk konversi.

Waktu nyalanya juga berbeda jauh: PostgreSQL beberapa detik, SQL Server puluhan
detik, Oracle bisa menit. Itulah yang membatasi N — bukan CPU-nya.

Dua hal yang wajib ikut dirancang, dan keduanya dipelajari dengan mahal di
server produksi:

* **Batas waktu keras.** Pernah terjadi dua job macet memegang slotnya selama
  timeout dan seluruh antrean berhenti — tercatat `mengantre 130s sebelum
  mulai`. Dengan N kecil, satu job macet cukup untuk menyandera semuanya.
* **Antreannya harus terlihat pengguna.** "Berkas Anda nomor 3 dalam antrean"
  jauh lebih baik daripada layar diam, yang akan dilaporkan sebagai kerusakan.

---

## 6. Yang masih harus diputuskan

Empat hal, dan semuanya menyentuh kontrak dengan portal — jadi bukan keputusan
sepihak dari sisi engine. (Siapa yang membelokkan ke jalur B **sudah** diputuskan:
dispatch — lihat §0.)

**Tabel mana yang berisi data kependudukan?** Dump bisa memuat puluhan tabel.
Sistem harus tahu yang mana — dari pilihan operator di portal, atau konvensi
nama yang disepakati. Tanpa itu konversinya menebak, dan menebak salah berarti
menggrading tabel yang keliru tanpa ada yang sadar.

**Dialek `.sql` dikirim portal atau dideteksi konverter?** Mendeteksi bisa,
tapi menerima keterangannya dari portal jauh lebih murah dan lebih jujur.

**`.mdf` tanpa `.ldf` ditolak atau dicoba?** Kalau dicoba, sebagian akan gagal
dengan pesan dari SQL Server yang tidak berarti apa-apa bagi operator.

**Berapa batas ukuran berkas?** Menentukan disk sandbox, batas waktu, dan
apakah edisi gratis Oracle/SQL Server cukup.

---

## 7. Urutan pengerjaan yang disarankan

1. ~~**`.xlsx` di jalur A.**~~ **SELESAI.** Termasuk penolakan NIK rusak dan
   penanganan tanggal Excel. Lihat §2. Satu pelajaran dibawa ke langkah
   berikutnya: dugaan "perubahannya kecil" ternyata menyembunyikan cacat diam
   pada tanggal, dan yang menemukannya adalah pengukuran, bukan pembacaan kode.
2. **Uji `.xls`** dengan berkas sungguhan. Hasilnya menentukan ia masuk jalur A
   atau B.
3. **Kerangka jalur B dengan `.sql` dialek PostgreSQL.** Mesinnya sudah ada,
   jadi seluruh usaha bisa dipusatkan ke sandbox, antrean, dan kontrak
   keluarannya — bukan ke memasang basis data baru. Percabangan di dispatch
   dikerjakan di sini juga, sekali, karena ia sama untuk ketiga formatnya.
4. **`.mdf`,** memakai kerangka yang sama.
5. **`.dmp`** terakhir, dan hanya kalau memang ada instansi yang tidak bisa
   memberi format lain.

Langkah 3 yang paling penting dikerjakan benar: kerangkanya dipakai ulang oleh
dua format sisanya, jadi kesalahan di situ akan terulang tiga kali.

---

## 8. Satu pertanyaan yang layak diajukan lebih dulu

Apakah `.dmp` dan `.mdf` benar-benar dibutuhkan, atau baru daftar keinginan di
UI?

Kalau kebutuhannya "instansi memberi kami ekspor Oracle", jalan yang jauh lebih
murah adalah **meminta mereka mengekspor CSV** — satu perintah di sisi mereka,
dan seluruh persoalan di dokumen ini tidak pernah ada.

Membangun jalur `.dmp` dan `.mdf` adalah sub-proyek tersendiri: dua mesin basis
data, urusan lisensi, isolasi keamanan, dan penanganan kasus seperti `.ldf`
yang hilang. Sepadan kalau memang ada yang tidak bisa memberi format lain.
Tidak sepadan kalau hanya supaya daftarnya terlihat lengkap.

---

## 9. Berkas yang perlu dibaca lebih dulu

| Berkas | Kenapa |
|---|---|
| `lib/_grading.py` — `POLA_TEKS`, `_sql_sumber` | tempat deteksi format tinggal |
| `lib/_grading.py` — `_pilih_sumber` | cara berkas masukan dipilih dari muatan portal |
| `lib/_worker.py` | pola pekerja latar, semaphore, detak, status job |
| `components/grading/api1_dispatch.py` | pola dispatch asinkron |
| `README.md` §4 | empat jebakan Langflow yang tidak memunculkan error |
