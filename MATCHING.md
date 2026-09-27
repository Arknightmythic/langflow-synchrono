# Matching — sesuai spesifikasi integrasi portal

Pipeline matching yang mengikuti `matching-engine-integration-spec.md` versi
27 Sep 2026: dari payload `matching-dispatch` sampai hasil tersuntik ke DB
portal dan callback terkirim.

Kode: `lib/_matching.py`, `lib/_matching_worker.py`,
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
       klasifikasi AUTO/REVIEW/UNMATCH/CONFLICT, pattern_group, snapshot
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
dengan NIK yang sama bukan konflik. Contoh nyata: dua orang bernama *Luwes
Januar* lahir di hari yang sama, skor seri 90.

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
tidak lengkap") bukan salah satu pola di spesifikasi — itu tugas `reasoning`.

---

## 5. Hal yang sengaja berbeda dari contoh di spesifikasi

| Contoh spesifikasi | Di sini | Kenapa |
|---|---|---|
| DELETE, INSERT, UPDATE terpisah | **satu transaksi** | proses mati di antara DELETE dan INSERT meninggalkan portal tanpa hasil. Terbukti: INSERT yang gagal setelah DELETE, 200.000 baris lama tetap utuh |
| nilai ditempel ke SQL (`'{actor}'`) | lewat `q()` | `actor` adalah email dari portal — ditempel mentah berarti injeksi SQL ke DB portal |
| `gen_random_uuid()` | `uuidv7()` | sisipan menumpuk di ujung indeks PK: 21,2 → 15,7 detik untuk 200 ribu baris, selisihnya membesar seiring tabel tumbuh |
| UPDATE lewat DuckDB | SQL PostgreSQL asli (`postgres_execute`) | DuckDB menerjemahkan UPDATE lewat tabel sementara bertipe TEXT, lalu gagal: *column "stage_durations" is of type jsonb but expression is of type character varying*. `postgres_execute` terbukti ikut transaksi |
| `s3Endpoint: http://localhost:8333` | diabaikan | `localhost` dari dalam container menunjuk container itu sendiri |
| node id turunan | dipaksa `MatchingDispatch-b4819` | skema kita menghasilkan `…-c793c`; tweak ke node yang tidak ada **diabaikan Langflow tanpa galat** |

---

## 6. Kecepatan penyuntikan — biayanya di PostgreSQL, bukan di engine

Grade B, 200.020 baris: total ~20 detik, **~16 detik di antaranya penyuntikan**.
Spesifikasi menyebut "> 100.000 baris per detik"; di sini ~13 ribu.

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

## 7. Menjalankan simulasi

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
```

Berkasnya harus sudah digrading lewat `grading-dispatch` — engine mencari grade-
nya di `grading_jobs`.

**Database `synchrono` lokal di laptop ini ber-encoding WIN1252** (bawaan
PostgreSQL Windows). `portal_sim` sengaja dibuat UTF-8; DB portal sungguhan
hampir pasti UTF-8, dan nama berkarakter non-Latin gagal masuk ke WIN1252.

---

## 8. Yang sudah diuji

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

---

## 9. Yang belum

- **`reasoning` masih NULL.** Tahap berikutnya: porting algoritma dari cabang
  `ai_reasoning_parquet_version` (verdict → signature → cache
  `reasoning_patterns` → LLM hanya saat cache miss). Yang TIDAK dipakai dari
  sana: `reasoning-dispatch`, `reasoning_jobs`, dan baca/tulis `manual_matches`.
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
