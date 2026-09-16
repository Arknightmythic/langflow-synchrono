# Normalisasi & Pembersihan Berkas Masuk

Dua masalah yang ditangani, keduanya membuat berkas bagus dinilai lebih rendah
dari seharusnya:

1. **Nama kolom berbeda** — berkas berisi enam elemen lengkap, tapi kolomnya
   bernama `no_identitas`, `NAMA LENGKAP WP`, `tgl lhr`. Tidak dikenali,
   jatuh ke grade F.
2. **Format tanggal bercampur** — satu berkas memuat `01-12-1983`,
   `15 Oct 1967`, `13/07/58`, dan `03-18-1991` sekaligus.

Keduanya diselesaikan sebelum penilaian, jadi grade dihitung dari isi berkas —
bukan dari seberapa baku penamaannya.

---

## 1. Hasilnya

Diukur pada berkas nyata:

| | Sebelum | Sesudah |
|---|---|---|
| Berkas berheader samaran (6 elemen) | 1 dari 6 elemen dikenali | **6 dari 6** |
| Berkas berkolom `kolom_satu/dua/tiga` | grade F | **grade E** |
| Tanggal terbaca di `data_dukcapil_gradeE` (200.000 baris) | 161.112 (80,6%) | **200.000 (100%)** |

**38.888 baris tanggal yang sebelumnya hilang jadi NULL kini terbaca.**

Kesembilan berkas uji dan keempat belas berkas produksi tetap menghasilkan
grade yang sama — kecuali dua berkas yang memang naik karena kolomnya kini
dikenali.

---

## 2. Nama kolom: lima lapis, AI paling akhir

```
1  alias persis          "no_identitas" -> nik
2  nama mirip            "NAMA LENGKAP WP" ~ "nama_lengkap" (rapidfuzz 92)
3  bentuk NILAI          16 digit berkode provinsi sah -> nik
4  kamus tabel master    nilai cocok 100% ke master.kecamatan -> kecamatan
5  AI                    satu panggilan per berkas, ~1,3 detik
```

Tiap lapis hanya menangani kolom yang belum terpetakan lapis sebelumnya, dan
prosesnya berhenti begitu semua kolom terpetakan. Berkas berpenamaan baku tidak
pernah melewati lapis 1.

### Lapis 3 mengabaikan header sepenuhnya

Ini yang menjawab kasus "`no_identitas` isinya NIK":

| Tanda | Kesimpulan |
|---|---|
| >80% nilai 16 digit **dan** dua digit pertamanya kode provinsi sah | `nik` |
| >70% nilai berpola tanggal | `tanggal_lahir` |
| ≤8 nilai unik, semuanya dalam kosakata L/P | `jenis_kelamin` |
| ≤8 nilai unik, semuanya hidup/mati | `status_hidup` |

Syarat kode provinsi pada NIK bukan hiasan: tanpa itu, nomor rekening 16 digit
ikut tertangkap.

### Lapis 4 memakai kamus yang sudah kita punya

`master` berisi 99 tempat lahir, 38 provinsi, 512 kabupaten, 6.887 kecamatan,
52.194 kelurahan, dan 226.407 nama. Kolom teks bebas dikenali dari seberapa
banyak nilainya muncul di kamus itu.

Yang menentukan **selisih terhadap pesaing**, bukan skor mutlak — dan pesaing
yang sudah diambil kolom lain dikeluarkan dari perbandingan. Tanpa aturan
kedua, kolom nama ibu tertolak hanya karena mirip kamus `nama` sebesar 88%,
padahal `nama` sudah dipegang kolom lain dan tidak mungkin jadi jawabannya.

### Lapis 5 (AI) jarang diperlukan

Yang benar-benar tidak bisa diputuskan mesin hanyalah membedakan kolom teks
bebas yang tidak ada di kamus. `nama` dan `nama_ibu` misalnya — keduanya nama
orang dari sebaran yang sama, dan hanya header yang membedakannya.

Diuji: 12 nama kolom samaran, model memetakan **12/12 benar** dalam 1,3 detik,
**tanpa melihat satu nilai pun**.

---

## 3. AI: apa yang dikirim, ke mana

```yaml
NORMALISASI_AI: "nama_saja"      # off | nama_saja | dengan_sampel
NORMALISASI_AI_BASE_URL: ""      # kosong = AI mati
NORMALISASI_AI_MODEL: "gemma4:31b"
```

| Mode | Yang dikirim |
|---|---|
| `off` | tidak memanggil AI sama sekali |
| `nama_saja` | **hanya nama kolom** — bawaan |
| `dengan_sampel` | nama kolom + beberapa contoh nilai |

### Kapan `dengan_sampel` benar-benar menolong

Hanya ketika nama kolom TIDAK memberi petunjuk apa pun **dan** nilainya tidak
ada di kamus master. Diuji pada berkas `samar-g` — kolom bernama `c1`…`c5`
berisi nama orang dan kota yang sengaja di luar master:

| Mode | Elemen dikenali | Grade | Latensi | Token |
|---|---|---|---|---|
| `nama_saja` | 2 dari 6 | **F** | 0,79 s | 198 |
| `dengan_sampel` | 5 dari 6 | **C** | 0,86 s | 338 |

Selisihnya besar: F berarti berkas butuh pemetaan kolom kustom sebelum bisa
dicocokkan, C berarti langsung bisa. Biayanya +140 token dan +0,07 detik — sekali
per berkas.

Pada mode `nama_saja`, model **tidak menebak** untuk `c1`/`c2`/`c5` melainkan
membiarkannya kosong. Itu perilaku yang diinginkan; kolom yang salah dipetakan
lebih berbahaya daripada kolom yang tidak dipetakan.

Keenam berkas samaran lain tetap **35/35 pemetaan benar** saat sampling
dinyalakan — sampling tidak merusak yang sudah betul.

> **Satu keterbatasan yang tetap ada.** Pada `samar-g`, `c1` dan `c5`
> sama-sama nama orang dari kumpulan yang sama. Model memetakan `c1` ke `nama`
> dan `c5` ke `nama_ibu` mengikuti urutan kolom — lazim, tapi itu konvensi,
> bukan kepastian. Berkas yang menaruh nama ibu lebih dulu bisa saja tertukar.

### Dua endpoint bernama sama, sifatnya berbeda

```
OLLAMA_LOCAL_BASE_URL   http://172.16.12.98:11434   on-prem
OLLAMA_CLOUD_BASE_URL   https://ollama.com          layanan hosted, internet publik
```

Yang kedua **bukan** on-prem meski sama-sama bernama Ollama. Mengarahkan ke
sana dengan mode `dengan_sampel` berarti NIK dan nama ibu kandung keluar dari
jaringan.

Karena itu kombinasi itu **ditolak** kecuali disengaja:

```
endpoint https://ollama.com BUKAN jaringan lokal. Contoh nilai tidak dikirim
karena akan membawa NIK dan nama keluar jaringan. Arahkan
NORMALISASI_AI_BASE_URL ke Ollama on-prem, atau setel
NORMALISASI_AI_IZIN_SAMPEL_LUAR=1 kalau itu memang disengaja.
```

Endpoint lokal (`127.0.0.1`, `10.x`, `172.16–31.x`, `192.168.x`) dianggap
on-prem dan tidak dihalangi.

---

## 4. Tanggal: satu format, tanpa AI per baris

Keluarannya selalu `dd-mm-yyyy`. Yang ditangani:

| Bentuk | Contoh | Catatan |
|---|---|---|
| ISO | `1988-10-21` | |
| Numerik | `02-10-1990`, `02/10/1990`, `25 07 1985` | |
| Nama bulan Inggris | `15 July 1967`, `04-Sep-1958`, `15 Oct 1967` | |
| **Nama bulan Indonesia** | `17 Agustus 1945`, `05-Okt-1970` | diterjemahkan dulu |
| **Tahun 2 digit** | `13/07/58`, `29-10-80` | pivot khusus tanggal lahir |
| **Urutan MM-DD** | `03-18-1991` | dikenali per baris |
| **Timestamp** | `1990-10-02 00:00:00`, `02/10/1990 00:00` | jam dibuang dulu |
| **Serial Excel** | `32874` | hari sejak 1899-12-30 |
| Gaya Amerika | `Oktober 2, 1990` | |

Diuji dengan 15 bentuk: **11 bentuk sah semuanya terbaca**, dan 4 bentuk rusak
(`''`, `'-'`, `00-00-0000`, `31-02-1990`) benar-benar ditolak jadi NULL.

Serial Excel hanya diterima 5 digit, kecuali kolomnya memang didominasi angka
bulat — di kolom tanggal biasa, `1990` hampir pasti tahun, bukan serial hari
ke-1990 (yang berarti 13 Juni 1905).

### Urutan DD-MM vs MM-DD: mayoritas berkas yang menentukan

`03-05-1991` bisa 3 Mei atau 5 Maret — tidak ada cara memastikannya dari satu
baris. Tapi `25-07-1985` hanya masuk akal sebagai DD-MM (tidak ada bulan ke-25),
dan `03-18-1991` hanya masuk akal sebagai MM-DD. Baris-baris itulah buktinya:

```
87.571 baris hanya masuk akal sebagai DD-MM, 6.658 hanya sebagai MM-DD;
61.351 baris ambigu dibaca sebagai DMY
```

Baris yang tidak terbantahkan diselesaikan sendiri-sendiri; yang ambigu
mengikuti mayoritas berkas. Angka-angka itu ikut di muatan callback, jadi
keputusannya bisa diperiksa.

### Tahun 2 digit perlu pivot sendiri

Pivot bawaan memetakan `58` → **2058** dan `30` → **2030**. Untuk tanggal lahir
keduanya mustahil. Aturannya: apa pun yang jatuh di masa depan dimundurkan satu
abad — sekaligus menjaga `05` → 2005 tetap apa adanya, karena orang yang lahir
2005 memang ada.

---

## 5. Wilayah disimpan apa adanya

Yang diseragamkan **namanya**, bukan bentuknya:

- berkas dengan satu kolom gabungan → tetap satu kolom, bernama `wilayah`
- berkas dengan pecahan → tetap terpecah, bernama `provinsi`, `kabupaten`,
  `kecamatan`, `kelurahan`
- berkas dengan sebagian → yang ada saja

Tidak ada pemecahan maupun penggabungan.

---

## 6. Berkas hasil

`enriched.parquet` berisi, berurutan:

1. **kolom baku** — `nik`, `nama`, `tempat_lahir`, `tanggal_lahir`,
   `jenis_kelamin`, `nama_ibu`, `wilayah`, `provinsi`, `kabupaten`,
   `kecamatan`, `kelurahan`, `id`, `status_hidup` (yang ada saja)
2. **kolom lain apa adanya** — kolom tak dikenali tetap dibawa; berkas instansi
   sering memuat keterangan yang tidak dipakai matching tapi berarti bagi
   pemiliknya
3. **8 kolom derivasi** — `nik_clean` … `anomaly_notes`

### Matching membaca berkas ini, bukan parquet mentah

Node `1. Open Matching Session` mencari hasil grading dari `grading_jobs`
berdasarkan `file_id`, lalu memakai `enriched.parquet` bila ada:

```
[N1] parquet: s3://synchrono/uploads/d88150c5/enriched.parquet
[N1] sumber : hasil grading (kolom ternormalisasi + enrichment)
```

**Backend tidak perlu diubah** — jalurnya dicari dari `file_id` yang memang
sudah dikirim. Kalau belum ada hasil grading, parquet dari muatan dipakai apa
adanya, dan itu dicatat di log.

Diverifikasi: matching atas `enriched.parquet` menghasilkan angka yang sama
persis dengan atas parquet mentah — 199.386 auto-match, 0 review, 614 unmatch.

Untuk memaksa memakai parquet mentah: kolom `Pakai Enriched` di node, atau
`--parquet-mentah` di `run_local.py`.

---

## 7. Jejak keputusan

Muatan callback memuat bagian `normalization`:

```json
{
  "byLayer": {"layer1": 6, "layer2": 0, "layer3": 0, "layer4": 0, "layer5": 0},
  "renamed": [
    {"from": "no_identitas", "to": "nik", "layer": 1, "reason": "alias 'no_identitas'"},
    {"from": "tgl lhr", "to": "tanggal_lahir", "layer": 1, "reason": "alias 'tgl_lhr'"}
  ],
  "unrecognisedColumns": ["keterangan"],
  "dateNormalisation": {
    "convention": "DMY",
    "outputFormat": "dd-mm-yyyy",
    "evidenceDayMonth": 663,
    "evidenceMonthDay": 176,
    "ambiguousRows": 561
  },
  "aiUsed": false,
  "trace": [ ... ]
}
```

`byLayer` adalah cara tercepat melihat apakah sebuah berkas butuh pertolongan
AI. Kalau grade mengejutkan, `renamed` menunjukkan kolom mana dipetakan ke apa
dan atas dasar apa.

---

## 8. Hasil uji enam berkas samaran

Enam CSV dummy dibangkitkan dari potongan `test-data-csv` (1.500 baris per
berkas), masing-masing dengan **nama kolom dan isi yang berbeda**, dan
masing-masing menyasar lapis pengenalan yang berbeda:

| Berkas | Header | Lapis yang menyelesaikan | Pemetaan |
|---|---|---|---|
| `samar-a` | `no_kependudukan`, `nama_ibunda`, `jk` | 1 | **6/6 benar** |
| `samar-b` | `N.I.K.`, `Nama Lengkap`, `Tempat Lahir` | 1 | **6/6 benar** |
| `samar-c` | `f1` … `f5` | 3 dan 4 | **5/5 benar** |
| `samar-d` | `kol_1` … `kol_5` | 3 dan 4 | **5/5 benar** |
| `samar-e` | `nm`, `tmp`, `tgl`, `prop`, `kab_kot`, `kecmtn` | 1, 2, 3, 4, **5** | **9/9 benar** |
| `samar-f` | nilai di luar kamus master | **5** | **4/4 benar** |

**35 pemetaan, 35 benar, 0 salah.** Kelima lapis terpakai.

`samar-e` menunjukkan kaskadenya bekerja sebagaimana dirancang: `prop`
diselesaikan alias, `kab_kot` nama mirip, `tgl` dan `stat` bentuk nilai, `tmp`
`kecmtn` `desa_kel` kamus master, dan hanya `nm` serta `ibu` yang sampai ke AI —
dua kolom yang memang tidak bisa dibedakan dari nilainya, karena keduanya nama
orang dari sebaran yang sama.

`samar-f` sengaja berisi nama dan kota yang **tidak ada** di tabel master
(Singapore, Jeddah, Kuala Lumpur), sehingga lapis 3 dan 4 sama-sama buntu.
AI memetakan keempatnya benar dari nama kolomnya saja.

### Tanggal

Tiap berkas ditulis ulang dalam tujuh bentuk berbeda dari satu tanggal yang
sama, lalu hasilnya diadu baris per baris dengan CSV sumber:

| | `samar-a` | `samar-b` | `samar-c` | `samar-e` | `samar-f` |
|---|---|---|---|---|---|
| tepat | 100% | 94,5% | 94,8% | 93,8% | 94,9% |
| ambigu | 0 | 57 | 78 | 60 | 41 |
| **gagal dibaca** | **0** | **0** | **0** | **0** | **0** |
| **salah** | **0** | **0** | **0** | **0** | **0** |

Baris "ambigu" adalah yang ditulis dalam urutan MM-DD dengan hari **dan** bulan
sama-sama ≤ 12 — `06-04-1962` sah dibaca 6 April maupun 4 Juni. Baris itu
mengikuti mayoritas berkas, dan sebagian memang meleset. Itu batas yang
disepakati, bukan kegagalan: tidak ada informasi di berkas yang bisa
memastikannya.

Yang penting: **nol baris gagal dibaca, dan nol baris salah di luar keambiguan
itu.**

### Menjalankan ulang

```bash
# 1. salin potongan CSV sumber ke container (1.500 baris per grade)
docker exec synchrono-langflow mkdir -p /tmp/sumber
docker cp test-data-csv/data_dukcapil_gradeA.csv synchrono-langflow:/tmp/sumber/gradeA.csv
# ... dan seterusnya untuk B, C, D, E

# 2. bangkitkan enam berkas samaran
docker exec synchrono-langflow python /synchrono/infra/buat_csv_samar.py

# 3. periksa kebenarannya
docker exec synchrono-langflow python /synchrono/infra/periksa_samar.py
```

Keluar dengan status 1 kalau ada pemetaan yang salah atau tanggal yang keliru.

---

## 9. Menguji berkas lain

```bash
# bangkitkan berkas uji, termasuk uji-samar (header samaran + 6 bentuk tanggal)
docker exec synchrono-langflow python /synchrono/infra/buat_data_uji_grading.py

docker exec synchrono-langflow python /synchrono/run_grading_local.py \
    --file-id uji-samar --bucket bucket-test \
    --key uploads/uji-samar/data.parquet --skip-write
```

Keluarannya menunjukkan lapis mana yang mengenali kolom apa:

```
[G2] lapis 1: nik            <- no_identitas         alias 'no_identitas'
[G2] lapis 4: nama_ibu       <- kolom_tiga           cocok kamus master 100% (pesaing terdekat 0%)
[G2] tanggal: 663 baris hanya masuk akal sebagai DD-MM, 176 hanya sebagai MM-DD
```

---

## 10. Yang perlu diketahui

### Penanda `hasAmbiguousDateFormats` punya dua sebab

Menyala kalau (a) tidak satu pun baris berhari di atas 12, sehingga urutannya
tidak terbuktikan, ATAU (b) berkas MENCAMPUR dua urutan — ada baris yang hanya
masuk akal DD-MM dan ada yang hanya masuk akal MM-DD.

Sebab kedua sempat terlewat: berkas uji yang mencampur tujuh bentuk tanggal
punya hari sampai 31, sehingga lolos pemeriksaan pertama dan dilaporkan tidak
ambigu — padahal 78 barisnya memang tidak bisa dipastikan.

### Panggilan AI sesekali gagal, dan itu mengubah grade

Saat menjalankan tujuh berkas beruntun, satu panggilan pernah gagal sendiri.
Berkasnya kehilangan dua elemen dan turun grade — padahal berkas, mode, dan
modelnya sama persis dengan run sebelumnya yang berhasil. Diulang empat kali
sesudahnya, keempatnya berhasil.

Grade yang berubah-ubah antar percobaan jauh lebih merepotkan daripada grade
yang salah secara konsisten, jadi panggilan AI kini dicoba ulang sampai tiga
kali (`NORMALISASI_AI_PERCOBAAN`) dengan jeda menaik. Galat 4xx selain 429
tidak diulang — muatannya yang bermasalah, bukan jaringannya.

Kalau ketiganya tetap gagal, grading TIDAK ikut gagal: kolom yang belum
terpetakan dibiarkan, dan alasannya tercatat di `normalization.trace`.

### Angka kamus di sini optimistis

Data uji dibangkitkan **dari** tabel master, jadi kecocokan 100% pada lapis 4
lebih baik dari yang akan terjadi pada data instansi asli. Untuk wilayah
kosakatanya memang tertutup (38 provinsi ya 38), jadi di situ hasilnya bertahan.
Untuk nama orang, tidak.

### BOM di kolom pertama

CSV dari Excel kerap diawali BOM, sehingga kolom pertama bernama `﻿nik`
dan tidak cocok alias apa pun. Baca dengan `utf-8-sig`. Ini terjadi pada
`data_dukcapil_gradeE.csv`.

### Nilai selain tanggal tidak diubah

Yang dinormalisasi adalah **nama kolom** dan **nilai tanggal**. Jenis kelamin,
nama, dan wilayah disimpan apa adanya — matching tetap menormalkannya sendiri
saat mencocokkan.

### Lapis 4 butuh tabel master

Kalau `master` kosong, lapis 4 dilewati dengan peringatan dan kaskade lanjut ke
AI. Tidak ada kegagalan.
