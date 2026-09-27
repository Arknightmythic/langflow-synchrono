# Matching — sesuai spesifikasi integrasi portal

Pipeline matching yang mengikuti `matching-engine-integration-spec.md` versi
27 Sep 2026: dari payload `matching-dispatch` sampai hasil tersuntik ke DB
portal dan callback terkirim.

Kode: `lib/_matching.py`, `lib/_reasoning.py`, `lib/_matching_worker.py`,
`components/matching/api_dispatch.py`, `infra/buat_flow_matching_dispatch.py`.

---

## 1. Keputusan yang mendasarinya

| Keputusan | Isi | Kapan |
|---|---|---|
| Opsi B | engine **menyuntik langsung ke DB portal** (`syncrono_matching_*`) | lebih awal |
| Jalan 2 | **pass sungguhan per baris**, bukan label dari grade | 27 Sep 2026 |
| Reasoning inline | reasoning = kolom di `result.parquet`, **di dalam** job matching | 27 Sep 2026 |
| DB portal tiruan | uji terhadap `portal_sim` lokal dulu; kredensial DB portal belum diberikan | 27 Sep 2026 |

Spesifikasi (tim portal) dan kode reasoning (`ai_reasoning_parquet_version`)
ditulis orang yang berbeda dan belum saling menyesuaikan. Kalau keduanya
bertentangan, spesifikasi yang dipakai — ia kontrak portal.

---

## 2. Alur

```
POST /api/v1/run/matching-dispatch        node MatchingDispatch-b4819
  -> periksa muatan, balas IN_PROGRESS    (<1 detik)
  -> pekerja latar:
       grade berkas  <- grading_jobs milik engine sendiri
       incoming      <- s3://{bucket}/{incomingFile.s3Key}       (dimaterialkan)
       master        <- s3://{bucket}/{masterDataFile.s3Key}     (VIEW, dialirkan)
       Pass 1  NIK tepercaya + nama PERSIS
       Pass 2  nama + tanggal lahir + nama ibu PERSIS
       Pass 3  blocking & skor milik grade, untuk sisanya
       klasifikasi AUTO/REVIEW/UNMATCH/CONFLICT, pattern_group
       pasangan: tiap keputusan + data incoming + master-nya
       reasoning per baris (§5) — gagal di sini tidak menggagalkan job
       snapshot
       -> s3://{bucket}/matching-results/{jobId}/result.parquet
       -> DB portal, SATU transaksi: DELETE lama, INSERT dari parquet, UPDATE job
       -> callback ke callbackUrl
```

**Grade dicari engine sendiri**, dari `grading_jobs.result.summary.grade`.
Spesifikasi versi 27 Sep tidak menambahkan `grade` ke payload — dan memang tidak
perlu: engine ini yang menghitungnya saat grading. Berkas yang belum pernah
digrading ditolak dengan pesan yang menyebut sebabnya.

**Master dibaca langsung dari S3** lewat `masterDataFile.s3Key` di setiap job,
bukan dari tabel PostgreSQL maupun berkas lokal.

---

## 3. Pass — dan dampaknya, terukur

Setiap baris melewati pass berurutan; yang sudah ketemu tidak lanjut. Pass 3
memakai query grade dari `matching_queries` **tanpa diubah satu huruf pun** —
view `incoming_df` saja yang dibatasi ke baris yang tersisa.

Diukur lewat API sungguhan, lima berkas × 200 ribu baris, master 299 ribu:

| Berkas | AUTO | REVIEW | UNMATCH | CONFLICT | Pass 1 | Pass 2 | Scoring |
|---|---|---|---|---|---|---|---|
| grade A | 199.386 | 0 | 614 | 0 | 199.386 | 0 | 614 |
| grade B | 166.566 | 0 | 33.454 | 0 | 142.634 | 23.932 | 33.454 |
| grade C | 199.403 | 0 | 597 | 0 | 0 | 199.391 | 609 |
| grade D | 115.551 | 24.020 | 60.447 | 2 | 0 | 83.772 | 116.248 |
| grade E | 158.304 | 0 | 41.696 | 0 | 0 | 40.408 | 159.592 |

Dibanding engine lama (per grade), pada data yang sama: **tidak satu baris pun
berpindah ke orang yang berbeda.** Grade A, C, D hasilnya sama — hanya berlabel.
Grade B berubah di dua tempat, dan keduanya dibongkar sampai sebabnya:

- **17.218 REVIEW → AUTO lewat Pass 1.** NIK dan nama persis. Aturan grade 2
  lama menurunkannya karena tempat lahir/nama ibu **kosong**. Dari seluruh baris
  Pass 1 grade B, yang tempat atau ibunya *berbeda* dengan master: **nol**.
- **23.922 UNMATCH → AUTO lewat Pass 2.** 23.921 NIK-nya **tidak ada di master
  sama sekali** (12.729 bahkan bukan 16 digit). Contoh nyata: NIK berkas
  `…8575`, master `…8576` — meleset satu digit, orangnya tetap ditemukan.

Grade E berbeda dari prototipe (158.304 vs 126.828 AUTO) karena prototipe
membaca CSV mentah, yang hanya 80,6% tanggalnya terbaca (`29-10-80`,
`03-18-1991`); enriched hasil grading 100%. Bedanya dari masukan, bukan dari
pass-nya.

### Dua aturan pengaman

1. **Pass 2 menemukan orang X, padahal NIK berkas milik orang lain di master →
   REVIEW + `NIK_CONFLICT`.** Barisnya bisa X (menurut identitas) atau pemilik
   NIK itu — manusia yang memutuskan. Hanya berlaku untuk NIK tepercaya.
2. **Atribut yang terisi di kedua sisi saling membantah → turun ke Pass 3**,
   bukan AUTO. Tanggal lahir dan jenis kelamin dibandingkan persis; nama ibu
   dengan Jaro-Winkler < 0,80. **Tempat lahir sengaja tidak ikut** — "Bogor"
   dan "Kab. Bogor" tempat yang sama.

### CONFLICT

Pass 3 memilih pemenang dengan `arg_min(..., 2)` — dua teratas dalam satu
agregasi, memorinya tetap dua kandidat per baris. Seri = dua **NIK berbeda**
dengan selisih skor ≤ `MATCHING_CONFLICT_EPSILON` (bawaan 0). Baris master ganda
dengan NIK yang sama bukan konflik. Contoh nyata: dua orang bernama *Jasmin
Waskita* lahir di hari yang sama, skor seri 90.

### UNMATCH tidak membawa kandidat

Spesifikasi §4.1: `master_nik` dan `master_snapshot` **"NULL jika UNMATCH"**.
Versi sebelumnya melanggarnya — UNMATCH dari Pass 3 membawa kandidat terdekat
yang justru DITOLAK, dan portal yang menampilkannya akan menunjuk orang yang
salah. Sekarang keduanya NULL; skornya tetap disimpan dan disebut reasoning.
`rank_conflict` juga FALSE untuk UNMATCH: dua kandidat yang seri di bawah ambang
tetap sama-sama ditolak.

Dibuktikan per baris terhadap hasil sebelum perubahan, **1.000.040 baris**
(kelima berkas):

| Kolom | Baris berbeda |
|---|---|
| status, method, score, pattern_group, incoming_snapshot | **0** |
| master_nik, master_snapshot, rank_conflict — selain UNMATCH | **0** |
| UNMATCH yang dulu membawa `master_nik` | 74.845 → 0 (disengaja) |
| UNMATCH yang dulu `rank_conflict = TRUE` | 3.697 → 0 (disengaja) |

---

## 4. `pattern_group`

Hanya untuk REVIEW dan CONFLICT, berurutan menurut prioritas:

| Pola | Syarat |
|---|---|
| `NIK_CONFLICT` | aturan pengaman 1, **atau** NIK cocok tapi nama Jaro-Winkler < 0,70 |
| `TITLE_DEGREE` | nama beda, tapi sama setelah gelar & patronimik dibuang |
| `SWAPPED_DOB` | tanggal beda, tapi hari dan bulannya tertukar |
| `SPELLING_NAME` | nama beda, Jaro-Winkler ≥ 0,85 |
| `GENERAL_REVIEW` | selain itu |

Teruji 7/7 di `tests/test_pola_matching.py`, memakai contoh dari spesifikasi
sendiri — termasuk jebakan: tanggal `05-05` yang hari dan bulannya sama tidak
boleh dituduh `SWAPPED_DOB`.

Pada data uji, 24.020 REVIEW grade D semuanya `GENERAL_REVIEW`: nama dan tanggal
lahir **identik**, tempat lahir dan nama ibu kosong. Sebab sebenarnya ("data
tidak lengkap") bukan salah satu pola di spesifikasi — reasoning yang
menyebutnya (§5).

---

## 5. Reasoning

Kolom `reasoning` diisi untuk **setiap** baris, semua status — §6 spesifikasi
memberi contoh untuk AUTO, REVIEW, CONFLICT, dan UNMATCH. Contoh keluaran
sungguhan dari data uji:

| Status | `reasoning` |
|---|---|
| AUTO, Pass 1 | Cocok otomatis melalui pencocokan deterministik Pass 1: NIK (8107142612951078) dan nama lengkap identik dengan master. Tanggal lahir, jenis kelamin, nama ibu kandung, dan tempat lahir juga identik. |
| AUTO, Pass 2 | Cocok otomatis melalui pencocokan deterministik Pass 2: nama lengkap, tanggal lahir (1970-02-24), dan nama ibu kandung identik dengan master NIK 6301616402708127. NIK berkas (6301616402708123) tidak terdaftar di master. Tempat lahir juga identik. Jenis kelamin kosong pada data incoming. |
| AUTO, Pass 3 | Cocok otomatis melalui pencocokan skor (Pass 3): skor kemiripan 97.67% terhadap master NIK 6404210109939206. Nama lengkap hanya berbeda pada gelar akademis/keagamaan atau bin/binti ('Tirtayasa Jatmiko Usada, S.Ked' vs 'Tirtayasa Jatmiko Usada'). Tanggal lahir, nama ibu kandung, dan tempat lahir identik. |
| REVIEW | Skor kemiripan 90.0% terhadap master NIK 1906316707661883 belum memenuhi syarat pencocokan otomatis. Nama lengkap, tanggal lahir, dan jenis kelamin identik. Nama ibu kandung dan tempat lahir kosong pada data incoming. |
| CONFLICT | Dua kandidat teratas memiliki skor seimbang: Kandidat 1 NIK 1812104102808198 (Jasmin Waskita, 90.0%) dan Kandidat 2 NIK 5207304102801120 (Jasmin Waskita, 90.0%), dengan tanggal lahir sama (1980-02-01). Sistem tidak memilih salah satunya secara otomatis. |
| UNMATCH | Tidak ditemukan catatan kependudukan yang relevan pada Master Data Dukcapil: tidak ada kandidat yang lolos penyaringan awal (blocking). NIK berkas (6402600506763387) tidak terdaftar di master. |

Pola yang tidak muncul di data uji (SPELLING_NAME, TITLE_DEGREE, SWAPPED_DOB,
NIK_CONFLICT) diuji dengan contoh dari §6 spesifikasi di `tests/test_reasoning.py`.

### Algoritma — dari cabang `ai_reasoning_parquet_version`

Kerangkanya dipertahankan, termasuk tabel cache-nya (migrasi 005 disalin apa
adanya):

```
vonis per elemen (SAME / DIFFERENT / EMPTY_IN_INSTITUTION / EMPTY_IN_MASTER)
  -> signature -> kalimat berplaceholder per signature
  -> (opsional) LLM memperhalus; cache `reasoning_patterns`
  -> dirangkai per baris di SQL
```

Kalimat disusun per **signature**, bukan per baris: 200 ribu baris hanya
2–32 signature (grade A 2, B 32, C 6, D 22, E 9).

| Versi asal | Di sini | Kenapa |
|---|---|---|
| job terpisah (`reasoning-dispatch`, `reasoning_jobs`), baca/tulis `manual_matches` | inline di job matching | portal menerima hasil lewat penyuntikan — tidak ada tahap kedua yang bisa mengisi kolomnya belakangan |
| bahasa Inggris | bahasa Indonesia | contoh §6 spesifikasi |
| hanya REVIEW | semua status; signature memuat status, pass, pattern_group | kalimat yang benar bergantung pada ketiganya |
| tanggal dibandingkan sebagai **teks** | sebagai **tanggal** | `'02-02-1992'` dan `'1992-02-02'` divonis DIFFERENT — padahal berkas incoming (dd-mm-yyyy) dan master (DATE) selalu beda format, jadi SETIAP baris akan "beda tanggal lahir" |
| jenis kelamin dinormalisasi | sama, di kedua sisi | `'LAKI-LAKI'` = `'L'` |
| LLM menerima **nilai** (nama, tanggal, nama ibu), lalu nilai di jawabannya dicari untuk ditukar jadi placeholder | LLM hanya menerima **kalimat berplaceholder** | kalau LLM menulis nilainya sedikit berbeda, penukarannya meleset dan nama orang itu tersimpan di templat — lalu tercetak di penjelasan setiap baris lain yang polanya sama |
| jawaban LLM dipakai apa adanya | diperiksa: placeholder utuh, tidak ada angka bertambah/hilang, kata kunci makna (identik, berbeda, kosong, …) tidak berubah | gagal periksa → kalimat deterministik |
| fallback deterministik disimpan ke cache dengan kunci yang sama | kunci = versi + model + kalimat dasar | pola yang sekali gagal tidak terkunci selamanya; perbaikan kalimat tidak tertahan cache lama |
| ikut `NORMALISASI_AI_BASE_URL` / `OLLAMA_LOCAL_BASE_URL` | hanya `REASONING_AI_BASE_URL` | menyalakan AI untuk pengenalan kolom tidak boleh diam-diam menyalakannya untuk reasoning |
| hidrasi REPLACE bersarang | dirangkai `concat` per signature | 3,1 → 0,11 detik untuk 200 ribu baris; nilai kotor berisi teks `{skor}` tidak bisa menyuntik isi |

Tambahan yang tidak ada di versi asal: NIK ikut divonis (tidak tepercaya /
tidak terdaftar / milik orang lain), nama yang hanya beda gelar atau bin/binti,
tanggal yang **tidak terbaca** (disebut beserta nilai mentahnya, bukan
"kosong"), dan elemen yang tidak ada di seluruh berkas tidak disebut (berkas
grade C–E memang tidak boleh memuat NIK).

### LLM — opsional, mati secara bawaan

Tanpa `REASONING_AI_BASE_URL`, seluruh kalimat deterministik dan **tidak ada
yang menyentuh database**. Kalimatnya sudah lengkap tanpa LLM; LLM hanya
memperhalus bahasa.

Kalau diisi:

- hanya endpoint on-prem yang diterima (IP privat, loopback, atau nama layanan
  Docker); `https://ollama.com` ditolak kecuali `REASONING_AI_ALLOW_EXTERNAL=1`
  disetel secara sadar (beserta `REASONING_AI_API_KEY`). Yang dikirim tetap
  hanya kalimat berplaceholder;
- satu panggilan per signature **baru**, mulai dari yang barisnya terbanyak,
  paling banyak `REASONING_AI_MAX_PATTERNS_PER_JOB` (25) dan
  `REASONING_AI_BUDGET_SECONDS` (90) per job — sisanya deterministik di job
  ini dan mendapat giliran di job berikutnya;
- endpoint yang gagal setelah percobaan ulang tidak dicoba lagi di job itu;
- hasil yang lolos periksa **dan** penolakan disimpan ke `reasoning_patterns`
  (DB engine) — penolakan disimpan sebagai kalimat dasarnya, supaya LLM yang
  sama tidak ditanya ulang untuk jawaban yang sama. Kegagalan jaringan tidak
  disimpan.

Menyalakannya di server: isi `REASONING_AI_BASE_URL` (mis.
`http://172.16.12.98:11434`) dan `REASONING_AI_MODEL` di `.env`, lalu buat ulang
container langflow (`up -d langflow` — `restart` tidak membaca `.env` baru).
Tabel `reasoning_patterns` dibuat migrasi 005, yang diterapkan otomatis oleh
service `skema` setiap `up`.

### Biaya, terukur

| | 200 ribu baris |
|---|---|
| tabel `pasangan` | 0,9–1,4 detik |
| reasoning deterministik (vonis, signature, rangkai) | 1,1–1,9 detik |
| penyuntikan ke DB portal, karena teks reasoning (±218 karakter/baris) | +2,3 / +4,0 / +5,7 detik dalam 3 putaran berpasangan (≈ +17%) |

Penyuntikan diukur ke tabel coba berstruktur sama (indeks + FK), dikosongkan
tiap putaran, dengan dan tanpa kolom reasoning bergantian. Lonjakan
`duckdbInjectMs` ke 44–57 detik pada run berulang di laptop ini sebagian besar
**bukan** dari reasoning (yang menambah ≈ 4 detik): tabel `portal_sim` sudah
1,59 GB dengan 596 ribu tuple mati akibat DELETE+INSERT berulang, dan
autovacuum berjalan bersamaan.

### Dengan LLM sungguhan: `gemma4:31b` (ollama.com), dari cache kosong

Lewat Langflow sungguhan, alur portal persis spesifikasi, grade A→E diulang per
putaran sampai satu putaran penuh tanpa panggilan LLM. Batas bawaan 25 pola
per job. "Engine" = total job dikurangi penyuntikan ke DB portal. Angka dari
pengukuran kedua (pemeriksa versi `id-2`). Diukur dua kali: total waktu LLM
putaran pertama hampir sama (68,7 vs 70,0 s), tapi per grade bisa selisih
sampai ±30% karena latensi cloud yang naik-turun.

| Grade | Pola | Putaran 1 (dingin): LLM | reasoning | engine | Putaran 3 (hangat): reasoning | engine |
|---|---|---|---|---|---|---|
| A | 2 | 2 panggilan, 1,5 s | 2,5 s | 8,6 s | 1,0 s | 4,4 s |
| B | 32 | 25 panggilan (batas), 32,8 s | 34,1 s | 39,3 s | 1,9 s | 5,2 s |
| C | 6 | 6 panggilan, 6,6 s | 7,5 s | 12,3 s | 0,9 s | 4,5 s |
| D | 22 | 19 panggilan, 20,7 s | 21,9 s | 27,3 s | 1,7 s | 6,3 s |
| E | 9 | 7 panggilan, 8,4 s | 10,0 s | 14,9 s | 1,6 s | 5,6 s |

- **Cache penuh untuk kelima grade setelah 6 job**: putaran 1, plus B sekali
  lagi di putaran 2 untuk 5 pola yang terpotong batas 25 (7,3 s). Putaran 3:
  nol panggilan.
- **Total 64 panggilan, 75 detik waktu LLM** (±1,2 s per panggilan). Cache
  dipakai lintas grade: B memakai 2 pola milik A, D 3 milik C, E 2.
- Setelah hangat, reasoning 0,9–1,9 s — **setara mode tanpa LLM** (1,1–1,9 s).
- Penyuntikan ke `portal_sim` 28–40 s per job di semua putaran; tidak
  dipengaruhi LLM.
- **62 dari 64 jawaban diterima (97%).** Pengukuran pertama 57/64: 4
  penolakan keliru karena kalimat dasar menulis "tidak sama dengan" (LLM:
  "berbeda dengan") dan pemeriksa menganggap "perbedaan" ≠ "berbeda".
  Diperbaiki di `id-2` — frasa "berbeda dengan", dan kata kunci berupa akar
  "beda". 2 penolakan yang tersisa (angka berubah) lolos saat ditanya ulang:
  di cloud, temperature 0 tidak menjamin jawaban yang sama.

Contoh yang tersuntik ke portal: *"Skor kemiripan 90.0% terhadap master NIK
1225502503613230 belum memenuhi syarat pencocokan otomatis. Nama lengkap,
tanggal lahir, dan jenis kelamin sudah identik. Namun, nama ibu kandung dan
tempat lahir pada data incoming masih kosong."*

---

## 6. Hal yang sengaja berbeda dari contoh di spesifikasi

| Contoh spesifikasi | Di sini | Kenapa |
|---|---|---|
| DELETE, INSERT, UPDATE terpisah | **satu transaksi** | proses mati di antara DELETE dan INSERT meninggalkan portal tanpa hasil. Terbukti: INSERT yang gagal setelah DELETE, 200.000 baris lama tetap utuh |
| nilai ditempel ke SQL (`'{actor}'`) | lewat `q()` | `actor` adalah email dari portal — ditempel mentah berarti injeksi SQL ke DB portal |
| `gen_random_uuid()` | `uuidv7()` | sisipan menumpuk di ujung indeks PK: 21,2 → 15,7 detik untuk 200 ribu baris, selisihnya membesar seiring tabel tumbuh |
| UPDATE lewat DuckDB | SQL PostgreSQL asli (`postgres_execute`) | DuckDB menerjemahkan UPDATE lewat tabel sementara bertipe TEXT, lalu gagal: *column "stage_durations" is of type jsonb but expression is of type character varying*. `postgres_execute` terbukti ikut transaksi |
| `s3Endpoint: http://localhost:8333` | diabaikan | `localhost` dari dalam container menunjuk container itu sendiri |
| node id turunan | dipaksa `MatchingDispatch-b4819` | skema kita menghasilkan `…-c793c`; tweak ke node yang tidak ada **diabaikan Langflow tanpa galat** |
| `stageDurations` | + `reasoningMs` | kunci tambahan; `current_stage` selama reasoning tetap `CLASSIFYING` — nilai baru berisiko ditolak constraint atau tidak dikenali UI portal |

---

## 7. Kecepatan penyuntikan — biayanya di PostgreSQL, bukan di engine

Grade B, 200.020 baris: total ~20 detik, **~16 detik di antaranya penyuntikan**
(sebelum reasoning; reasoning menambah ≈ 17%, lihat §5). Spesifikasi menyebut
"> 100.000 baris per detik"; di sini ~13 ribu.

Diukur untuk memisahkan sebabnya:

| Percobaan | Waktu |
|---|---|
| container → PostgreSQL, tabel **tanpa** indeks/FK | 8,7 s |
| **di dalam** PostgreSQL (tanpa jaringan), ke tabel ber-indeks + FK | 26,7 s |

Biayanya di sisi PostgreSQL: satu FK dan tiga indeks per baris, di PostgreSQL
Windows laptop ini. Di server angkanya bergantung pada **tabel portal
sungguhan** — indeks dan FK apa yang dipasang tim portal. UUIDv7 adalah satu-
satunya bagian dari sisi engine yang terbukti menolong.

---

## 8. Menjalankan simulasi dan uji

```bash
# 1. DB portal tiruan (sekali) — HARUS UTF-8
#    CREATE DATABASE portal_sim ENCODING 'UTF8' TEMPLATE template0;
#    lalu jalankan infra/simulasi_portal/skema.sql di dalamnya

# 2. Flow (sekali, sesudah komponennya termuat)
docker exec synchrono-langflow python /synchrono/infra/buat_flow_matching_dispatch.py

# 3. Penerima callback yang meniru portal
cd infra/simulasi_portal
python penerima_callback.py            # :3999, mencatat ke callback_diterima.jsonl

# 4. Simulasi (terminal lain)
set LANGFLOW_API_KEY=sk-...
uv run --with "psycopg[binary]" python simulasi.py sim-grade-b sim-grade-d

# Uji — tests/ TIDAK di-mount ke container, jadi lewat stdin, dari akar repo
docker exec -i synchrono-langflow python - < tests/test_pola_matching.py
docker exec -i synchrono-langflow python - < tests/test_reasoning.py
```

Berkasnya harus sudah digrading lewat `grading-dispatch` — engine mencari grade-
nya di `grading_jobs`. Perubahan di `lib/` baru termuat setelah container
langflow di-restart.

**Database `synchrono` lokal di laptop ini ber-encoding WIN1252** (bawaan
PostgreSQL Windows). `portal_sim` sengaja dibuat UTF-8; DB portal sungguhan
hampir pasti UTF-8, dan nama berkarakter non-Latin gagal masuk ke WIN1252.

---

## 9. Yang sudah diuji

| Jalur | Hasil |
|---|---|
| lima grade lewat API | semua COMPLETED, angka di §3 |
| dispatch dengan `x-api-key` **dan** `Authorization: Bearer` (persis §3) | diterima |
| penyuntikan ulang berkas yang sama | tetap 200.020 baris dari 1 job — idempoten |
| muatan kurang field | ditolak **seketika**, semua field kurang disebut sekaligus |
| berkas belum digrading | FAILED di tabel portal + callback FAILED |
| job tidak dibuat portal | callback FAILED dengan sebabnya |
| dibatalkan sebelum mulai | berhenti, 0 baris tersuntik, status tetap CANCELLED |
| INSERT gagal setelah DELETE | 200.000 baris lama utuh |
| regresi setelah reasoning, 1.000.040 baris | nol perbedaan selain perubahan UNMATCH yang disengaja (§3) |
| reasoning, pipeline asli pada parquet lokal (Pass 1, 2, 3, CONFLICT, dua jenis UNMATCH) | `tests/test_reasoning.py` bagian 1 |
| reasoning dengan galat buatan | tabel hasil tetap utuh, kolom reasoning kosong (`tests/test_reasoning.py` bagian 5 — diuji di tingkat fungsi, bukan lewat Langflow) |
| LLM tiruan lewat Langflow (grade D & B) | D: 22 pola dikirim, 13 diterima, 9 ditolak periksa; B: 25 dikirim (batas), 7 deterministik; run ulang D: 22/22 dari cache, nol panggilan |
| privasi LLM | **434.764 nilai unik** (nama, NIK, ibu, tempat, tanggal) dari kedua berkas diperiksa terhadap 48 permintaan ke LLM: **nol** yang terkirim |
| LLM sungguhan `gemma4:31b` cloud, kelima grade dari cache kosong, diukur dua kali | 64 panggilan; diterima 57 lalu 62 setelah pemeriksa diperbaiki; cache penuh setelah 6 job; angka di §5 |

LLM tiruan dipakai lebih dulu karena Ollama on-prem (172.16.12.98) tidak
terjangkau dari laptop ini; baris cache buatannya sudah dihapus. Cache hasil
`gemma4:31b` (64 pola) dibiarkan di DB engine lokal.

---

## 10. Temuan — dicatat, TIDAK diubah

1. **Query grade 1 & 2 di DB tidak ikut `matching_queries.json`.** Penjaga
   `nik_trusted` (`ON CASE WHEN i.nik_trusted THEN i.nik END = m.nik`) masuk ke
   JSON pada 23 Sep; DB lokal di-seed 15 Sep dan masih `ON i.nik = m.nik`.
   Seeder sengaja `ON CONFLICT DO NOTHING`, jadi DB yang sudah terisi tidak
   pernah berubah — server lama kemungkinan sama. Akibatnya perilaku Pass 3
   grade 1/2 bisa berbeda antar-server. Terlihat di data uji: 9 baris grade B
   ber-NIK tidak tepercaya tetap mendapat kandidat. Memeriksanya:
   `SELECT grade_code, matching_query LIKE '%nik_trusted%' FROM matching_queries;`
2. **Query grade 4 & 5 memuat `!= '` yang kehilangan satu kutip** (8 dan 2
   tempat, di DB maupun JSON). Literal teksnya menelan dua syarat berikutnya.
   Hasilnya kebetulan tetap sama karena syarat `LEFT(…, 3) = LEFT(…, 3)`
   sesudahnya sudah mencakup keduanya — rapuh, tapi tidak salah hari ini.
3. **Aturan grade 4 punya celah tepat di skor 90.** AUTO butuh kosong ≤ 1,
   REVIEW butuh skor < 90. Baris bernama + tanggal lahir identik dengan dua
   elemen kosong berskor 0,6 + 0,3 = **0,8999999999999999** (DuckDB maupun
   Python) — jadi REVIEW. Seandainya aritmetikanya persis 90, ke-24.022 baris
   itu (24.020 REVIEW + 2 CONFLICT) jatuh ke UNMATCH. Hasil hari ini benar
   karena kebetulan; mengganti ke DECIMAL atau mengubah bobot akan memindahkan
   24 ribu baris.
4. **Reasoning memperlihatkan AUTO yang mencurigakan** dari rumus skor grade
   (warisan engine lama): grade C 12 baris dan grade E 100 baris AUTO yang
   tanggal lahir **dan** nama ibunya berbeda dengan master — mis. 'Laras' vs
   'Laras Hilda Nainggolan', lahir 1967 vs 1989, ibu dan tempat lahir berbeda,
   skor 81,33%.
5. **Jenis kelamin master hanya dikecilkan hurufnya** (`SQL_VIEW_MASTER`),
   sedangkan incoming dipetakan ke `l`/`p`. Master uji memakai `L`/`P`, jadi
   aman. Kalau master sungguhan memakai `LAKI-LAKI`/`PEREMPUAN`: aturan
   pengaman 2 menganggap SETIAP baris bertentangan (Pass 1/2 mati diam-diam),
   dan blocking grade 3/4 yang membandingkan jenis kelamin tidak menemukan
   kandidat sama sekali. Reasoning tidak terpengaruh — ia menormalisasi kedua
   sisi. Periksa nilai `jenis_kelamin` master Dukcapil sebelum produksi.

---

## 11. Yang belum

- **LLM on-prem belum diuji** — baru `gemma4:31b` lewat ollama.com.
- **`rulePreset` diterima tapi belum berpengaruh** — nilainya disebut di
  spesifikasi tanpa definisi. Perlu ditanyakan ke tim portal.
- **Master 100 juta baris** untuk grade 3–5: Pass 3 mewarisi persoalan
  blocking yang sudah diukur sebelumnya (ordo miliaran pasangan). Pass 1 dan 2
  aman — keduanya hash join dengan incoming sebagai sisi build.
- **Tidak ada pemanen job macet di sisi engine.** Kalau Langflow mati di tengah
  job, baris portal tertinggal IN_PROGRESS. Grading punya `panen_mangkrak`;
  matching belum.
- **`blockingMs` selalu 0.** Blocking dan scoring Pass 3 berjalan menyatu
  (kandidat mengalir langsung ke agregasi); memisahkannya berarti menjalankan
  join dua kali. Seluruh waktunya dilaporkan di `scoringMs`.
