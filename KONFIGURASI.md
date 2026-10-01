# Konfigurasi grading & matching

Semua angka yang menentukan hasil grading dan matching adalah **data**, bukan
kode: bisa dilihat dan diubah lewat API tanpa deploy ulang, dan setiap
perubahan tercatat. Sejak 30 Sep 2026 ini termasuk **bobot skor**, **elemen
yang dihitung kosong**, dan **pembersihan nama** per grade — sebelumnya
tertanam di kode.

Cara kerja matching yang memakai angka-angka ini: [MATCHING.md](MATCHING.md).

---

## Yang bisa diatur

### Per grade

| Bagian API | Isi | Grade | Tabel |
|---|---|---|---|
| `criteria` | ambang kelengkapan per elemen & mutu NIK (grading) | A–D | `grade_criteria` |
| `score` | pita skor mutu, label, boleh lanjut ke sinkronisasi | A–F | `grade_bands` |
| `matching.autoMissingMax` … `reviewScoreMax` | ambang AUTO / REVIEW | A–F | `grade_rules` |
| `matching.weights` | **bobot skor per elemen, dalam persen** | A–E | `grade_rules.bobot` |
| `matching.missingElements` | **elemen yang dihitung "kosong"** untuk aturan AUTO/REVIEW | A–E | `grade_rules.elemen_kosong` |
| `matching.nameCleaning` | **pembersihan nama sebelum dibandingkan** | A–E | `grade_rules.bersih_nama` |
| `matching.blocking` | kueri blocking — **hanya dibaca** | A–E | `matching_queries` |
| `matching.availableElements` | dihitung: elemen yang bisa diberi bobot / dihitung kosong dengan kueri blocking grade itu | A–E | — |
| `matching.analysis` | dihitung: skor tertinggi per jumlah kosong + peringatan | A–E | — |

Elemen yang boleh diberi bobot / dihitung kosong: `nama`, `tempat_lahir`,
`tanggal_lahir`, `jenis_kelamin`, `nama_ibu`, `wilayah`.

- **Skor dihitung dari KELUARAN kueri blocking**, bukan dari tabel asal. Kueri
  yang disalin dari sistem lama hanya membawa kolom rumus lama grade itu (grade
  C tanpa nama ibu, grade A hanya nama). Migrasi `007_blocking_semua_elemen`
  menambahkan kolom yang kurang ke **daftar SELECT** setiap kueri — syarat
  JOIN-nya tidak disentuh, jadi kandidat yang ditemukan tetap sama — sehingga
  keenam elemen bisa diberi bobot di semua grade. Kalau kueri di basis data
  diubah langsung dan tidak membawa kolom sebuah elemen, API **menolak** bobot
  untuk elemen itu; `availableElements` menunjukkan yang bisa dipakai.

- **NIK tidak bisa diberi bobot.** NIK dipakai untuk blocking grade A/B dan
  Pass 1 (cocok persis), bukan untuk skor kemiripan.
- **`wilayah`** = rata-rata kemiripan provinsi, kabupaten, kecamatan,
  kelurahan yang terisi di kedua sisi; dihitung kosong hanya kalau keempatnya
  kosong. Master tidak punya kolom alamat jalan.
- **`nameCleaning`** — tiga sakelar, berlaku untuk nama DAN nama ibu, di sisi
  berkas DAN master, di semua pass (termasuk Pass 1/2 yang membandingkan nama
  persis, dan blocking yang memakai 3 huruf awal nama):

  | Sakelar | Contoh |
  |---|---|
  | `titles` | `Dr. Hj. Siti Aminah, S.Pd.` → `siti aminah` |
  | `patronym` | `Maulana bin Ahmad` → `maulana` |
  | `abbreviations` | `M. Rizki`, `Muh Rizki`, `Moch. Rizki` → `muhammad rizki` (hanya kata pertama) |

### Global

| Bagian API | Isi | Bawaan |
|---|---|---|
| `global.grading.scoreWeights` | bobot skor mutu grading: `kelengkapan`, `nik_tepercaya` (jumlah 1) | 0,6 / 0,4 |
| `global.grading.gradeECombinations` | kombinasi kolom yang membuat berkas jadi grade E | 4 kombinasi sistem lama |
| `global.matching.conflictEpsilon` | selisih skor dua kandidat teratas yang masih dianggap seri (CONFLICT) | env `MATCHING_CONFLICT_EPSILON`, atau 0 |
| `global.matching.contradictionJw` | di bawah ini nama ibu dianggap bertentangan (aturan pengaman 2) | env `MATCHING_KONTRA_JW`, atau 0,80 |

`global.meta.<kunci>.source` menunjukkan asal nilainya: `config` (disimpan
lewat API), `env`, atau `default`.

**Nilai awal = persis aturan yang berlaku sebelumnya** (sistem lama). Deploy
dengan migrasi 006 dan 007 tidak mengubah satu hasil pun; mengganti ke nilai
lain adalah langkah terpisah lewat API.

---

## API

Dua bentuk, isinya sama. Semua butuh `x-api-key`.

| | Kontrak Langflow (portal) | REST |
|---|---|---|
| Baca semua / satu grade | `POST /api/v1/run/config-rules`, tweak `grade_id` | `GET /api/v1/config/rules[/{gradeId}]` |
| Ubah satu grade | `POST /api/v1/run/config-rules-update`, `payload` = `{"gradeId": 3, ...}` | `PATCH /api/v1/config/rules/{gradeId}` |
| Ubah nilai global | `config-rules-update`, `payload` = `{"global": {...}}` | `PATCH /api/v1/config/global` |
| Riwayat perubahan | — | `GET /api/v1/config/history?gradeId=&limit=` |
| Isi satu versi | — | `GET /api/v1/config/versions/{configVersion}` |

Koleksi Postman (`infra/postman_synchrono_service.json`, folder 2) memuat
semuanya.

### Aturan mengubah

- **Sebagian**: hanya field yang disebut yang berubah.
- **Kecuali** `weights` dan `missingElements`: **diganti utuh**. Bobot harus
  berjumlah 100 — menggabungkan sebagian bobot dengan yang lama hampir selalu
  menghasilkan jumlah yang salah.
- `nameCleaning` digabung per sakelar. `scoreWeights` digabung per kunci.
- **`null` = kembali ke bawaan** (untuk `weights`, `missingElements`,
  `nameCleaning`, dan semua kunci global). Pada ambang artinya lain:
  `reviewMissingCount: null` = REVIEW **tanpa syarat** jumlah kosong.
  `autoScoreMin`, `reviewScoreMin`, `reviewScoreMax` wajib angka.
- **`dryRun: true`** — divalidasi dan disimulasikan, tidak ditulis. Balasan
  memuat `after` (hasil simulasi), `changed`, dan `warnings`.
- **Urutan bobot dipertahankan** dan ikut menentukan hasil di digit terakhir
  (skor adalah jumlah pecahan biner). Kirim sebagai objek JSON berurutan, atau
  `[["nama", 70], ["nama_ibu", 30]]`.

### Ditolak vs diperingatkan

| | Kapan | Balasan |
|---|---|---|
| **Ditolak — bentuk** | field tak dikenal, tipe salah, mengubah `blocking` | REST 400 · Langflow 500 dengan `detail` |
| **Ditolak — isi** (`problems`) | bobot tidak berjumlah 100, elemen tak dikenal atau tidak dibawa kueri blocking, ambang di luar 0–100, grade A–D jadi tak tercapai, pita skor tumpang tindih | REST 409 · Langflow 200 dengan `applied: false` |
| **Diperingatkan** (`warnings`) | konfigurasi sah tapi akibatnya mungkin tak disangka | tetap disimpan |

Peringatan dihitung dari **skor tertinggi yang masih mungkin** untuk setiap
jumlah elemen kosong — dengan aritmetika yang sama persis dengan SQL-nya —
lalu dicocokkan dengan ambang:

- *"REVIEW tidak pernah terjadi: butuh tepat 99 elemen kosong, padahal elemen
  yang dihitung kosong hanya 0"* — grade A dan C dengan nilai bawaan.
- *"REVIEW tidak mungkin tercapai: dengan 2 elemen kosong, skor tertinggi 80
  di bawah reviewScoreMin 80.001"*.
- *"Baris dengan 2 elemen kosong yang selebihnya cocok sempurna jatuh ke
  UNMATCH: tidak bisa AUTO (kosong 2 > autoMissingMax 1), dan skornya di luar
  pita REVIEW 85–<90"* — misalnya grade D dengan bobot nama 100%.

---

## Jejak: riwayat dan versi

- **Riwayat** (`config_riwayat`): setiap perubahan lewat API — siapa
  (`updatedBy`), kapan, field apa, dari nilai berapa ke berapa, dan versi
  konfigurasi sesudahnya.
- **Versi** (`config_versi`): sidik 12 heksa dari seluruh isi konfigurasi yang
  menentukan hasil (kriteria, pita, aturan matching, kueri blocking, global).
  Setiap job mencatat versi yang dipakainya:

  | Di mana | Kunci |
  |---|---|
  | hasil grading (callback & `grading-status`) | `configVersion` |
  | callback matching | `metrics.configVersion`, `metrics.rulesApplied` |
  | tabel job portal | `syncrono_matching_job.blocking_metrics` (kalau kolomnya ada) |

  `GET /api/v1/config/versions/{configVersion}` mengembalikan isinya — hasil
  lama tetap bisa dijelaskan walau aturannya sudah berubah.

Konfigurasi dibaca **sekali di awal job**: mengubahnya tidak mengganggu job
yang sedang berjalan. Matching ulang sesudah perubahan memakai aturan baru —
dan, seperti biasa, mengganti hasil lama beserta keputusan review operatornya
(MATCHING.md §11.4).

---

## Contoh

Acuan aturan adalah **engine lama** (`data-matching`, V2) — itulah nilai
bawaannya. **Selalu `dryRun: true` dulu**, baca `warnings`, baru kirim tanpa
`dryRun`.

Mengubah bobot grade C (diganti utuh, jumlah harus 100):

```json
PATCH /api/v1/config/rules/3
{"matching": {"weights": {"nama": 60, "tanggal_lahir": 25, "tempat_lahir": 15}},
 "updatedBy": "nama-operator", "dryRun": true}
```

Menyalakan pembersihan gelar di grade B:

```json
PATCH /api/v1/config/rules/2
{"matching": {"nameCleaning": {"titles": true}}, "updatedBy": "nama-operator", "dryRun": true}
```

Mengembalikan satu grade ke nilai bawaan (grade 3 sebagai contoh; ambang
bawaannya ada di MATCHING.md §4):

```json
PATCH /api/v1/config/rules/3
{"matching": {"weights": null, "missingElements": null, "nameCleaning": null,
              "reviewMissingCount": 99}, "updatedBy": "nama-operator"}
```

---

## Deploy

Dua migrasi, dijalankan otomatis oleh `skema-service` saat `docker compose up`
(DEPLOY.md):

- **`006_config_dinamis`** — kolom `bobot`, `elemen_kosong`, `bersih_nama` di
  `grade_rules`, diisi nilai yang selama ini berlaku; tabel `engine_config`,
  `config_riwayat`, `config_versi`. `engine_config` sengaja kosong, supaya env
  `MATCHING_CONFLICT_EPSILON` / `MATCHING_KONTRA_JW` yang mungkin sudah
  disetel tetap berlaku sampai ada yang menyimpan nilai lewat API.
- **`007_blocking_semua_elemen`** — kolom elemen yang kurang ditambahkan ke
  daftar SELECT kueri blocking, per elemen dan hanya kalau belum ada (aman
  untuk kueri A/B server yang teksnya berbeda dari repo). Syarat JOIN tidak
  berubah.

Keduanya tidak mengubah hasil dengan nilai bawaan. Diuji 30 Sep 2026 pada
52.493 baris × master 2 juta, grade A–E, kode lama vs baru: status, NIK, skor,
metode, dan pola **identik di setiap baris**; satu-satunya selisih adalah
snapshot satu baris yang NIK master-nya tercatat dua kali — pilihan barisnya
acak di kode lama juga (MATCHING.md §9). Grading berkas uji A–E: hasil dan
enriched.parquet identik. `configVersion` ikut berubah sekali karena teks
kueri blocking termasuk isi versi.
