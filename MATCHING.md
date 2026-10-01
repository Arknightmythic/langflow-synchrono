# Matching & fuzzy match — cara kerja synchrono-service

Dokumen ini menjelaskan **proses, konfigurasi, dan aturan per grade** yang
benar-benar dijalankan service ini saat portal memanggil `matching-dispatch`.
Semua angka dan aturan diambil dari kode dan seed yang berlaku per 29 Sep 2026.

> **Sejak 30 Sep 2026 angka-angka di §4–§5 adalah NILAI BAWAAN**, bukan
> aturan tetap. Ambang, bobot skor, elemen yang dihitung kosong, dan
> pembersihan nama bisa dilihat dan diubah per grade lewat API — lihat §8 dan
> [KONFIGURASI.md](KONFIGURASI.md). Nilai bawaannya persis aturan di bawah —
> acuan aturannya adalah engine lama (`data-matching`, V2).

| Bagian | Berkas |
|---|---|
| Alur job, Pass 1–3, klasifikasi, pola review, penyuntikan | [`lib/_matching.py`](lib/_matching.py) |
| Normalisasi, fuzzy (Jaro-Winkler), rumus skor & elemen kosong (bawaan), rumus klasifikasi | [`lib/_shared.py`](lib/_shared.py) |
| Konfigurasi: baca, validasi, peringatan, riwayat, versi | [`lib/_config.py`](lib/_config.py) |
| Blocking per grade (kueri SQL) | [`infra/skema/matching_queries.json`](infra/skema/matching_queries.json) → tabel `matching_queries` |
| Ambang, bobot, elemen kosong, pembersihan nama per grade | [`infra/skema/db/seeder/002_grade_rules.sql`](infra/skema/db/seeder/002_grade_rules.sql) → tabel `grade_rules` |
| Pembersih gelar, bin/binti, singkatan "M." | [`lib/_nama.py`](lib/_nama.py) |
| Kalimat `reasoning` | [`lib/_reasoning.py`](lib/_reasoning.py) |

---

## 1. Alur besar

```
portal ──POST matching-dispatch──▶ periksa payload ──▶ balas IN_PROGRESS (seketika)
                                          │
                                          ▼  antre (MATCHING_MAX_CONCURRENT, bawaan 1)
   ┌───────────────────────────── pekerja latar ─────────────────────────────┐
   │ BLOCKING       grade berkas, aturan & kueri grade, muat incoming+master │
   │ DETERMINISTIC  Pass 1: NIK + nama persis                                │
   │                Pass 2: nama + tanggal lahir + nama ibu persis           │
   │ SCORING        Pass 3: blocking + skor Jaro-Winkler, sisa baris saja    │
   │ CLASSIFYING    satu keputusan per baris, pola review, reasoning         │
   │                → matching-results/{jobId}/result.parquet (S3)           │
   │ COMPLETED      suntik ke DB portal (DELETE+INSERT+UPDATE, 1 transaksi)  │
   └───────────────────────────────────────────┬─────────────────────────────┘
                                               ▼
                                     callback ke callbackUrl
```

Nama tahap di kiri adalah nilai `current_stage` yang ditulis ke
`syncrono_matching_job` di DB portal, jadi portal bisa menampilkan kemajuannya.
Portal dicek status `CANCELLED`-nya di antara tahap; kalau dibatalkan, job
berhenti.

**Setiap baris incoming melewati pass secara berurutan, dan baris yang sudah
ketemu tidak lanjut ke pass berikutnya.** Grade berkas tidak memilih "satu
jalur untuk seluruh berkas". Grade hanya menentukan blocking, rumus skor, dan
ambang yang dipakai di Pass 3.

---

## 2. Masukan dan normalisasi

### 2.1 Dari mana grade berkas diambil

Grade dibaca dari hasil grading **engine ini sendiri**: `grading_jobs.result.summary.grade`,
job `COMPLETED` terbaru untuk `fileId` itu. Portal tidak mengirim grade.

- Berkas yang belum pernah digrading ditolak ("jalankan grading lebih dulu").
- Grade 6 (F) ditolak, karena elemennya tidak cukup untuk dicocokkan dengan aman.
- Field `grade` di payload bisa menimpa grade hasil grading. Field ini **hanya
  untuk pengujian** dan tidak ada di spesifikasi.

### 2.2 Normalisasi — sama di sisi incoming dan master

| Elemen | Bentuk yang dibandingkan |
|---|---|
| nama, tempat lahir, nama ibu, wilayah | `lower(trim(nilai))` — **gelar tidak dibuang** (lihat §8) |
| tanggal lahir | incoming dicoba 6 format berurutan: `dd/mm/yyyy`, `dd-mm-yyyy`, `dd mm yyyy`, `dd-Mon-yyyy`, `dd Month yyyy`, `yyyy-mm-dd`; tahun < 1900 → kosong. Master dibaca sebagai DATE |
| jenis kelamin | `laki-laki`, `laki laki`, `pria`, `male`, `l` → **`l`**; `perempuan`, `wanita`, `female`, `p` → **`p`**; lainnya → kosong |
| status hidup | `hidup`, `h` → `h`; `meninggal`, `mati`, `wafat`, `m` → `m` |
| NIK tepercaya | kolom `nik_trusted` dari grading; kalau tidak ada, dianggap tepercaya |

- **Kolom yang tidak ada** di berkas tetap disediakan sebagai kosong (NULL), supaya
  kueri grade mana pun bisa jalan. Grade C/D/E memang tidak punya kolom NIK.
- **`id_incoming`** memakai kolom `id` kalau ada. Kalau tidak ada, dipakai nomor
  baris di berkas. `id` yang tidak unik membuat job gagal.
- **Perbaikan 28 Sep 2026:** jenis kelamin master sebelumnya hanya di-`lower()`.
  Akibatnya `LAKI-LAKI` tidak pernah sama dengan `L`, dan Pass 1/2 serta
  blocking grade C/D tidak bekerja pada master seperti itu.

---

## 3. Tiga pass

### Pass 1 — NIK + nama persis (deterministik)

| | |
|---|---|
| Syarat | NIK incoming **tepercaya** dan sama persis dengan NIK master, **dan** nama (setelah normalisasi) sama persis |
| Ditolak kalau | atribut lain **bertentangan** (aturan pengaman 2, di bawah) |
| Hasil | `AUTO`, skor 100, `method = PASS1_NIK_NAMA` |

Pass 1 secara praktis selalu menghasilkan AUTO, tidak pernah CONFLICT. Semua
kandidatnya ber-NIK sama, jadi jumlah NIK berbeda selalu 1.

### Pass 2 — nama + tanggal lahir + nama ibu persis (deterministik)

Hanya untuk baris yang belum ketemu di Pass 1. Gunanya menemukan orang yang benar
walaupun NIK di berkas rusak, tidak tepercaya, atau tidak ada.

| | |
|---|---|
| Syarat | nama, tanggal lahir, dan nama ibu **ketiganya terisi** dan sama persis dengan master |
| Ditolak kalau | jenis kelamin bertentangan (tanggal dan ibu sudah pasti sama) |
| Hasil | `AUTO` skor 100 · `CONFLICT` kalau cocok dengan **lebih dari satu NIK** di master · `REVIEW` kalau NIK berkas ternyata milik **orang lain** di master (aturan pengaman 1) |
| `method` | `PASS2_NAMA_TGL_IBU` |

**Urutan kandidat CONFLICT.** Kalau beberapa orang di master identik (nama,
tanggal lahir, dan nama ibu sama), engine **tidak** memilih — NIK yang hanya
mirip bukan bukti; manusia yang memutuskan. Tapi portal hanya membandingkan
baris dengan kandidat pertama (`master_nik` / `master_snapshot`), jadi kandidat
diurutkan dari yang paling masuk akal:

1. NIK yang berbeda **≤ 2 digit** dari NIK berkas, di posisi yang sama
   (`BEDA_DIGIT_NIK` di `lib/_matching.py`; panjang NIK harus sama) —
   kemungkinan salah ketik;
2. **kesamaan kata** tempat lahir: bagian kata yang dimiliki keduanya dari
   yang lebih pendek. "PROV. ACEH" vs "ACEH" = 1, vs "JAWA TIMUR" = 0;
3. Jaro-Winkler tempat lahir, untuk salah ketik ("BOGR" vs "BOGOR");
4. NIK terkecil (supaya hasilnya tetap sama setiap dijalankan).

Jaro-Winkler saja tidak dipakai sebagai kunci kedua: ia menilai awalan
"PROV."/"KAB." sebagai beda besar. Pada data uji D, ia menaruh JAWA TIMUR di
atas ACEH untuk berkas bertempat lahir "PROV. ACEH".

Statusnya tetap `CONFLICT`. Hingga **5 kandidat** teratas (`MAKS_KANDIDAT` di
`lib/_shared.py`) disebut di reasoning beserta tempat lahirnya; jumlah
seluruhnya tetap disebut kalau lebih dari 5. Master data uji memuat 43
kelompok berisi 3 orang identik dan 1 kelompok berisi 4 orang.

**Aturan pengaman 1.** Pass 2 bisa menemukan orang X lewat identitasnya,
padahal NIK di berkas terdaftar atas nama orang lain di master. Barisnya bisa X
(menurut identitas) atau pemilik NIK itu (menurut NIK), jadi manusia yang
memutuskan: REVIEW, dengan pola `NIK_CONFLICT`.

**Aturan pengaman 2: atribut bertentangan.** Kecocokan Pass 1/2 dibatalkan
(barisnya turun ke Pass 3) kalau atribut yang **terisi di kedua sisi** saling
membantah:

| Atribut | Dianggap bertentangan kalau |
|---|---|
| tanggal lahir | tanggal berbeda |
| jenis kelamin | `l` vs `p` |
| nama ibu (Pass 1 saja) | Jaro-Winkler < **0,80** (`MATCHING_KONTRA_JW`) |
| tempat lahir | **tidak pernah**: variasinya terlalu besar ("Bogor" vs "Kab. Bogor") |

Atribut yang kosong di salah satu sisi tidak dihitung bertentangan.

### Pass 3 — blocking + skor (fuzzy)

Hanya untuk baris yang belum ketemu di Pass 1/2.

1. **Blocking**: kueri milik grade berkas (§5) memasangkan setiap baris
   incoming dengan kandidat master yang lolos syarat kasar (misalnya 3 huruf awal
   nama sama dan hari/bulan lahir sama). Tujuannya membatasi perbandingan.
2. **Skor**: setiap pasangan diberi skor 0–100 dengan rumus grade itu (§5).
   Baris tanpa kandidat mendapat skor 0.
3. **Pemenang**: kandidat berskor tertinggi (NIK terkecil kalau sama). Semua
   kandidat ber-NIK berbeda yang selisih skornya dari yang tertinggi ≤
   `MATCHING_CONFLICT_EPSILON` (bawaan **0**, artinya hanya seri persis) adalah
   kandidat **seri**. Kalau ada dua atau lebih, baris itu seri. Seri dihitung
   dari 10 skor teratas (NIK ganda di master dihitung sekali), dan hingga 5
   di antaranya disebut di reasoning beserta tempat lahir, tanggal lahir, dan
   skornya. Sebelumnya hanya dua teratas yang dilihat, jadi seri 4 orang
   (ada di data uji C dan D) tercatat sebagai 2.
4. **Klasifikasi**: ambang grade (§4):

| Kondisi | Status |
|---|---|
| tidak ada kandidat, atau di bawah ambang | `UNMATCH`: `master_nik` dan snapshot master **kosong**, skor tetap disimpan |
| seri (dan tidak di bawah ambang) | `CONFLICT`, `rank_conflict = true` |
| lolos ambang AUTO | `AUTO` |
| lolos ambang REVIEW | `REVIEW` |

`method = SCORING` untuk semua baris Pass 3.

---

## 4. Rumus klasifikasi & ambang per grade

Ambang ada di tabel `grade_rules` (DB engine). Rumusnya:

```
AUTO    kalau  missing_count ≤ auto_missing_max      DAN  skor ≥ auto_score_min
REVIEW  kalau  missing_count = review_missing_count  DAN  review_score_min ≤ skor < review_score_max
selain itu UNMATCH
```

(Nilai NULL pada `auto_missing_max` / `review_missing_count` berarti "tidak
dibatasi".) `missing_count` = jumlah elemen yang **kosong di sisi incoming**,
dihitung dari daftar elemen milik grade itu (§5, `matching.missingElements`).

| Grade | `auto_missing_max` | `auto_score_min` | `review_missing_count` | `review_score_min` | `review_score_max` | Artinya di praktik |
|---|---|---|---|---|---|---|
| 1 (A) | 99 | 80,001 | 99 | 0 | 80,001 | AUTO kalau skor ≥ 80,001. **REVIEW tidak pernah terjadi** (butuh tepat 99 elemen kosong) |
| 2 (B) | 1 | 85 | 2 | 80 | 85 | AUTO: kosong ≤ 1 & skor ≥ 85. REVIEW: **tepat 2** kosong & skor 80–85 |
| 3 (C) | 99 | 87 | 99 | 85 | 87 | AUTO kalau skor ≥ 87. **REVIEW tidak pernah terjadi** |
| 4 (D) | 1 | 90 | 2 | 85 | 90 | AUTO: kosong ≤ 1 & skor ≥ 90. REVIEW: **tepat 2** kosong & skor 85–90 |
| 5 (E) | 1 | 81 | 2 | 80 | 81 | AUTO: kosong ≤ 1 & skor ≥ 81. REVIEW: **tepat 2** kosong & skor 80–81 |
| 6 (F) | 1 | 90 | — | 70 | 90 | tidak dipakai: grade 6 ditolak sebelum matching |

Nilai di atas adalah **nilai seed**. Operator bisa mengubahnya per grade lewat
`config-rules-update` (kunci `matching`: `autoMissingMax`, `autoScoreMin`,
`reviewMissingCount`, `reviewScoreMin`, `reviewScoreMax`). Nilai yang sedang
berlaku bisa dibaca lewat `config-rules`.

### 4.1 Maksud "REVIEW: tepat 2 kosong" — dengan contoh

**`missing_count`** = berapa elemen dari daftar grade itu yang **kosong di
baris incoming**. Elemen yang kosong bernilai 0 di rumus skor, jadi skor
tertinggi yang masih mungkin dicapai **turun** sesuai bobot elemen yang hilang.

Baris dengan 2 elemen kosong **tidak pernah bisa AUTO** (`auto_missing_max = 1`).
Aturan REVIEW adalah jalan satu-satunya bagi baris seperti itu, dan pitanya
**berimpit tepat dengan skor tertinggi yang masih mungkin** saat 2 elemen
tertentu kosong:

| Grade | Dihitung kosong | 2 elemen yang kosong | Skor tertinggi yang mungkin | Pita REVIEW | Artinya |
|---|---|---|---|---|---|
| B | nama, tempat, ibu | tempat + ibu | nama × 0,8 = **80** | 80–85 | REVIEW **hanya** kalau nama *persis* sama |
| | | nama + salah satu | ≤ 10 | | tidak mungkin REVIEW |
| D | tanggal, tempat, ibu | tempat + ibu | nama 0,6 + tanggal 0,3 = **90** | 85–90 | REVIEW kalau nama & tanggal hampir persis |
| | | tanggal + salah satu | 65 | | tidak mungkin REVIEW |
| E | nama, tanggal, wilayah, ibu | tanggal + ibu | nama 0,5 + wilayah 0,3 = **80** | 80–81 | REVIEW **hanya** kalau nama & wilayah persis |
| | | kombinasi lain | ≤ 60 | | tidak mungkin REVIEW |
| A, C | (tidak ada) | — | — | `review_missing_count = 99` | kosong selalu 0 → **tidak pernah REVIEW**; hasilnya AUTO atau UNMATCH |

Contoh, dihitung dengan rumus asli service (`sql_skor`, `sql_missing`,
`SQL_KLASIFIKASI`):

| Grade | Baris incoming vs master | Kosong | Skor | Hasil |
|---|---|---|---|---|
| B | lengkap: `siti aminah`/`siti amina`, tempat & ibu sama | 0 | 98,55 | **AUTO** |
| B | lengkap: `endang, s.e.`/`endang`, `bogor`/`kab. bogor`, ibu beda | 0 | 82,33 | **UNMATCH** — tidak AUTO (< 85), dan tidak REVIEW karena kosongnya 0, bukan 2 |
| B | ibu kosong: `dewi anggara, s.e.`/`dewi anggara`, tempat sama (kasus server) | 1 | 84,67 | **UNMATCH** — kosong 1, bukan 2 |
| B | ibu kosong: nama & tempat persis | 1 | 90,00 | **AUTO** |
| B | tempat & ibu kosong: nama persis | **2** | 80,00 | **REVIEW** |
| B | tempat & ibu kosong: `siti aminah`/`siti amina` (beda 1 huruf) | 2 | 78,55 | **UNMATCH** — di bawah 80 |
| D | tempat & ibu kosong: nama & tanggal persis | **2** | 89,99… | **REVIEW** (tidak bisa AUTO karena kosong 2) |
| D | tempat & ibu kosong: tanggal beda 1 hari | **2** | 88,80 | **REVIEW** |
| D | tanggal & ibu kosong: nama & tempat persis | 2 | 65,00 | **UNMATCH** — pita 85–90 tidak terjangkau |
| E | tanggal & ibu kosong: nama & wilayah persis | **2** | 80,00 | **REVIEW** |

Jadi dalam aturan ini, **REVIEW bukan untuk "skor tanggung"**. REVIEW untuk
**"datanya kurang lengkap (2 elemen kosong), tapi yang ada cocok sempurna —
biar manusia yang memastikan"**. Baris yang *lengkap* dengan skor tanggung
(misalnya 82 di grade B) dianggap buktinya saling bertentangan, lalu jadi
UNMATCH. Di server (job 28 Sep) ada 1.057 baris grade B seperti itu.

> **Status: menunggu keputusan pemilik aturan.** Kode lama (`data-matching`)
> memakai rumus yang **sama persis** (`missing_count == review_missing_count`),
> dan pola angkanya di atas menunjukkan `=` itu disengaja. Ini mengoreksi
> dugaan versi awal dokumen ini bahwa maksudnya `≤`. Kalau baris lengkap berskor
> tanggung ingin ikut ditinjau manusia, itu **perubahan aturan**, bukan
> perbaikan bug. Contohnya: `review_missing_count` dikosongkan (NULL = tanpa
> syarat kosong) di `grade_rules`.

---

## 5. Aturan per grade

Ciri berkas per grade berasal dari kriteria grading (`grade_criteria`):
A = NIK wajib, semua elemen lengkap, semua NIK tepercaya · B = NIK wajib,
kelengkapan ≥ 60–70% · C = **tanpa kolom NIK**, 5 elemen lengkap · D = tanpa
NIK, kelengkapan ≥ 60–70% · E = kombinasi elemen minimal (mis. nama + tanggal
lahir + jenis kelamin).

Di Pass 3, tanggal lahir dibandingkan sebagai **teks `yyyy-mm-dd`** dengan
Jaro-Winkler, bukan sebagai tanggal. Selisih 1 hari bernilai ±0,96.

### Grade A (1)

| | |
|---|---|
| **Blocking** | NIK incoming **=** NIK master. Versi di repo (`matching_queries.json`, sejak 23 Sep) hanya memakai NIK **tepercaya**: NIK tidak tepercaya tidak mendapat kandidat. DB server 192.168.2.107 sempat memakai versi lama `ON i.nik = m.nik` (semua NIK) karena seeder tidak menimpa isi yang sudah ada; migrasi 008 menyamakannya — lihat §8 |
| **Skor** | `nama × 1,0` |
| **Elemen kosong dihitung** | — (selalu 0) |
| **Ambang** | AUTO ≥ 80,001 · REVIEW tidak pernah · selain itu UNMATCH |

### Grade B (2)

| | |
|---|---|
| **Blocking** | sama dengan A: NIK incoming = NIK master, hanya NIK tepercaya |
| **Skor** | `nama × 0,8 + tempat_lahir × 0,1 + nama_ibu × 0,1` |
| **Elemen kosong dihitung** | nama, tempat lahir, nama ibu |
| **Ambang** | AUTO: kosong ≤ 1 & ≥ 85 · REVIEW: kosong = 2 & 80–85 |

Contoh nyata dari server: `DEWI ANGGARA, S.E.` (ibu kosong) vs master
`DEWI ANGGARA`, tempat sama → `0,9333 × 0,8 + 1 × 0,1 + 0 × 0,1 = 84,67` →
kosong 1, bukan 2 → **UNMATCH**.

### Grade C (3)

| | |
|---|---|
| **Blocking** | jenis kelamin sama **dan** hari lahir sama **dan** bulan lahir sama **dan** 3 huruf pertama nama sama. Tahun lahir tidak disyaratkan |
| **Skor** | `nama × 0,6 + tempat_lahir × 0,2 + tanggal_lahir × 0,2` |
| **Elemen kosong dihitung** | — (selalu 0) |
| **Ambang** | AUTO ≥ 87 · REVIEW tidak pernah · selain itu UNMATCH |

### Grade D (4)

**Blocking** menggabungkan empat cabang. Kandidat diambil dari cabang mana pun
yang cocok, dan duplikat dibuang:

| Cabang | Berlaku untuk baris yang | Syarat terhadap master |
|---|---|---|
| jenis kelamin kosong | tidak punya jenis kelamin | 3 huruf awal **tempat** lahir sama, hari & bulan lahir sama |
| tempat kosong | tidak punya tempat lahir | jenis kelamin sama, 3 huruf awal **nama** sama, hari & bulan lahir sama |
| tanggal kosong | tidak punya tanggal lahir | jenis kelamin sama, 3 huruf awal tempat **dan** nama sama |
| lengkap | semua baris | jenis kelamin sama, 3 huruf awal nama sama, hari & bulan lahir sama |

| | |
|---|---|
| **Skor** | `nama × 0,6 + tanggal_lahir × 0,3 + tempat_lahir × 0,05 + nama_ibu × 0,05` |
| **Elemen kosong dihitung** | tanggal lahir, tempat lahir, nama ibu |
| **Ambang** | AUTO: kosong ≤ 1 & ≥ 90 · REVIEW: kosong = 2 & 85–90 |

### Grade E (5)

| | |
|---|---|
| **Blocking** | status hidup sama (atau incoming tidak punya status hidup) **dan** 3 huruf pertama nama sama **dan** hari & bulan lahir sama (nama & tanggal wajib terisi). Versi sistem lama mensyaratkan status hidup sama TANPA pengecualian — berkas E tanpa kolom status hidup tidak pernah mendapat kandidat (terukur: AUTO 30.137 vs 47.818 pada 52.493 baris). Server 192.168.2.107 sempat memakai versi itu; migrasi 008 menyamakannya |
| **Skor** | `nama × 0,5 + rata-rata(wilayah) × 0,3 + nama_ibu × 0,1 + tanggal_lahir × 0,1` |
| **Rata-rata wilayah** | rata-rata Jaro-Winkler provinsi, kabupaten, kecamatan, kelurahan, **hanya yang terisi di kedua sisi**; tidak ada sama sekali → 0 |
| **Elemen kosong dihitung** | nama, tanggal lahir, wilayah (kosong hanya kalau **keempatnya** kosong), nama ibu |
| **Ambang** | AUTO: kosong ≤ 1 & ≥ 81 · REVIEW: kosong = 2 & 80–81 |

### Ringkasan bobot

| Elemen | A | B | C | D | E |
|---|---|---|---|---|---|
| nama | 1,0 | 0,8 | 0,6 | 0,6 | 0,5 |
| tempat lahir | | 0,1 | 0,2 | 0,05 | |
| tanggal lahir | | | 0,2 | 0,3 | 0,1 |
| nama ibu | | 0,1 | | 0,05 | 0,1 |
| wilayah (rata-rata 4 tingkat) | | | | | 0,3 |

---

## 6. Fuzzy match — Jaro-Winkler

Kemiripan teks memakai `jaro_winkler_similarity()` bawaan DuckDB (hasilnya
identik dengan rapidfuzz), dibungkus macro `j(a, b)`: **0 kalau salah satu
sisi kosong atau NULL**. Nilainya 0–1, lalu dikali bobot dan 100.

Contoh nilai (dihitung dengan fungsi yang sama):

| a | b | Jaro-Winkler |
|---|---|---|
| `siti aminah` | `siti aminah` | 1,0000 |
| `siti aminah` | `siti amina` | 0,9818 |
| `muhammad rizki` | `muhamad rizky` | 0,9560 |
| `endang, s.e.` | `endang` | 0,9000 |
| `maulana bin ahmad` | `maulana` | 0,8824 |
| `budi santoso` | `santoso budi` | 0,6111 |
| `bogor` | `kab. bogor` | 0,5333 |
| `1990-01-01` | `1990-01-02` | 0,9600 |
| `1990-01-12` | `1990-12-01` (hari/bulan tertukar) | 0,9600 |

Jaro-Winkler memberi bobot lebih pada awalan yang sama, jadi urutan kata yang
terbalik (`budi santoso` vs `santoso budi`) dihukum berat. Gelar dan "bin/binti"
menurunkan nilai (lihat §8).

---

## 7. Pola review, reasoning, dan keluaran

### Pola review (`pattern_group`)

Hanya diisi untuk REVIEW dan CONFLICT. Urutannya prioritas, dan pola pertama
yang cocok yang dipakai:

| Pola | Kondisi |
|---|---|
| `NIK_CONFLICT` | NIK berkas milik orang lain (aturan pengaman 1), **atau** NIK sama tapi nama Jaro-Winkler < 0,70 |
| `TITLE_DEGREE` | nama berbeda, tapi sama setelah gelar depan/belakang dan bin/binti dibuang |
| `SWAPPED_DOB` | tanggal lahir berbeda, tapi hari dan bulannya tertukar |
| `SPELLING_NAME` | nama berbeda, Jaro-Winkler ≥ 0,85 |
| `GENERAL_REVIEW` | selain itu |

### Reasoning

Setiap baris mendapat kalimat penjelasan (`reasoning`). Kalimat dasarnya
disusun deterministik dari perbandingan per elemen. Kalau
`REASONING_AI_BASE_URL` diisi, LLM hanya memperhalus bahasanya: satu panggilan
per **pola** baru, bukan per baris, dan hasilnya disimpan di cache
`reasoning_patterns`. LLM tidak pernah menerima nilai data. Kegagalan reasoning
hanya mengosongkan kolom itu; matching tetap selesai.

Untuk `CONFLICT`, reasoning menyebut setiap kandidat (hingga 5), karena kartu
portal hanya membandingkan baris dengan kandidat pertama. Contoh (data uji C):

> Ditemukan 3 kandidat master dengan nama lengkap, tanggal lahir, dan nama ibu
> kandung identik: Kandidat 1 NIK 7468144311051845 (SITI, tempat lahir SULAWESI
> TENGGARA), Kandidat 2 NIK 3326084311057613 (SITI, tempat lahir JAWA TENGAH),
> dan Kandidat 3 NIK 3584124311051346 (SITI, tempat lahir JAWA TIMUR). Sistem
> tidak memilih salah satunya secara otomatis.

CONFLICT dari Pass 3 menambahkan tanggal lahir dan skor tiap kandidat.

### Keluaran

1. `result.parquet` (18 kolom, spesifikasi §4.1) di
   `s3://{bucket}/matching-results/{jobId}/result.parquet`. Jumlah barisnya
   **harus** sama dengan jumlah baris incoming; kalau tidak, job gagal.
2. Penyuntikan ke DB portal dalam **satu transaksi**: DELETE hasil lama untuk
   pasangan berkas × master yang sama, INSERT dari parquet, lalu UPDATE baris job
   (hitungan per status/pass, durasi per tahap, puncak memori). Karena itu
   matching ulang **mengganti** hasil lama, termasuk keputusan review operator.
3. Callback ke `callbackUrl`, diulang sampai `GRADING_CALLBACK_RETRIES` kali. 4xx
   selain 429 tidak diulang. Callback yang gagal **tidak** menggagalkan job,
   karena hasilnya sudah ada di tabel portal.

---

## 8. Konfigurasi

Rincian API dan contoh muatan: [KONFIGURASI.md](KONFIGURASI.md).

### Di basis data engine

| Tabel / kolom | Isi | Diubah lewat |
|---|---|---|
| `grade_rules` (5 kolom ambang) | ambang AUTO/REVIEW per grade (§4) | API `config-rules-update` / `PATCH /api/v1/config/rules/{grade}` |
| `grade_rules.bobot` | bobot skor per elemen, persen, **berurutan** (§5) | API — `matching.weights` |
| `grade_rules.elemen_kosong` | elemen yang dihitung `missing_count` (§4.1) | API — `matching.missingElements` |
| `grade_rules.bersih_nama` | pembersihan nama: gelar, bin/binti, singkatan "M." | API — `matching.nameCleaning` |
| `grade_rules.cocok_tanggal` | cara menilai tanggal lahir: `similarity` (bawaan) / `exact` | API — `matching.dateMatch` |
| `engine_config` | selisih seri (`matching.conflictEpsilon`), ambang nama ibu bertentangan (`matching.contradictionJw`) | API — `PATCH /api/v1/config/global` |
| `matching_queries` | kueri blocking per grade (§5). Sejak migrasi 007 setiap kueri membawa kolom keenam elemen (hanya daftar SELECT yang bertambah; syarat JOIN tetap), jadi elemen apa pun bisa diberi bobot | hanya DB/seed. API **menampilkannya** (`matching.blocking`, `matching.availableElements`), tidak mengubahnya |
| `config_riwayat`, `config_versi` | jejak perubahan & versi konfigurasi per job | otomatis |
| `reasoning_patterns` | cache kalimat reasoning | otomatis |

Setiap perubahan divalidasi (bobot harus berjumlah 100, elemen harus
dikenal, ambang 0–100) dan diberi **peringatan** kalau akibatnya mungkin tak
disangka — mis. pita REVIEW yang mustahil tercapai (seperti §4.1, dihitung
otomatis). Job membaca aturan **sekali di awal** dan mencatat versinya di
`blocking_metrics` job portal serta di callback (`metrics.configVersion`,
`metrics.rulesApplied`).

**Yang berlaku adalah isi tabel di DB, bukan berkas seed.** Seeder memakai
`ON CONFLICT DO NOTHING`, jadi perubahan di `matching_queries.json` tidak
pernah menimpa DB yang sudah terisi. Terbaca 1 Okt: `matching_queries` di DB
server 192.168.2.107 masih versi sistem lama — tanpa penjaga `nik_trusted` di
grade A/B, dan grade E mensyaratkan status hidup sama. Migrasi
`008_samakan_kueri_blocking` menyamakan keduanya dengan repo. Memeriksanya:
`SELECT grade_code, matching_query LIKE '%nik_trusted%', matching_query LIKE '%status_hidup_clean IS NULL%' FROM matching_queries;`

### Environment

| Variabel | Bawaan | Gunanya |
|---|---|---|
| `PORTAL_PG_DSN` | — (wajib) | DB portal tujuan penyuntikan. Kosong = setiap job gagal dengan pesan jelas |
| `MATCHING_MAX_CONCURRENT` | 1 | job matching yang boleh jalan bersamaan |
| `MATCHING_CONFLICT_EPSILON` | 0 | selisih skor 2 kandidat teratas yang masih dianggap seri. **Bawaan saja**: setelan `matching.conflictEpsilon` lewat API mengalahkannya |
| `MATCHING_KONTRA_JW` | 0,80 | di bawah ini nama ibu dianggap bertentangan (aturan pengaman 2). **Bawaan saja**, seperti di atas (`matching.contradictionJw`) |
| `GRADING_CALLBACK_RETRIES` / `_TIMEOUT` | 3 / 20 dtk | percobaan dan batas waktu callback (dipakai grading & matching) |
| `DUCKDB_MEMORY_LIMIT`, `DUCKDB_TEMP_DIR` | — | batas memori DuckDB & tempat tumpahan |
| `REASONING_AI_*` | kosong | LLM untuk memperhalus reasoning (opsional) |

Ambang pola review (0,70 dan 0,85) adalah konstanta di kode.

### Field payload portal

| Field | Diperlakukan |
|---|---|
| `jobId`, `fileId`, `masterFileId`, `actor`, `callbackUrl`, `s3Bucket`, `incomingFile.s3Key`, `masterDataFile.s3Key` | wajib; yang kurang dilaporkan sekaligus |
| `s3Endpoint` | dipakai kalau layak; `localhost`/`127.0.0.1` diabaikan (dari dalam container tidak bermakna) |
| `rulePreset` (FAST/BALANCED/…) | dicatat di log, **belum berpengaruh**: spesifikasi tidak mendefinisikan artinya |
| `customRuleIds` (mis. `rule_nik`, `rule_dob_gender_initial`) | **tidak dipakai**. Blocking selalu mengikuti grade berkas |
| `grade` | di luar spesifikasi, hanya untuk pengujian: menimpa grade hasil grading |

---

## 9. Catatan dan hal yang perlu diketahui

| | Keadaan sekarang |
|---|---|
| **REVIEW memakai `=`** (bawaan) | REVIEW hanya untuk baris dengan tepat 2 elemen kosong yang sisanya cocok (hampir) sempurna; baris lengkap berskor tanggung jadi UNMATCH; grade A & C tidak pernah REVIEW (§4.1). Sama persis dengan kode lama, dan **dipertahankan**: acuan aturan adalah engine lama (1 Okt 2026) |
| **Kueri blocking server ≠ repo** (diperbaiki 1 Okt 2026) | grade A/B server memakai semua NIK dan grade E mensyaratkan status hidup sama, seperti sistem lama; migrasi 008 menyamakannya dengan repo (§8) |
| **Tanggal master harus valid** | tanggal master dibaca dengan `CAST(... AS DATE)`. Kalau kolomnya teks dan ada satu nilai tidak valid/kosong (`''`, `31-01-1990`), **seluruh job gagal**. Kode lama mengosongkan nilai itu saja. Master server saat ini aman (job 2 juta baris sukses) |
| **Gelar tidak dibuang sebelum skor** (bawaan) | nama dibandingkan setelah `lower`/`trim` saja. `ENDANG, S.E.` vs `ENDANG` = 0,90, bukan 1,0. Sejak 30 Sep bisa dinyalakan per grade: `matching.nameCleaning` (gelar, bin/binti, singkatan "M.") — belum dinyalakan, menunggu keputusan |
| **CONFLICT di Pass 3 hanya untuk seri persis** (bawaan) | selisih seri 0. Spesifikasi menyebut "seri/sangat dekat" tanpa angka; bisa diatur lewat `matching.conflictEpsilon` |
| **`customRuleIds` & `rulePreset` diabaikan** | blocking ditentukan grade, bukan pilihan aturan dari portal |
| **Kueri grade D & E memuat `!= '`** | di beberapa tempat tertulis `!= '` (satu tanda petik), sehingga dua syarat "master tidak kosong" ikut terbaca sebagai literal teks. Dampaknya praktis nihil, karena syarat `LEFT(..., 3) = LEFT(..., 3)` sesudahnya sudah menolak nilai kosong. Kueri disalin apa adanya dari sistem lama |
| **Tanggal dibandingkan sebagai teks** (bawaan) | Jaro-Winkler atas `yyyy-mm-dd`: selisih 1 hari ≈ 0,96, tertukar hari/bulan juga ≈ 0,96, beda tahun ≈ 0,9. Diukur dengan data uji ber-kunci jawaban (`test-data-csv/uji-master-ae`): sumber utama AUTO salah orang di grade C/D/E — orang lain bernama sama, hari & bulan lahir sama (syarat blocking), tahun berbeda. Sejak 1 Okt 2026 bisa diganti lewat `matching.dateMatch: "exact"` (sama persis = 1, selain itu 0): ketepatan AUTO C 82,6% → 92,2%, D 88,1% → 96,5%, E 88,4% → 95,7% |
| **NIK kembar di master → snapshot bisa orang yang salah** | ditemukan 30 Sep 2026. Snapshot & reasoning mengambil baris master dengan `DISTINCT ON (nik)` tanpa urutan; kalau satu NIK tercatat untuk dua orang, baris yang terpilih acak antar-jalan. Status, NIK, dan skornya benar — yang bisa keliru hanya data master yang ditampilkan dan kalimat reasoning-nya (contoh: NIK `3580220211083231` = RIZKI PRATAMA dan AHMAD SANTOSO di master server; kode lama 4× dijalankan: 2× masing-masing). Belum diperbaiki |
| **Diperbaiki 28 Sep 2026** | normalisasi jenis kelamin master (§2.2). Pada master `LAKI-LAKI`/`PEREMPUAN`: grade B Pass 1/2 dari 0/0 menjadi 41.597/1.882; grade C/D dari 0 kandidat (semua UNMATCH) menjadi ±45 ribu AUTO dari 52.493 baris |

Salinan logika yang sama ada di `langflow-synchrono` (`lib/`, kueri, seed).
Perbedaannya bisa dicek dengan `python infra/cek_salinan.py`.

---

## 10. Perbandingan dengan logika lama (`data-matching`)

Pembanding: `data-matching/processing/matching_service_new.py`
(`MatchingServiceV2`, yang dipanggil `handler.py` untuk grade 1–6),
`string_similarity.py` (`ScoringService`), `minio_fetching_service.py` dan
`repository.py` (normalisasi incoming & master), dan `custom_query_builder.py`
(grade 6).

### Yang SAMA PERSIS

| Bagian | Keterangan |
|---|---|
| Bobot & rumus skor grade 1–5 | `compute_similarity_score` lama = `sql_skor` service, termasuk rata-rata wilayah grade E |
| Fungsi kemiripan | `safe_jaro` (rapidfuzz Jaro-Winkler, 0 kalau kosong) = macro `j()` (Jaro-Winkler DuckDB, hasil identik) |
| Elemen kosong per grade | `count_missing_attributes` lama = `sql_missing` service |
| **Rumus klasifikasi** | `classify_result` lama = `SQL_KLASIFIKASI`, **termasuk `missing_count == review_missing_count`** (§4.1) |
| Ambang per grade | nilai `grade_rules` disalin dari sistem yang berjalan |
| Blocking grade C, D, E | kueri disalin apa adanya (termasuk `!= '` yang janggal) |
| Normalisasi incoming | `lower`/`strip`, 6 format tanggal (tahun < 1900 dibuang), jenis kelamin → `l`/`p`, status hidup → `h`/`m`. **Gelar tidak dibuang** di keduanya |
| Tanggal di skor | keduanya membandingkan teks `yyyy-mm-dd` dengan Jaro-Winkler (`safe_jaro` mengubah tanggal jadi teks). Cocok-persis 1/0 + re-weighting hanya ada di V1 (`matching_service.py`, grade D/E), yang **tidak dipakai** — `handler.py` mengarahkan grade 1–5 ke V2 |

### Yang BERBEDA

| Aspek | Lama (`data-matching`) | synchrono-service | Dampak |
|---|---|---|---|
| **Alur** | satu jalur: blocking + skor per grade untuk semua baris | **3 pass**: Pass 1 NIK + nama persis, Pass 2 nama + tanggal + ibu persis, baru Pass 3 = logika lama | baris yang cocok persis tidak lagi bergantung ambang skor. Pass 2 menemukan orang yang benar walau NIK rusak (terukur: 1.879 baris grade B yang dulu UNMATCH jadi AUTO) |
| **Aturan pengaman** | tidak ada | (1) NIK milik orang lain → REVIEW `NIK_CONFLICT`; (2) atribut terisi yang bertentangan membatalkan Pass 1/2 | kecocokan persis yang mencurigakan tidak langsung AUTO |
| **Seri / CONFLICT** | tidak ada status CONFLICT; saat skor seri, kandidat yang **lebih dulu** muncul di join yang menang (bergantung urutan) | 2 kandidat teratas; seri antara 2 NIK berbeda → **CONFLICT** + `rank_conflict` | tidak ada lagi pemenang "kebetulan" |
| **Status** | angka 1/2/3 (cocok/review/tidak cocok) di tabel `institution`, baris review juga ke `manual_review` | `AUTO/REVIEW/UNMATCH/CONFLICT` + `method` + `pattern_group` + snapshot incoming & master, di `syncrono_matching_result` portal | portal bisa menampilkan cara & alasan tiap keputusan |
| **UNMATCH** | tetap menyimpan `nik_master` kandidat terbaik (kecuali tanpa kandidat) | `master_nik` & snapshot master **kosong** (spesifikasi §4.1); skornya tetap disimpan | portal tidak menunjuk orang yang justru ditolak |
| **Blocking grade A/B** | `ON i.nik = m.nik` (semua NIK) | hanya NIK **tepercaya** (migrasi 008 untuk DB lama) | pada data uji server: hasil identik |
| **Blocking grade E** | status hidup harus sama — berkas tanpa kolom status hidup tidak dapat kandidat | status hidup hanya disyaratkan kalau berkasnya memuatnya | AUTO 30.137 → 47.818 pada data uji |
| **Jenis kelamin master** | hanya `lower()` — **cacat yang sama** | dinormalisasi ke `l`/`p` sejak 28 Sep 2026 | dengan master `LAKI-LAKI`/`PEREMPUAN`, engine lama pun tidak menemukan kandidat di grade C/D (blocking lewat jenis kelamin) |
| **Tanggal master tidak valid** | `strptime` longgar → nilai itu kosong | `CAST AS DATE` → **job gagal** | service lebih rapuh terhadap master kotor (§9) |
| **Grade 6 (F)** | didukung lewat **custom mapping**: pengguna memasangkan kolom sendiri + bobot, blocking disusun dinamis (NIK, atau jenis kelamin / 3 huruf nama / hari-bulan lahir) | **ditolak** ("elemen tidak cukup") | fitur grade 6 belum ada di service |
| **Sumber grade** | `uploaded_files.grade` (StarRocks) | `grading_jobs` engine ini sendiri | — |
| **Master** | seluruh tabel `master` StarRocks ditarik ke memori (Polars) | parquet master dari S3 dibaca mengalir (view), tidak dimuat utuh | memori sebanding incoming, bukan master |
| **Eksekusi** | incoming dipotong per 1 juta baris; skor dihitung **baris per baris di Python** | seluruhnya SQL DuckDB set-based | jauh lebih cepat (grade D 52 ribu × master 2 juta ≈ 70 dtk termasuk semua tahap) |
| **Reasoning** | tugas Celery terpisah **setelah** matching | disusun di dalam job (deterministik + LLM opsional per pola, di-cache) | hasil & alasannya tersedia bersamaan |
| **Penulisan hasil** | INSERT ke StarRocks + `sync_status`; ekspor CSV otomatis kalau tidak ada review | DELETE + INSERT + UPDATE ke DB portal dalam **satu transaksi**, lalu callback | tidak ada keadaan setengah jadi |
| **`customRuleIds` / `rulePreset`** | tidak dikenal (payload portal ini baru) | diterima, tidak dipakai | — |

Singkatnya: **cara menghitung skor dan menggolongkan (Pass 3) sama dengan
sistem lama**; yang ditambahkan service adalah dua pass deterministik di
depannya, penanganan seri (CONFLICT), dan aturan pengaman. Grade 6 (custom
mapping) belum dibawa.

---

## 11. Status hasil & keputusan operator: lama vs baru

### 11.1 Lama (`data-matching`): satu kolom `match_result`, lima nilai

Tabel rujukan `ref_match_results`; nilainya disimpan di `institution.match_result`:

| Kode | Nama | Siapa yang mengisi | Kapan |
|---|---|---|---|
| 1 | `AUTO_MATCH` | engine | `classify_result` = 1 (lolos ambang AUTO) |
| 2 | `MANUAL_REVIEW` | engine | `classify_result` = 2 (lolos ambang REVIEW). Baris juga dicatat di `manual_matches`, lalu diberi alasan AI oleh tugas Celery |
| 3 | `AUTO_UNMATCH` | engine | `classify_result` = 3, atau tanpa kandidat. **`nik_master` kandidat terbaik tetap disimpan** |
| 4 | `MANUAL_MATCH` | **operator** ("Match") | hanya dari baris berstatus **2**: `UPDATE ... SET match_result = 4 WHERE match_result = 2` |
| 5 | `MANUAL_UNMATCH` | **operator** ("Unmatch"), **atau otomatis** | dari baris berstatus 2. Saat berkas ditandai **"Mark as Completed"**, **semua** baris 2 yang tersisa diubah menjadi 5 sekaligus |

Siklus hidup satu baris:

```
engine ──▶ 1 AUTO_MATCH ─────────────────────────────▶ ekspor "match"
       ──▶ 3 AUTO_UNMATCH ───────────────────────────▶ ekspor "unmatch"
       ──▶ 2 MANUAL_REVIEW ──operator "Match"────────▶ 4 MANUAL_MATCH   ─▶ ekspor "match"
                          ──operator "Unmatch"──────▶ 5 MANUAL_UNMATCH ─▶ ekspor "unmatch"
                          ──"Mark as Completed"─────▶ 5 MANUAL_UNMATCH (sisa yang belum diputuskan)
```

- **Ekspor CSV:** "match" = status **1 + 4**, "unmatch" = status **3 + 5**. Baris
  yang masih 2 tidak ikut diekspor sampai berkasnya ditandai selesai.
- **Keputusan operator menimpa** keputusan engine di kolom yang sama. Status
  asli engine hanya tersisa di log audit (`before_state`).
- **Keputusan bersifat final.** Baris 4/5 tidak bisa diubah lagi, karena
  `UPDATE` hanya berlaku untuk `match_result = 2`.
- **"Match" selalu mengonfirmasi `nik_master` pilihan engine.** Operator tidak
  memilih orang lain.
- **Baris 1 dan 3 tidak bisa diputuskan ulang** oleh operator.

### 11.2 Baru (synchrono-service + portal): dua kolom terpisah

| Kolom (`syncrono_matching_result`) | Diisi oleh | Nilai |
|---|---|---|
| `status` | **engine**, tidak pernah ditimpa operator | `AUTO`, `REVIEW`, `UNMATCH`, `CONFLICT` |
| `review_decision` | **portal**, saat operator memutuskan; engine tidak pernah menulisnya | `NULL` (belum), `MATCH`, `NOT_MATCH` (spesifikasi §4.1) |
| `reviewed_at`, `reviewed_by` | portal | kapan & siapa |
| `method`, `pattern_group`, `reasoning`, snapshot | engine | cara ditemukan, pola review, alasan, data kedua sisi |

Terbaca di DB portal (29 Sep): `AUTO` 44.729, `REVIEW` 6.856 (1 di antaranya
`NOT_MATCH`), `UNMATCH` 400.927. Keputusan operator sudah dipakai.

### 11.3 Padanan lama → baru

| Lama | Baru |
|---|---|
| 1 `AUTO_MATCH` | `status = AUTO` |
| 2 `MANUAL_REVIEW` (belum diputuskan) | `status = REVIEW` (atau `CONFLICT`), `review_decision = NULL` |
| 3 `AUTO_UNMATCH` | `status = UNMATCH` |
| 4 `MANUAL_MATCH` | `status = REVIEW`/`CONFLICT` **dan** `review_decision = MATCH` |
| 5 `MANUAL_UNMATCH` | `status = REVIEW`/`CONFLICT` **dan** `review_decision = NOT_MATCH` |
| — | `CONFLICT` **baru**: dua kandidat berbeda sama kuat. Lama tidak punya; kandidat yang muncul lebih dulu langsung menang |

Kalau portal perlu angka "lima status" seperti dulu, bisa diturunkan dari dua
kolom ini: "match" = `AUTO` + `MATCH`, "unmatch" = `UNMATCH` + `NOT_MATCH`,
"menunggu" = `REVIEW`/`CONFLICT` yang `review_decision`-nya masih NULL.

### 11.4 Yang berbeda dan perlu diketahui

| | Lama | Baru |
|---|---|---|
| **Jejak keputusan engine** | hilang saat operator memutuskan (ditimpa) | tetap utuh di `status`; keputusan operator di kolom sendiri |
| **Sisa review saat berkas diselesaikan** | "Mark as Completed" → semua otomatis `MANUAL_UNMATCH` | **engine tidak mengatur ini**. Kalau ada perilaku serupa, itu ada di portal; spesifikasi tidak menyebutnya |
| **Matching ulang** | baris baru di-INSERT | hasil lama untuk pasangan berkas × master yang sama **dihapus lalu diganti** (spesifikasi §5.1). **Keputusan operator (`review_decision`) ikut hilang** |
| **Kandidat pada CONFLICT** | — | `master_nik` & snapshot hanya untuk kandidat **pertama** (NIK terkecil di antara yang seri). Kandidat kedua hanya disebut di teks `reasoning`, tidak punya kolom sendiri. "MATCH" pada CONFLICT berarti mengonfirmasi kandidat pertama |
| **Kandidat pada UNMATCH** | `nik_master` terbaik tetap disimpan | kosong (spesifikasi §4.1). Skornya tetap ada |
| **Baris yang bisa diputuskan operator** | hanya status 2 | ditentukan portal. Spesifikasi menyebut *Review Workbench* untuk `REVIEW` dan `CONFLICT` |
| **Tabel `ref_match_results`** | dipakai (nama status) | masih ada di seed engine (5 nilai lama), tapi **tidak dipakai kode service**. Portal tidak punya tabel ini |

> **Catatan spesifikasi §8.** Tabel ringkasan status di spesifikasi portal
> menyebut "REVIEW: 60 ≤ skor < 85, UNMATCH: skor < 60, AUTO: skor ≥ 85".
> Engine (lama maupun baru) **tidak** memakai angka tetap itu. Ambangnya per
> grade dari `grade_rules`, dengan syarat elemen kosong (§4, §4.1). Angka di
> spesifikasi sebaiknya dibaca sebagai gambaran, bukan aturan.
