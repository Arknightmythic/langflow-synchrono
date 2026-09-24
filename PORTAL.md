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
| `.sql` | **baru** | dialek PostgreSQL; dikonversi dulu, otomatis |
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
| `sqlDialect` | `"postgresql"`, `"mysql"`, `"oracle"`, `"sqlserver"`. Tanpa ini engine menebak dari kepala berkas, dan dump yang tidak lazim bisa meleset **tanpa memberi tanda**. |
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
