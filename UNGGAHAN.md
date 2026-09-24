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

### `.xls` — SUDAH DIUJI: DITOLAK, dan bukan jalur B

Dokumen ini semula menduga `.xls` "masuk jalur B sebagai kasus konversi kalau
tidak didukung". Diuji dengan berkas BIFF8 sungguhan, jawabannya **bukan jalur
B, melainkan ditolak** — dan tiga temuan yang menentukannya:

* **Bukan ZIP.** Tanda tangannya `d0cf11e0a1b11ae1`, wadah OLE2. `read_xlsx`
  gagal dengan `Failed to open zip for reading`.
* **`spatial` pun tidak menolong.** `st_drivers()` di build DuckDB ini hanya
  memuat driver `XLSX`. Tidak ada `XLS`, jadi tidak ada pintu belakang lewat
  GDAL.
* **Formatnya terlalu kecil untuk pekerjaan ini.** `.xls` hanya memuat
  **65.535 baris data** per lembar — terverifikasi, penulisnya menolak di baris
  65.536. Berkas kependudukan yang dipakai di sini 200.000 baris. Bahkan kalau
  pembacanya ada, `.xls` tidak akan pernah memuat datanya utuh.

Yang ketiga yang menutup perkara. Membangun jalur konversi untuk format yang
secara struktural **tidak sanggup menampung datanya** berarti membangun sesuatu
yang selalu memotong data diam-diam.

Karena itu `.xls` ditolak di `_sql_sumber` dengan pesan yang menyebut jalan
keluarnya — simpan ulang sebagai `.xlsx`, atau ekspor ke CSV — bukan dibiarkan
gagal sebagai galat zip yang tidak berarti apa-apa bagi operator.

Diuji ujung ke ujung lewat S3: job gagal dengan pesan itu, bukan dengan galat
zip.

### Catatan: "tidak terbaca" tidak otomatis berarti jalur B

Pembagian di §1 memisahkan **data** dari **konten yang bisa dieksekusi**, dan
jalur B ada karena yang kedua berbahaya — bukan karena sulit dibaca.

`.xls` sulit dibaca tapi sama sekali tidak berbahaya. Menaruhnya di jalur B
berarti menyalakan sandbox, basis data sekali pakai, dan antrean untuk berkas
yang isinya cuma sel. Jadi ada tiga kemungkinan hasil, bukan dua: jalur A,
jalur B, atau **ditolak** — dan yang ketiga sering jawaban yang benar.

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
| `.mdf` | SQL Server | image 0,58 GB unduhan / **2,34 GB di disk**; `.ldf` kadang wajib (lihat bawah); versi harus cocok |
| `.dmp` | Oracle Database | beberapa GB; batas data edisi Free & lisensinya perlu dipastikan sendiri |

Tiga hal yang sering baru ketahuan di lapangan:

* **Dialek `.sql` harus diketahui lebih dulu.** Dump MySQL, PostgreSQL, Oracle,
  dan SQL Server tidak saling kompatibel.
* **`.mdf` sendirian kadang bisa, kadang tidak** — dan yang menentukan tidak
  terlihat dari berkasnya. Sudah diuji, lihat di bawah.
* **`.dmp` bisa dua format berbeda** dengan ekstensi yang sama: Data Pump
  (`impdp`) atau `exp` lama (`imp`).

### `.mdf` tanpa `.ldf` — SUDAH DIUJI

`.ldf` adalah **log transaksi** SQL Server: catatan perubahan yang belum tentu
sudah rampung ditulis ke `.mdf`. (`.ndf`, kalau ada, adalah berkas data sekunder
— itu wajib dan tidak bisa dibangun ulang sama sekali.)

Diuji dengan SQL Server 2022 sungguhan, tiga skenario, izin berkas dibuat
identik supaya yang membedakan hanya keadaan database-nya:

| | `.ldf` disertakan | Hasil |
|---|---|---|
| **A.** database di-detach baik-baik | tidak | **BERHASIL** — log dibangun ulang, 2/2 baris terbaca |
| **B.** proses SQL Server dibunuh (SIGKILL) | tidak | **GAGAL** |
| **C.** proses dibunuh, berkas yang sama | ya | **BERHASIL** — 30.000/30.000 baris |

Pesan galat pada B menerangkan sendiri sebabnya:

> The log cannot be rebuilt because there were open transactions/users when the
> database was shutdown, no checkpoint occurred to the database, or the database
> was read-only.

Jadi `ATTACH_REBUILD_LOG` memang bisa membangun ulang log yang hilang — tapi
**hanya kalau database-nya ditutup bersih**. Kalau tidak, log itu menyimpan
transaksi yang belum tercermin di `.mdf`, dan tanpa log tidak ada cara memulihkan
database ke keadaan yang konsisten. C membuktikan `.ldf` itulah yang
menyelamatkannya, bukan sekadar berkasnya kebetulan rusak.

**Akibatnya untuk kontrak unggahan portal:** operator **tidak punya cara tahu**
ia di keadaan A atau B hanya dengan melihat berkasnya. Orang yang menyalin
`.mdf` dari server yang sedang jalan akan yakin berkasnya baik-baik saja.

Karena itu:

* Portal **harus menerima `.ldf` (dan `.ndf`) sebagai berkas pendamping opsional**
  di samping `.mdf`. Mewajibkannya akan memblokir kasus A yang sebetulnya
  baik-baik saja; melarangnya akan menggagalkan seluruh kasus B tanpa jalan keluar.
* Konverter mencoba `FOR ATTACH` biasa kalau pendampingnya ada, lalu mundur ke
  `FOR ATTACH_REBUILD_LOG` kalau hanya `.mdf` yang dikirim.
* Kalau rebuild gagal, pesannya harus menyebut jalan keluarnya: *"berkas ini
  diambil saat basis datanya sedang berjalan — mintakan `.ldf`-nya juga, atau
  minta pengirim men-detach database-nya baik-baik lebih dulu."* Pesan mentah
  dari SQL Server tidak berarti apa-apa bagi operator.

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
`pg_write_server_files`. Pulihkan dalam satu transaksi yang berhenti di galat
pertama, supaya tidak ada keadaan setengah jadi.

**Sudah diuji di container konverter yang berjalan**, bukan diasumsikan:

| Sebagai peran `pemulih` | Hasil |
|---|---|
| `CREATE TABLE`, `INSERT` | **boleh** — pemulihan dump tetap jalan |
| `COPY t FROM PROGRAM 'id'` | **ditolak** — *only roles with privileges of the "pg_execute_server_program" role* |
| `COPY t FROM '/etc/passwd'` | **ditolak** — *only roles with privileges of the "pg_read_server_files" role* |
| `pg_read_file('/etc/passwd')` | **ditolak** — *permission denied for function* |

Perintah yang sama dijalankan **sebagai superuser berhasil**, dan mengembalikan
`uid=999(postgres)`. Jadi serangannya nyata dan perannyalah yang menahannya —
bukan penyaringan teks yang bisa diakali.

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
sama, dengan batas sendiri.

**Keputusan §3 menghapus kendala terbesarnya.** Rancangan semula — satu container
basis data per job — berarti tiap job menanggung waktu nyala mesinnya:
PostgreSQL beberapa detik, SQL Server puluhan detik, Oracle bisa menit. Itu yang
tadinya membatasi N, bukan CPU-nya.

Dengan satu container berumur panjang, mesinnya **sudah menyala**. Yang terjadi
per job hanya `CREATE DATABASE` dan `DROP DATABASE`, yang ongkosnya sepersekian
detik. Waktu nyala pindah dari tiap job ke sekali saat container naik.

Yang tersisa sebagai pembatas: **memori**. Satu SQL Server memegang ~2 GB
sepanjang hidupnya, dipakai atau tidak, dan Oracle lebih besar lagi. Itu ongkos
tetap yang harus disediakan sejak container naik — bukan lagi ongkos yang naik
turun mengikuti jumlah job.

Dua hal yang wajib ikut dirancang, dan keduanya dipelajari dengan mahal di
server produksi:

* **Batas waktu keras.** Pernah terjadi dua job macet memegang slotnya selama
  timeout dan seluruh antrean berhenti — tercatat `mengantre 130s sebelum
  mulai`. Dengan N kecil, satu job macet cukup untuk menyandera semuanya.
* **Antreannya harus terlihat pengguna.** "Berkas Anda nomor 3 dalam antrean"
  jauh lebih baik daripada layar diam, yang akan dilaporkan sebagai kerusakan.

---

## 6. Yang masih harus diputuskan

Tiga hal, dan semuanya menyentuh kontrak dengan portal — jadi bukan keputusan
sepihak dari sisi engine.

Yang **sudah** terjawab dan pindah ke tempatnya masing-masing: siapa yang
membelokkan ke jalur B (dispatch — §0), bentuk sandbox-nya (satu container,
database dibuang tiap job — §3), ruang lingkupnya (ketiga format — §3), dan
`.mdf` tanpa `.ldf` (diterima, dengan pendamping opsional — §3).

**Tabel mana yang berisi data kependudukan?** Dump bisa memuat puluhan tabel.
Sistem harus tahu yang mana — dari pilihan operator di portal, atau konvensi
nama yang disepakati. Tanpa itu konversinya menebak, dan menebak salah berarti
menggrading tabel yang keliru tanpa ada yang sadar.

**Dialek `.sql` dikirim portal atau dideteksi konverter?** Mendeteksi bisa,
tapi menerima keterangannya dari portal jauh lebih murah dan lebih jujur.

**Berapa batas ukuran berkas?** Menentukan disk sandbox, batas waktu, dan
apakah edisi gratis Oracle/SQL Server cukup.

---

## 7. Urutan pengerjaan yang disarankan

1. ~~**`.xlsx` di jalur A.**~~ **SELESAI.** Termasuk penolakan NIK rusak dan
   penanganan tanggal Excel. Lihat §2. Satu pelajaran dibawa ke langkah
   berikutnya: dugaan "perubahannya kecil" ternyata menyembunyikan cacat diam
   pada tanggal, dan yang menemukannya adalah pengukuran, bukan pembacaan kode.
2. ~~**Uji `.xls`** dengan berkas sungguhan.~~ **SELESAI — ditolak.** Tidak ada
   pembacanya di DuckDB, dan formatnya tidak sanggup menampung 200.000 baris.
   Lihat §2.
3. **Kerangka jalur B dengan `.sql` dialek PostgreSQL.** *Percabangan di
   dispatch SUDAH terpasang* (lihat di bawah). Yang tersisa: sandbox, antrean,
   dan kontrak keluarannya — bukan memasang basis data baru, karena mesinnya
   sudah ada.
4. ~~**`.mdf`,** memakai kerangka yang sama.~~ **SELESAI.** Lihat §3.
5. ~~**`.dmp`** terakhir.~~ **SELESAI.** Lihat §3.

Langkah 3 yang paling penting dikerjakan benar: kerangkanya dipakai ulang oleh
dua format sisanya, jadi kesalahan di situ akan terulang tiga kali.

### Yang sudah terpasang dari langkah 3

Percabangan di dispatch sudah ada, dan untuk sekarang ia **menolak**, bukan
meneruskan — karena belum ada yang bisa dituju.

* `POLA_EKSEKUTABEL` di `lib/_grading.py` mengenali `.sql`, `.dmp`, `.mdf`.
* `format_ditolak()` memberi sebab dan jalan keluarnya.
* Dipanggil DUA kali dengan sengaja: di `api1_dispatch.py` supaya penolakannya
  sampai ke pemanggil seketika, dan di `_sql_sumber()` supaya jalur mana pun
  yang melewati dispatch tetap tertutup.

Sebelum ini, berkas `.sql` jatuh ke `read_parquet('...sql')` dan gagal jauh di
dalam G2 sebagai galat parse DuckDB yang tidak menerangkan apa pun. Sekarang ia
gagal di depan, dengan kalimat yang bisa dibaca operator.

Saat konverternya jadi, yang berubah di dispatch hanya satu: `format_ditolak`
diganti pemilihan pekerja. Bentuk muatan, jumlah `jobId`, dan endpoint-nya tetap.

> **Catatan Langflow.** Perubahan di `components/` baru berlaku setelah
> **flow-nya dibangun ulang** — Langflow menyimpan salinan kode komponen di
> dalam flow, bukan membaca berkasnya. Perubahan di `lib/` cukup restart
> container. Itu sebabnya pemeriksaannya ditaruh di kedua tempat: yang di
> `lib/` sudah berlaku sekarang.

### Jalur B `.sql` — SUDAH JALAN DAN DIUJI UJUNG KE UJUNG

Dari `.sql` di S3 sampai grade, lewat pekerja grading, dengan dump pg_dump
sungguhan berisi 200.000 baris dan dua tabel umpan:

```
[K] konversi selesai: 200.000 baris dari tabel 'penduduk'
    -> uploads/uji-sql-sehat/source.parquet          (1,6 detik)
[G2] 200.000 baris, 6 kolom  (dibaca sebagai parquet)
[G4] grade B (2) — skor 89, trusted 194.754 / 200.000
STATUS AKHIR: COMPLETED
```

**Ketujuh sumber jatuh di angka yang sama persis:**

```
parquet         grade B  skor 89  trusted 194.754  anomali 176.810
CSV             grade B  skor 89  trusted 194.754  anomali 176.810
xlsx (teks)     grade B  skor 89  trusted 194.754  anomali 176.810
xlsx (tanggal)  grade B  skor 89  trusted 194.754  anomali 176.810
.sql (jalur B)  grade B  skor 89  trusted 194.754  anomali 176.810
.mdf (jalur B)  grade B  skor 89  trusted 194.754  anomali 176.810
.dmp (jalur B)  grade B  skor 89  trusted 194.754  anomali 176.810
```

Pemilihan tabelnya benar tanpa diberi tahu: `penduduk` mendapat **6/6** elemen,
`pegawai` dan `ref_agama` masing-masing 1.

Dump jahat yang menyisipkan `COPY ... FROM PROGRAM 'cat /etc/passwd'` **gagal
tepat di situ** — *permission denied to COPY to or from an external program* —
dan karena `--single-transaction`, tidak ada yang tertinggal.

#### Dua hal yang hanya muncul karena dijalankan

**1. `OWNER TO` menggagalkan hampir setiap dump, dan perbaikan yang paling
jelas justru membuka lubang.**

Pemulihan pertama berhenti di baris ke-13:

> ERROR: must be able to SET ROLE "dukcapil_admin"

`ALTER TABLE ... OWNER TO pemilik;` menuntut peran yang menjalankannya menjadi
ANGGOTA peran pemilik. Godaannya jelas: `GRANT "dukcapil_admin" TO pemulih`.
Untuk peran kosong yang baru dibuat itu memang tidak berbahaya — **tapi dump
yang dibuat superuser memuat `OWNER TO postgres`**, dan peran itu sudah ada di
sini sebagai superuser. Menjadikan `pemulih` anggotanya berarti menyerahkan
kembali seluruh hak yang baru dicabut, lewat satu baris di dalam berkas yang
justru tidak dipercaya. Lapisan 1 batal oleh perbaikan yang kelihatannya sepele.

Yang dipakai: kepemilikan **dibuang**, bukan dipenuhi. Kita hanya membaca enam
kolom lalu membuang database-nya; siapa pemilik tabelnya tidak ada artinya.
`pg_restore` punya `--no-owner` untuk alasan yang persis sama.

**2. Menambah jaringan kedua diam-diam memutus S3.**

Begitu `dalam` ditambahkan, SeaweedFS punya dua alamat — dan ia hanya
mendengarkan di satu. Konverter (172.24.0.2) jalan; langflow (172.23.0.2)
mendapat `Connection refused`, yang terlihat persis seperti SeaweedFS mati
padahal ia sedang melayani container lain dengan baik. Perbaikannya
`-ip.bind=0.0.0.0` di perintah SeaweedFS.

#### Yang masih perlu diketahui

* **Kamus master tidak tersedia di konverter.** `petakan_kolom()` di sana
  berjalan tanpa lapis kamus (`Catalog "pg" does not exist`) karena layanan ini
  tidak menyambung ke PostgreSQL utama — memang tidak boleh. Lapis alias sudah
  cukup untuk 6/6 pada dump uji, tapi dump dengan nama kolom yang tidak lazim
  akan lebih sering meleset di sini daripada di grading.
* **Batas ukuran berkas sempat dipasang salah, dan pengukuran yang
  menunjukkannya.** Versi pertama memakai `read_blob()` DuckDB, yang
  mengembalikan satu objek bytes utuh — sehingga batasnya ditentukan RAM.
  Terukur di container konverter:

  ```
  RAM  : 5.927 MB total, 3.571 MB TERSEDIA
  disk : 900.832 MB kosong
  ```

  Batas yang dipasang waktu itu 2048 MB, yaitu **lebih besar daripada RAM yang
  tersisa**. Berkas sebesar itu akan membunuh container lewat OOM kernel
  sebelum sempat ditolak — dan OOM kernel tidak meninggalkan pesan apa pun.
  Sementara disknya menganggur 900 GB.

  Sekarang unduhannya **mengalir ke disk** dengan GET bertanda tangan sendiri
  (`konverter/_s3.py`, pustaka standar, tanpa boto3). Pemakaian memori tetap
  86 MB berapa pun besar berkasnya, dan batasnya berpindah ke disk dan waktu.
  Ukurannya diperiksa dua kali: dari `Content-Length` **sebelum** satu bita pun
  diunduh, dan lagi selagi mengalir untuk berjaga kalau panjangnya tidak
  dilaporkan dengan benar. Diuji: berkas 16 MB dengan batas 1 MB ditolak
  sebelum diunduh, dan tidak ada berkas yang tertulis.

  `KONV_BATAS_MB` kini 4096 — pada ~82 bita/baris, itu sekitar **52 juta
  baris**. Yang lebih besar dari itu hampir pasti salah kirim.

### Jalur B `.mdf` — SUDAH JALAN DAN DIUJI

Berkas `.mdf` SQL Server sungguhan berisi 200.000 baris, dengan tabel umpan:

```
[K] dilampirkan: FOR ATTACH dengan .ldf
[K] konversi selesai: 200.000 baris dari tabel 'dbo.penduduk'  (4,6 detik)
[G4] grade B — skor 89, trusted 194.754
STATUS AKHIR: COMPLETED
```

Pemilihan tabelnya benar tanpa diberi tahu: `dbo.penduduk` **6/6**,
`dbo.pegawai` 1.

Diuji juga tanpa `.ldf`: berhasil, lewat `ATTACH_REBUILD_LOG` — karena database
ujinya memang di-detach dengan bersih. Itu skenario A pada tabel di atas.

#### Container terpisah per mesin, bukan satu container berisi ketiganya

Keputusan "satu container berumur panjang" tetap berlaku, tapi bentuknya **satu
container seperti itu PER MESIN**: `konverter` (PostgreSQL, port 8390) dan
`konverter-mssql` (SQL Server, port 8391). Menggabungkan ketiganya ke dalam satu
image berarti satu kegagalan — lisensi, inisialisasi, apa pun — menghentikan
ketiga formatnya sekaligus.

Pembelokannya dari ekstensi berkas, di `lib/_konversi.py`:

```python
ALAMAT = {
    ".sql": "http://konverter:8390",
    ".mdf": "http://konverter-mssql:8391",
}
```

#### DuckDB tidak punya extension SQL Server — ODBC yang jadi jalannya

Sudah diperiksa: `sqlserver`, `mssql`, dan `tds` tidak ada. Yang ada `odbc`, dan
ia bekerja: `odbc_connect()` + `odbc_query()` menembak SQL Server langsung,
memakai driver `ODBC Driver 18 for SQL Server` yang sudah ada di image-nya.

Alternatifnya mengekspor lewat `bcp` ke TSV, dan itu memaksa **membuang karakter
kendali dari data** supaya pemisahnya tidak rusak — artinya mengubah data demi
format perantara. Lewat ODBC nilainya berpindah apa adanya.

#### Pemilihan tabel dipakai BERSAMA ketiga mesin

`konverter/_umum.py` memegang dua hal yang harus sama untuk `.sql`, `.mdf`, dan
nanti `.dmp`: kolom apa yang boleh keluar, dan tabel mana yang dipilih. Kalau
tiap mesin punya jawabannya sendiri, dump yang isinya sama akan menghasilkan
berkas berbeda tergantung format kirimannya — dan itu jenis perbedaan yang tidak
akan pernah ada yang menyadarinya.

Penilaiannya memakai `ALIAS` yang sama dengan grading, lewat lapis 1 dan 2 saja
(nama kolom, bukan isinya). Lapis 3-5 membaca data, dan untuk MEMILIH tabel itu
terlalu mahal: berarti menyampel setiap tabel di dalam dump, yang bisa puluhan.

#### Tiga hal yang hanya muncul karena dijalankan

* **Volume baru dimiliki root, SQL Server berjalan sebagai `mssql`.** Matinya
  dengan `BootstrapSystemDataDirectories() failure (HRESULT 0x80070005)` —
  pesan yang tidak menyebut izin sama sekali. Diperbaiki dengan membuat dan
  meng-*chown* folder datanya di Dockerfile SEBELUM volume dipasang, persis
  seperti `langflow-data`.
* **Extension DuckDB terpasang ke `/root/.duckdb`, dibaca dari `/var/opt/mssql`.**
  Gagal dengan `Can't find the home directory at '/home/mssql'`. Diperbaiki
  dengan menyetel `HOME` saat build.
* **`os.replace` lintas filesystem gagal** (`Invalid cross-device link`) saat
  memindahkan `.mdf` dari `/tmp` ke folder data SQL Server. Menyalinnya bisa,
  tapi itu menyalin berkas basis data utuh tanpa alasan — jadi unduhannya
  diarahkan langsung ke filesystem yang benar.

### Jalur B `.dmp` — SUDAH JALAN DAN DIUJI

Dump Data Pump sungguhan (17,6 MB, 200.000 baris, dengan tabel umpan):

```
[K] skema asal di dalam dump: DUKCAPIL
[impdp] . . imported "JOB_UJI_DMP_1"."PENDUDUK"   16.4 MB  200000 rows
[K] konversi selesai: 200.000 baris dari tabel 'PENDUDUK'  (15,0 detik)
[G4] grade B — skor 89, trusted 194.754
STATUS AKHIR: COMPLETED
```

#### Unit isolasinya SKEMA, bukan database

Di PostgreSQL tiap job mendapat database baru; di SQL Server, database yang
dilampirkan. Di Oracle padanannya **USER/skema**: membuat pluggable database per
job berarti menit-menit tambahan tiap kali, sementara `CREATE USER` /
`DROP USER CASCADE` ongkosnya sepersekian detik dan membuang segalanya.

#### `INCLUDE=TABLE` adalah lapisan keamanannya, dan itu lebih kuat dari memeriksa isi

Prosedur PL/SQL, paket, trigger, kelas Java, dan job scheduler **tidak pernah
masuk ke basis data sama sekali**. Yang tidak diimpor tidak bisa berjalan, apa
pun isi dumpnya. Ditambah skema yang haknya cuma `CREATE SESSION` dan
`CREATE TABLE` — tanpa `CREATE ANY DIRECTORY`, tanpa `CREATE PROCEDURE`.

#### Tiga hal yang hanya muncul karena dijalankan

**1. Impor "berhasil" tanpa satu pun tabel, tanpa galat.** Dua baris yang mudah
terlewat di tengah keluaran impdp:

```
ORA-39154: Objects from foreign schemas have been removed from import
ORA-31655: no data or metadata objects selected for job
```

Pengguna non-privilese yang mengimpor dump milik skema LAIN tidak mendapat galat
— Oracle membuang objeknya diam-diam lalu **melaporkan sukses**. Tanpa
`REMAP_SCHEMA`, jalur ini akan selalu menghasilkan parquet kosong dan tidak ada
yang tahu kenapa.

Menaikkan hak penggunanya memang membuat impornya jalan, tapi itu membatalkan
pertahanan yang justru paling penting. Jadi yang dinaikkan **bukan haknya,
melainkan pengetahuannya**: `DBMS_DATAPUMP.GET_DUMPFILE_INFO` membaca kepala
dump-nya — API resmi Oracle, tanpa mengimpor apa pun — dan `item_code` 8
memuat nama master table-nya, `"DUKCAPIL"."SYS_EXPORT_SCHEMA_01"`. Dari situ
skema asalnya terbaca, lalu `REMAP_SCHEMA=DUKCAPIL:JOB_X`.

**2. Memindahkan baris satu per satu tidak akan pernah selesai.** Versi pertama
memakai `cur.fetchmany()` + `con.executemany()`. Ia benar, dan **masih berjalan
setelah sepuluh menit** — sementara impor Data Pump-nya sendiri selesai dalam
detik. `fetch_df_batches()` milik oracledb mengembalikan batch yang sudah
berbentuk Arrow, dan DuckDB membacanya langsung lewat antarmuka PyCapsule:
**15 detik untuk seluruh job**. Datanya tidak pernah menjadi objek Python
satu-satu.

**3. Base image Oracle membawa Python 3.6.** DuckDB 1.5.5 tidak punya wheel
untuk versi setua itu; pip di sana menawarkan duckdb 0.8.1 sebagai yang
terbaru lalu build gagal dengan "No matching distribution found". Diperbaiki
dengan memasang `python3.11` secara eksplisit.

#### `.dmp` lama (exp) ditolak, dan itu keputusan Oracle bukan batasan kita

Ada DUA format berbeda dengan ekstensi yang sama: **Data Pump**
(`expdp`/`impdp`) dan **exp lama** (`exp`/`imp`). Utilitas `imp` **dihapus
Oracle sejak versi 21**, jadi berkas exp lama tidak bisa diimpor ke mesin mana
pun yang masih didukung.

Dikenali dari penanda `EXPORT:V` di kepala berkas lalu ditolak dengan pesan yang
menyebut jalan keluarnya — daripada `impdp` gagal dengan `ORA-39001: invalid
argument value`, yang tidak menerangkan apa pun kepada siapa pun.

#### Tidak ikut naik secara bawaan

Service-nya di balik `profiles: [oracle]`. Selama belum ada instansi yang
benar-benar mengirim `.dmp`, tidak ada alasan memegang beberapa GB memori untuk
mesin yang menganggur:

```bash
docker compose --profile oracle up -d konverter-oracle
```

**Catatan lisensi:** Oracle Database Free punya batas data dan syarat pemakaian
sendiri. Sebelum dipakai di produksi, keduanya perlu dipastikan — itu bukan hal
yang bisa diputuskan dari sisi kode.

### Bentuk konverternya — SUDAH DIPUTUSKAN

**Satu container konverter berumur panjang, database dibuang tiap job.**

Yang ditolak: menjalankan container basis data baru per job. Itu menuntut
orkestratornya memegang soket Docker, dan pemegang soket Docker praktis punya
akses root ke host — tepat pada layanan yang tugasnya menjalankan berkas tidak
tepercaya. Isolasinya memang lebih kuat, tapi harganya melanggar seluruh alasan
jalur B ada.

Yang dipakai sebagai gantinya:

```
konverter (satu container, umur panjang)
  ├─ PostgreSQL / SQL Server / Oracle di dalamnya
  ├─ jaringan: TIDAK ada akses keluar
  └─ per job:
       CREATE DATABASE job_<id>      ← kosong, baru
       SET ROLE pemulih              ← non-superuser
       pulihkan dump
       COPY (kolom kontrak) → parquet
       DROP DATABASE job_<id>        ← apa pun yang dibuatnya ikut hilang
```

Isolasi antar job datang dari database yang dibuang, bukan dari container yang
dibuang. Itu lebih lemah — dua job berbagi proses basis data yang sama — tapi
lapisan yang benar-benar menahan serangan tetap utuh: peran non-superuser dan
container tanpa jaringan keluar (§4 lapisan 1 dan 2). Yang hilang hanya
pemisahan antar job, dan job-job itu semuanya berasal dari portal yang sama.

**Tidak perlu soket Docker sama sekali**, dan itu yang menentukan pilihannya.

### Ruang lingkup — SUDAH DIPUTUSKAN: ketiganya

`.sql`, `.dmp`, dan `.mdf` semuanya akan didukung. Urutan pengerjaannya tetap
seperti §7: `.sql` dialek PostgreSQL lebih dulu, karena mesinnya sudah ada dan
kerangkanya dipakai ulang dua format sisanya.

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
