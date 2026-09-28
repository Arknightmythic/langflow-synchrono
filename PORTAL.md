# Catatan untuk tim portal — unggahan multi-format

Engine grading sekarang menerima lebih banyak format berkas. Dokumen ini hanya
memuat yang menyentuh sisi portal: apa yang **tidak** berubah, apa yang perlu
**diputuskan**, dan apa yang sebaiknya **ditampilkan ke pengguna**.

Rincian teknis di sisi engine ada di `UNGGAHAN.md` dan `GRADING.md`.

---

## 1. Yang TIDAK berubah — dan ini bagian terpentingnya

**Cara mengunggah tetap sama.** Portal menaruh berkas di

```
uploads/{fileId}/raw/<nama berkas asli>
```

persis seperti yang sudah dilakukan untuk CSV. Tidak ada endpoint unggah baru,
tidak ada bucket baru, tidak ada perubahan struktur folder.

**Cara memanggil grading tetap sama.** Satu endpoint untuk semua format:

```http
POST {{base_url}}/api/v1/run/grading-dispatch?stream=false
```

dengan `OutboundGradingJobPayload` yang sama seperti sekarang.

**Cara menanyakan hasil tetap sama.** `grading-status` dan webhook callback
berlaku apa adanya, dan **satu `fileId` tetap menghasilkan satu `jobId`** dari
awal sampai selesai — termasuk untuk format yang perlu dikonversi lebih dulu.
Konversi muncul sebagai tahap di kolom `stage`, bukan sebagai job kedua.

Jadi: **portal tidak perlu tahu ada pembagian jalur di belakang.** Engine yang
memeriksa ekstensi `rawSourceKey` dan menentukan sisanya.

---

## 2. Format yang diterima sekarang

| Ekstensi | Status | Catatan |
|---|---|---|
| `.parquet` | diterima | tercepat, tetap yang dianjurkan |
| `.csv` `.tsv` `.txt` | diterima | seperti sebelumnya |
| `.xlsx` | **baru** | diuji 200.000 baris, hasilnya identik dengan CSV |
| `.sql` | **baru** | dialek PostgreSQL atau MySQL/MariaDB; dikonversi dulu, otomatis. Kirim `sqlDialect` kalau tahu — lihat §6.3 |
| `.mdf` | **baru** | SQL Server; `.ldf` pendamping opsional — lihat §6.2 |
| `.xls` | **ditolak** | lihat §3 |
| `.dmp` | **baru** | Oracle Data Pump (`expdp`). Format `exp` lama ditolak — lihat §3b |

Berkas yang ditolak gagal **di saat dispatch**, bukan beberapa menit kemudian.
Pesan galatnya sudah ditulis untuk dibaca operator, jadi bisa ditampilkan apa
adanya.

---

## 3. `.xls` ditolak — mohon disaring di sisi portal juga

`.xls` (Excel 97-2003) tidak bisa dibaca sama sekali, dan alasan ketiganya yang
menentukan: **formatnya hanya memuat 65.535 baris data per lembar.** Berkas
kependudukan yang biasa dikirim berisi 200.000 baris, jadi `.xls` tidak akan
pernah memuatnya utuh.

Sebaiknya portal menolaknya saat dipilih, dengan pesan:

> Format .xls tidak didukung. Simpan ulang sebagai .xlsx (Excel > Save As >
> Excel Workbook), atau ekspor ke CSV.

---

## 3b. `.dmp` — hanya Data Pump, bukan `exp` lama

Ada **dua format berbeda dengan ekstensi `.dmp` yang sama**, dan hanya satu yang
bisa diterima:

| Dibuat dengan | Bisa? |
|---|---|
| `expdp` (Data Pump, sejak Oracle 10g) | **ya** |
| `exp` (utilitas lama) | **tidak** |

Ini bukan batasan di sisi kami: **Oracle sendiri menghapus utilitas `imp`** yang
bisa membaca format lama, sejak Oracle 21. Berkas seperti itu tidak bisa diimpor
ke mesin Oracle mana pun yang masih didukung.

Engine mengenalinya dari isi berkas dan menolak dengan pesan yang menyebut jalan
keluarnya: minta pengirim mengekspor ulang dengan `expdp`, atau ekspor ke CSV.

Kalau portal bisa menanyakan ini ke pengirim di muka, itu menghemat satu putaran
unggah-tolak yang mungkin berukuran gigabita.

---

## 4. Jebakan Excel yang perlu diberitahukan ke pengguna

Ini yang paling penting untuk ditampilkan di UI, karena akibatnya diam.

Kalau kolom NIK diketik sebagai **angka** di Excel, Excel menyimpannya sebagai
bilangan pecahan. NIK 16 digit melewati batas presisi eksaknya, sehingga digit
terakhirnya **bergeser saat berkas disimpan** — sebelum berkasnya dikirim ke
mana pun. Tidak ada cara memulihkannya dari sisi engine.

NIK yang meleset satu digit tetap berbentuk NIK yang sah: ia lolos pemeriksaan
bentuk, lolos grading, lalu dicocokkan ke **orang yang berbeda** saat matching.

Engine menandai baris seperti itu `nik_trusted = false` dan **tidak menolak
berkasnya** — baris sehat lainnya tetap terpakai. Tapi pencegahan di hulu jauh
lebih murah.

**Saran untuk UI unggah Excel:**

> Pastikan kolom NIK diformat sebagai **Text** sebelum menyimpan. NIK yang
> diketik sebagai angka akan rusak di dalam berkas Excel dan tidak bisa
> diperbaiki setelahnya.

Terukur pada 200.000 baris uji: dari 31.626 NIK yang melewati batas presisi,
**15.755 benar-benar bergeser**. Yang tidak bergeser tidak bisa dibedakan dari
yang bergeser, jadi semuanya ditandai tidak tepercaya.

---

## 5. Field baru di muatan hasil

Satu kunci ditambahkan ke `caseFlags`:

```json
"caseFlags": {
  "hasExcelScientificNik": false,
  "hasExcelPrecisionNik": true,     // BARU
  "hasAmbiguousDateFormats": false
}
```

`hasExcelPrecisionNik` bernilai `true` kalau ada NIK yang terdampak §4.

**Portal tidak perlu berubah untuk ini** — ini kunci tambahan, bukan perubahan
kunci yang ada. Tapi menampilkannya menjelaskan kenapa angka NIK tepercaya
turun pada berkas yang kelihatannya normal. Tanpa itu, penurunannya akan
terlihat seperti kerusakan.

Muatan hasil memang sudah memuat beberapa kunci di luar spesifikasi integrasi
(`nikKecamatanInvalidCount`, `nameWithTitleCount`, `normalization`, dan lainnya);
yang ini mengikuti pola yang sama.

---

## 6. Yang perlu DIPUTUSKAN di sisi portal

Tiga hal. Semuanya menyentuh kontrak unggahan, jadi tidak bisa diputuskan
sepihak dari sisi engine.

### 6.1 Batas ukuran berkas

Engine saat ini membatasi **4096 MB** untuk berkas yang perlu dikonversi
(`.sql`). Angka itu bukan batas teknis yang keras — unduhannya mengalir ke disk,
jadi memori tidak jadi kendala.

Yang jadi kendala adalah **waktu**. Pada ~82 bita/baris, 4 GB berarti sekitar 52
juta baris, dan pemulihannya bisa berjam-jam.

Pertanyaannya bukan "berapa yang sanggup", melainkan **"berapa lama pengguna
boleh menunggu sebelum menganggap sistemnya rusak"**. Kalau jawabannya 10 menit,
batasnya jauh lebih kecil dari 4 GB.

Mohon tentukan angkanya, dan tolak di sisi portal sebelum diunggah — jauh lebih
baik daripada pengguna menunggu unggahan 3 GB selesai lalu ditolak.

### 6.2 Berkas pendamping untuk `.mdf` — SUDAH BISA, dan ini yang perlu dikirim

Database SQL Server terdiri dari `.mdf` (data) dan `.ldf` (log transaksi).

Sudah diuji dengan SQL Server 2022:

| Keadaan | `.ldf` disertakan | Hasil |
|---|---|---|
| database di-detach baik-baik | tidak | **berhasil** |
| proses SQL Server mati mendadak | tidak | **GAGAL** |
| proses mati mendadak, berkas sama | ya | **berhasil** |

Masalahnya: **pengguna tidak punya cara tahu ia di keadaan yang mana.** Orang
yang menyalin `.mdf` dari server yang sedang berjalan akan yakin berkasnya
baik-baik saja.

Jadi portal perlu **menerima `.ldf` (dan `.ndf` kalau ada) sebagai berkas
pendamping opsional** di samping `.mdf`. Mewajibkannya akan memblokir berkas
yang sebetulnya sehat; melarangnya akan menggagalkan sebagian tanpa jalan keluar.

**Cara mengirimkannya:** unggah `.ldf` ke folder yang sama seperti `.mdf`, lalu
tambahkan satu field ke muatan dispatch:

```json
{
  "rawSourceKey": "uploads/{fileId}/raw/data.mdf",
  "logKey":       "uploads/{fileId}/raw/data.ldf"
}
```

`logKey` opsional. Tanpa itu engine mencoba membangun ulang lognya sendiri, dan
kalau tidak bisa, pesan gagalnya sudah menyebutkan jalan keluarnya:

> Berkas .mdf ini diambil saat basis datanya masih berjalan... Mintakan berkas
> .ldf-nya juga, atau minta pengirim men-detach database-nya baik-baik lebih
> dulu lalu mengirim ulang.

### 6.3 Dua keterangan opsional untuk `.sql` (sangat membantu, tidak wajib)

Engine bisa menebak keduanya, tapi keterangan selalu lebih baik daripada tebakan:

| Field | Gunanya kalau dikirim |
|---|---|
| `sqlDialect` | `"postgresql"`, `"mysql"`, `"mariadb"`, `"oracle"`, `"sqlserver"`. Yang dipulihkan: `postgresql` dan `mysql`/`mariadb` (mesin terpisah — `sqlDialect` menentukan ke mana dump dikirim). Tanpa ini engine menebak dari kepala berkas, dan dump yang tidak lazim bisa meleset **tanpa memberi tanda**. |
| `sourceTable` | Nama tabel yang berisi data kependudukan. Tanpa ini engine memilih sendiri dari nama kolomnya. Untuk `.mdf`, sertakan skemanya: `dbo.penduduk`. |

Untuk `sourceTable`: pemilihan otomatis sudah bekerja baik pada dump uji (tabel
`penduduk` terpilih 6/6 elemen, mengalahkan `pegawai` dan `ref_agama`). Tapi
dump sungguhan bisa memuat puluhan tabel, dan **memilih tabel yang keliru tidak
menimbulkan galat** — hasilnya cuma grade yang aneh.

Kalau memungkinkan, tampilkan daftar tabel ke operator dan minta ia memilih.

---

## 7. Antrean perlu terlihat pengguna

Konversi `.sql` berjalan **satu per satu**, tidak paralel — ia memegang satu
basis data, bukan sekadar kueri.

Artinya berkas kedua yang diunggah bersamaan akan menunggu. Kolom `stage` pada
`grading-status` melaporkan keadaannya, dan layanan konversi juga punya endpoint
sehat yang melaporkan panjang antrean.

Mohon tampilkan sesuatu seperti *"Berkas Anda nomor 3 dalam antrean"*. Layar
diam selama beberapa menit akan dilaporkan sebagai kerusakan, dan itu memakan
waktu semua orang.

---

## 8. Ringkasan yang perlu ditindaklanjuti

| | Tindakan |
|---|---|
| 1 | Izinkan `.xlsx` dan `.sql` di dialog unggah |
| 2 | Tolak `.xls` di sisi portal, dengan pesan "simpan ulang sebagai .xlsx" |
| 3 | Tampilkan peringatan "format kolom NIK sebagai Text" pada unggah Excel |
| 4 | **Tentukan batas ukuran berkas** (§6.1) dan tolak sebelum diunggah |
| 5 | Tampilkan posisi antrean, jangan layar diam (§7) |
| 6 | Opsional: kirim `sqlDialect` dan `sourceTable` (§6.3) |
| 7 | Opsional: tampilkan `caseFlags.hasExcelPrecisionNik` (§5) |
| 8 | Terima `.ldf`/`.ndf` sebagai pendamping `.mdf`, kirim lewat `logKey` (§6.2) |

Nomor 4 yang paling mendesak, karena tanpa itu pengguna bisa menunggu berjam-jam
untuk berkas yang sebetulnya salah kirim.

---

# Bagian B — Matching (`matching-dispatch`)

Engine sudah mengikuti `matching-engine-integration-spec.md` versi 27 Sep 2026,
dari dispatch sampai callback, dan sudah diuji ujung ke ujung terhadap **tiruan**
tabel portal di PostgreSQL lokal. Belum pernah menyentuh DB portal sungguhan.

## B1. Yang dibutuhkan engine dari tim portal

| | Keterangan |
|---|---|
| **Alamat DB portal** | connection string ke database yang memuat `syncrono_matching_job` & `syncrono_matching_result` |
| **User PostgreSQL** | `SELECT`, `UPDATE` pada `syncrono_matching_job`; `DELETE`, `INSERT` pada `syncrono_matching_result` — tidak lebih |
| **Skema tabelnya** | spesifikasi 27 Sep **menghapus definisi tabelnya**. Tiruan kami (`infra/simulasi_portal/skema.sql`) diambil dari versi 21 Sep + dua kolom baru (`result_parquet_key`, `reasoning`). Mohon konfirmasi atau kirim DDL yang sebenarnya |
| **Encoding UTF-8** | nama berkarakter non-Latin gagal masuk ke database WIN1252 |

## B2. Yang sudah sesuai spesifikasi — tidak perlu diubah di portal

- Endpoint `POST /api/v1/run/matching-dispatch?stream=false`, node
  **`MatchingDispatch-b4819`** persis seperti di spesifikasi.
- Header `x-api-key` dan `Authorization: Bearer` dikirim bersamaan — diterima.
- Balasan `IN_PROGRESS` dalam < 1 detik.
- `result.parquet` 18 kolom di `matching-results/{jobId}/result.parquet`.
- Callback §7.1 kunci per kunci, dengan header `x-callback-source: matching-engine`.
- **`grade` tidak perlu dikirim.** Engine mencarinya sendiri dari hasil grading
  berkas itu.

## B3. Yang perlu diketahui portal

**Baris job harus dibuat SEBELUM dispatch.** Engine memeriksanya, dan hasil
tidak bisa disuntik untuk job yang tidak ada. Tanpa baris itu engine membalas
callback `FAILED` dengan sebabnya.

**Galat muatan dibalas HTTP 500, bukan 400.** Itu perilaku Langflow untuk galat
di dalam komponen dan tidak bisa diubah dari sisi kami. Sebabnya ada di
`detail`, dan semua field yang kurang disebut sekaligus:
`Field wajib tidak ada: callbackUrl, masterDataFile.s3Key`.

**`s3Endpoint: http://localhost:8333` diabaikan.** Dari dalam container
`localhost` berarti container itu sendiri. Contoh di spesifikasi memakai nilai
ini; engine memakai alamat S3 dari konfigurasinya.

**`reasoning` terisi di SETIAP baris, semua status**, berbahasa Indonesia
mengikuti contoh §6 spesifikasi — rata-rata ±220 karakter, terpanjang ±440.
Contoh REVIEW: *"Skor kemiripan 90.0% terhadap master NIK 1906316707661883
belum memenuhi syarat pencocokan otomatis. Nama lengkap, tanggal lahir, dan
jenis kelamin identik. Nama ibu kandung dan tempat lahir kosong pada data
incoming."* Kalau tahap reasoning gagal, kolomnya NULL dan hasil matching
tetap tersuntik — portal sebaiknya menampilkan NULL sebagai "tidak tersedia",
bukan galat.

**UNMATCH tidak membawa `master_nik` maupun `master_snapshot`** (keduanya
NULL, persis §4.1), dan `rank_conflict`-nya selalu `false`. Sebelumnya UNMATCH
bisa membawa kandidat terdekat — yang justru ditolak engine. `score` tetap
berisi skor kandidat terdekat itu, dan `reasoning` menyebutnya.

**`stageDurations` di callback punya satu kunci tambahan, `reasoningMs`.**
Selama reasoning berjalan, `current_stage` tetap `CLASSIFYING` — kami tidak
menambah nilai tahap baru karena tidak tahu apakah kolom itu dibatasi
constraint atau dipetakan UI portal.

**Angka "Perlu Review" akan TURUN**, dan itu bukan kerusakan. Matching sekarang
bertahap per baris (Pass 1 NIK + nama persis, Pass 2 nama + tanggal lahir +
ibu persis, Pass 3 skor). Pada berkas uji grade B: 17.218 baris yang dulu
REVIEW kini AUTO (NIK dan nama persis, atribut lain hanya kosong), dan 23.922
yang dulu UNMATCH kini ketemu (NIK di berkas salah, orangnya ditemukan lewat
identitas). Tidak ada satu baris pun yang berpindah ke orang yang berbeda.

## B4. Tiga hal di spesifikasi yang sebaiknya diluruskan

**1. Callback tanpa rahasia.** Grading mengirim `secretToken`; matching tidak.
Siapa pun di jaringan bisa mengirim callback `COMPLETED` palsu ke portal.
Usulan: tambahkan `callbackToken` ke payload dispatch, dan engine
mengembalikannya di header — pola yang sama dengan grading.

**2. Contoh kode injeksi (§5.1) rentan injeksi SQL.** Nilai seperti `actor`
(email operator) ditempel langsung ke string SQL. Engine tidak memakai pola itu,
tapi kalau contoh yang sama dipakai di tempat lain di portal, risikonya sama.

**3. `rulePreset` belum didefinisikan.** Nilainya disebut (FAST / BALANCED /
THOROUGH / STRICT) tanpa arti. Engine menerimanya tapi belum bertindak apa pun
atasnya — menebak artinya lebih berbahaya daripada mengabaikannya.

## B5. Kecepatan penyuntikan bergantung pada tabel portal

Pada 200 ribu baris, penyuntikan memakan ~16 dari ~20 detik total. Kami ukur
sebabnya: memindahkan datanya ke tabel **tanpa** indeks hanya 8,7 detik;
biaya selebihnya ada di **PostgreSQL** — satu FK dan tiga indeks per baris.

Jadi angka di server ditentukan indeks dan FK yang dipasang di tabel portal.
Dua hal yang membantu:

- **Indeks `(csv_file_id, master_file_id)`** — wajib ada, karena setiap
  penyuntikan diawali `DELETE` pada pasangan itu. Tanpanya DELETE memindai
  seluruh tabel hasil, yang tumbuh dengan setiap berkas.
- **`id` yang dikirim engine berurutan waktu (UUIDv7)**, bukan acak — terukur
  26% lebih cepat masuk ke indeks primary key. Tetap UUID yang sah.

Teks `reasoning` menambah ±17% pada waktu penyuntikan (terukur +2,3 sampai
+5,7 detik per 200 ribu baris di laptop kami). Penyuntikan berulang untuk berkas
yang sama meninggalkan tuple mati di tabel hasil. Di tiruan kami, setelah
belasan penyuntikan ulang tabelnya 1,59 GB dengan 596 ribu tuple mati, dan
penyuntikan naik dari ±17 ke 44–57 detik (hanya ±4 detik di antaranya dari
reasoning) — pengaturan autovacuum tabel itu ikut menentukan.
