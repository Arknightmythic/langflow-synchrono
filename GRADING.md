# Service Grading Synchrono (Langflow)

Mesin grading kualitas data kependudukan, berdiri sendiri dari backend Synchrono
dan dipanggil lewat HTTP. Seluruhnya berjalan di dalam Langflow — tidak ada
service tambahan.

Pasangannya adalah service matching di repo yang sama (lihat `README.md`).
Keduanya berbagi satu container Langflow, satu PostgreSQL, dan satu SeaweedFS.

---

## 1. Alur

```
  User unggah CSV
        |
        v
  Backend Synchrono  ──simpan──>  SeaweedFS   uploads/{fileId}/data.parquet
        |
        | POST /api/v1/run/grading-dispatch          <── API 1
        v
  ┌─────────────────────────────────────────────────────────┐
  │  Langflow                                               │
  │                                                         │
  │   GradingDispatch ──catat job──> PostgreSQL             │
  │        │                         grading_jobs = QUEUED  │
  │        │                                                │
  │        └──lepas thread──> G1..G6                        │
  │                            │                            │
  │                            ├─ baca  data.parquet        │
  │                            ├─ bersihkan NIK, tandai      │
  │                            ├─ hitung grade & skor        │
  │                            ├─ tulis enriched.parquet     │
  │                            └─ grading_jobs = COMPLETED   │
  │                                     │                    │
  └─────────────────────────────────────┼────────────────────┘
        ▲                                │
        │ POST /api/v1/run/grading-status │ POST callback.url
        │        (polling)  <── API 2     v
  Backend Synchrono  <───────────────  webhook (opsional)
```

**API 1 balas seketika** — diukur 0,9–1,2 detik, jauh di bawah batas 5 detik
yang dituntut spesifikasi integrasi. Grading sesungguhnya berjalan di thread
latar belakang, jadi backend dan UI Synchrono tidak pernah menunggu.

### Dua jalur hasil, pilih salah satu atau keduanya

| | Kapan dipakai |
|---|---|
| **Polling** (API 2) | Backend memanggil berulang sampai `done: true`. Tidak butuh endpoint publik di sisi backend. |
| **Webhook** | Isi `callback.url` pada muatan. Engine menelepon balik sekali selesai, sesuai spesifikasi integrasi bagian 4. |

Keduanya mengembalikan **objek `result` yang sama persis**, jadi backend cukup
punya satu penangan hasil. Webhook yang gagal terkirim tidak menghilangkan
hasil — datanya tetap terbaca lewat API 2, dan `callbackStatus` menerangkan
apa yang terjadi.

---

## 2. API 1 — Dispatch

```http
POST http://localhost:7860/api/v1/run/grading-dispatch?stream=false
x-api-key: <API KEY>
Content-Type: application/json
```

> **`x-api-key`, bukan `Authorization: Bearer`.** Bearer hanya berlaku untuk
> endpoint pengelolaan (`/api/v1/flows`, `/api/v1/all`). Endpoint `/run`
> menolaknya dengan `{"detail":"Invalid or missing API key"}`.

### Badan permintaan

```json
{
  "output_type": "chat",
  "input_type": "text",
  "input_value": "",
  "tweaks": {
    "GradingDispatch-a3967": {
      "payload": "{\"fileId\":\"csv_178...\",\"s3Bucket\":\"bucket-test\",\"parquetKey\":\"uploads/csv_178.../data.parquet\",\"callback\":{\"url\":\"http://portal:3000/api/internal/grading/callback\",\"secretToken\":\"rahasia\"}}"
    }
  }
}
```

Tiga hal yang mudah salah di sini:

1. **Kunci `tweaks` HARUS id node**, bukan nama komponen. Nama komponen
   diterima tanpa keluhan tapi parameternya tidak sampai, dan node gagal dengan
   "fileId kosong".
2. **Id node-nya tetap** — `GradingDispatch-a3967` dan `GradingStatus-3cc03`.
   Diturunkan dari nama endpoint dan nama komponen, jadi membangun ulang flow
   tidak mengubahnya. Aman di-hardcode di backend.
3. **`payload` adalah STRING berisi JSON**, bukan objek JSON. Isinya persis
   `OutboundGradingJobPayload` pada spesifikasi integrasi bagian 2.1.

Alternatifnya, isi kolom satuan alih-alih `payload` — lebih enak untuk Postman:

```json
"tweaks": { "GradingDispatch-a3967": {
    "file_id": "uji-b",
    "s3_bucket": "bucket-test",
    "parquet_key": "uploads/uji-b/data.parquet",
    "callback_url": "",
    "callback_token": ""
}}
```

### Balasan

```json
{
  "outputs": [{ "outputs": [{ "results": { "message": {
    "text": "{\"status\": \"QUEUED\", \"jobId\": \"ds-grade-20260915-b6cb12dc\", \"fileId\": \"uji-b\", \"message\": \"Grading job successfully queued for processing.\"}"
  }}}]}]
}
```

Isi yang berguna ada di `outputs[0].outputs[0].results.message.text`, dan itu
**string yang masih perlu di-`JSON.parse`**. Selubung berlapis ini bawaan
Langflow, bukan pilihan desain kami.

```js
const r = await fetch(url, { method: 'POST', headers, body });
const hasil = JSON.parse((await r.json()).outputs[0].outputs[0].results.message.text);
// hasil.jobId, hasil.status
```

---

## 3. API 2 — Status (polling)

```http
POST http://localhost:7860/api/v1/run/grading-status?stream=false
x-api-key: <API KEY>
Content-Type: application/json
```

```json
{
  "output_type": "chat",
  "input_type": "text",
  "input_value": "",
  "tweaks": { "GradingStatus-3cc03": { "file_id": "uji-b" } }
}
```

`file_id` mengembalikan job **terbaru** untuk berkas itu. Untuk menanyakan job
tertentu — termasuk percobaan lama setelah tombol "Coba Lagi Evaluasi" —
pakai `job_id`.

### Balasan (dibungkus sama seperti API 1)

```json
{
  "found": true,
  "status": "COMPLETED",
  "done": true,
  "jobId": "ds-grade-20260915-b6cb12dc",
  "fileId": "uji-b",
  "stage": "selesai",
  "queuedAt": "2026-09-15T09:38:57.048518+00:00",
  "startedAt": "2026-09-15T09:38:57.422220+00:00",
  "finishedAt": "2026-09-15T09:38:58.008805+00:00",
  "gradingDurationMs": 575,
  "enrichedParquetKey": "uploads/uji-b/enriched.parquet",
  "parquetSizeBytes": 101250,
  "recordCount": 2000,
  "callbackStatus": "SENT",
  "callbackError": null,
  "error": null,
  "result": { "...GradingCallbackPayload utuh..." }
}
```

| Field | Gunanya |
|---|---|
| `done` | **Ini yang dipakai untuk berhenti polling.** `true` saat COMPLETED maupun FAILED, jadi backend tidak perlu menghafal daftar status. |
| `status` | `QUEUED` → `RUNNING` → `COMPLETED` \| `FAILED` |
| `stage` | Tahap yang sedang berjalan, mis. `G3 clean NIK & flag anomalies`. Cukup untuk progress di UI. |
| `result` | Muatan callback utuh — sama persis dengan yang dikirim webhook. |
| `found` | `false` kalau berkas belum pernah digrading. `status` menjadi `NOT_FOUND`. |

Selang polling 2–3 detik sudah lebih dari cukup: 299.088 baris selesai dalam
1,6 detik pada mesin pengembangan.

---

## 4. Yang dilakukan mesin ini

### Enam elemen kependudukan

Dikenali dari nama kolom, tidak peka huruf besar-kecil, mengabaikan spasi dan
garis bawah. `nama_lengkap` dan `nama` sama-sama diterima, begitu juga
`nama_ibu_kandung` dan `nama_ibu` — lihat `ALIAS` di `lib/_grading.py` untuk
daftar lengkapnya.

Kolom yang **tidak** dikenali tetap ikut ke berkas enriched apa adanya.

Kalau grade meleset dari dugaan, jawabannya ada di
`result.elementDetails.recognisedElements` — di situ terlihat kolom mana yang
dipetakan ke elemen mana, dan mana yang `null`.

### Pemeriksaan NIK

| Yang diperiksa | Jadi metrik |
|---|---|
| Panjang tepat 16 digit | — |
| Dua digit pertama termasuk 38 kode provinsi | `nikProvinceInvalidCount` |
| Digit 7–8 (hari, +40 bila perempuan) cocok tanggal lahir | `nikDobMismatchCount` |
| Digit 7–8 menyiratkan jenis kelamin yang sama | `nikGenderMismatchCount` |
| Tidak kembar di dalam berkas | `duplicateNikCount`, `duplicateNikGroupsCount` |

Satu baris bisa kena lebih dari satu, jadi **jumlah metrik ini wajar melebihi
`totalAnomalies`**. Contohnya baris ber-NIK duplikat yang NIK-nya juga tidak
cocok dengan tanggal lahirnya.

### Dua bentuk NIK rusak dari spreadsheet

Keduanya sering terjadi dan akibatnya **tidak sama**:

| Bentuk | Contoh | Perlakuan |
|---|---|---|
| Notasi ilmiah | `3.20101E+15` | Panjang dipulihkan, tapi digit belakang benar-benar hilang. Baris selalu **tidak tepercaya**, `hasExcelScientificNik: true`. |
| Float utuh | `3201015107700001.0` | Tidak ada yang hilang. Bagian pecahan dibuang, baris tetap **tepercaya**. |

Membuang semua karakter non-digit begitu saja merusak kasus kedua: titiknya
hilang tapi nol di belakangnya ikut terbaca, dan NIK berubah jadi 17 digit.

---

## 5. Grade dan skor

**Dua angka, dua asal yang berbeda.**

`grade` (A–F) ditentukan **aturan struktural** — kolom apa yang ada dan seberapa
terisi. Ini mengikuti `GraderService` yang sudah berjalan di produksi.

`qualityScore` (0–100) adalah ukuran menerus, lalu **dipetakan ke dalam pita
milik grade-nya**. Jadi keduanya tidak pernah bertentangan: grade A tidak
mungkin berskor 40.

Urutannya sengaja begitu dan bukan sebaliknya. **Matching memilih rumus
pembobotan berdasarkan grade.** Berkas lima elemen tanpa kolom NIK adalah
grade C; kalau grade diturunkan dari skor, berkas itu naik ke B dan matching
akan memakai rumus ber-NIK terhadap kolom yang tidak ada.

| Grade | Pita skor | Kriteria struktural | Boleh matching? |
|:---:|:---:|---|:---:|
| A | 90–100 | 6 elemen lengkap 100%, **seluruh NIK tepercaya** | ya |
| B | 70–89 | 6 elemen ada, **≥70% NIK tepercaya**, nama 100%, tempat/tgl/JK ≥70%, ibu ≥60% | ya |
| C | 50–69 | **tanpa kolom NIK**, 5 elemen lengkap 100% | ya |
| D | 30–49 | **tanpa kolom NIK**, nama 100%, tempat/tgl/JK ≥70%, ibu ≥60% | ya |
| E | 10–29 | minimal nama + salah satu kombinasi (tgl+JK, tempat+tgl, tempat+ibu, tgl+wilayah) | ya |
| F | 0–9 | tidak satu pun pola di atas terpenuhi — butuh pemetaan kolom kustom | **tidak** |

### Hanya F yang diblokir, dan bukan karena mutunya

`grading-engine-integration-spec.md` §7 menyatakan D, E, dan F sama-sama tidak
layak sinkron. **Tabel aturan Indosat menyatakan sebaliknya untuk D dan E** —
keduanya punya kolom Automatis, Manual, dan Tidak Padan yang terisi penuh,
artinya memang diproses matching.

Kenyataan produksi memihak tabel aturan: berkas `825fc484` bergrade D dan
dicocokkan penuh — 200.020 baris, 115.551 auto-match. Karena itu
`can_proceed` untuk D dan E disetel `TRUE`.

F tetap diblokir, tetapi alasannya bukan mutu data. Grade F berarti kolom
berkasnya tidak dikenali sebagai elemen kependudukan, jadi matching belum punya
pemetaan untuk dipakai. Isinya bisa saja rapi — hanya nama kolomnya yang tidak
standar. `blockedReason` menyebutkan alasan itu, bukan ambang skor.

### Diadu dengan grade produksi

Keempat belas berkas yang masih ada di penyimpanan dinilai ulang oleh mesin ini,
lalu dibandingkan dengan `uploaded_files.grade` yang dicatat sistem lama:

| | Hasil |
|---|---|
| **Sembilan berkas dukcapil** (grade A–E, 200 ribu baris masing-masing) | **cocok semua** |
| Dua berkas CSV acak (portfolio, daftar tugas) | cocok, sama-sama F |
| Tiga berkas laporan anomali | lama F, baru E — lihat di bawah |

Ketiga berkas laporan anomali itu berkolom `Nama`, `NIK`, `Tanggal Lahir`,
`Tempat Lahir` — **berhuruf besar dan berspasi**. Grader lama mencocokkan nama
kolom persis dalam huruf kecil sehingga tidak mengenali satu pun, dan berkasnya
jatuh ke F. Mesin ini menormalkan huruf besar-kecil dan pemisah, jadi keempat
elemen itu dikenali dan berkasnya naik ke E.

Itu perbedaan yang perlu kamu putuskan: berkas yang dulu selalu butuh pemetaan
kolom kustom kini bisa langsung masuk matching. Kalau perilaku lama yang
diinginkan, persempit `ALIAS` di `lib/_grading.py`.

### Kode provinsi Papua tidak berurutan

Blok Papua **bukan** 91–96. Kode 93 tidak pernah dipakai, sementara pemekaran
2022–2023 menambahkan 92 (Papua Barat Daya), 95 (Papua Selatan), 96 (Papua
Tengah), dan 97 (Papua Pegunungan) di samping 91 dan 94 yang lama.

Menebaknya sebagai rentang berurutan membuat **seluruh penduduk Papua
Pegunungan dinyatakan ber-NIK tidak sah**. Ini sempat terjadi dan tertangkap
saat menilai berkas produksi `d88150c5`: 5.246 barisnya ditolak semata karena
berkode 97, dan berkas yang seharusnya grade A jatuh ke B. Ada `assert` di
`lib/_grading.py` yang menjaga daftarnya tetap 38 kode unik.

### Dua perbedaan lain yang disengaja dari GraderService lama

Keduanya membuat penilaian **lebih ketat**, bukan lebih longgar:

1. **Kelengkapan memakai "terisi"**, bukan "bukan NULL". CSV yang dikonversi ke
   parquet menyimpan sel kosong sebagai string kosong, bukan NULL, sehingga
   aturan lama menghitungnya sebagai terisi.

2. **Grade A dan B memakai `trusted`, bukan sekadar panjang 16 digit.** Ini
   terbukti penting saat pengujian: berkas yang seluruh NIK-nya rusak jadi
   notasi ilmiah Excel **lolos sebagai grade A** dengan aturan panjang, karena
   `3.20102E+15` memang dikembangkan menjadi tepat 16 digit. Spesifikasi
   bagian 7 memang menuntut lebih — grade A mensyaratkan tidak ada duplikasi
   maupun inkonsistensi jenis kelamin dan tanggal lahir.

### Belum dikerjakan: "case lain" grade E

Tabel aturan mencantumkan tiga kasus tambahan di bawah grade E yang **belum
diimplementasikan**:

1. Tanggal lahir berbeda-beda format dalam satu berkas
2. Nama alias, nama mengandung "bin"/"binti", dan nama panggilan
3. Anomali nama: kesalahan penulisan akibat huruf diketik berulang

Mesin ini mendeteksi keambiguan format tanggal dan melaporkannya lewat
`caseFlags.hasAmbiguousDateFormats`, tetapi tidak satu pun dari ketiganya
memengaruhi penentuan grade. Ketiganya terbaca lebih sebagai penjelasan
*mengapa* berkas grade E berantakan ketimbang aturan tambahan untuk
menetapkannya — tapi itu perlu dipastikan ke pemilik aturan sebelum
diimplementasikan.

### Sumber kebenaran aturan

Kalau dua dokumen ini berselisih, **tabel aturan Indosat yang menang** —
`grading-engine-integration-spec.md` adalah dokumen integrasi yang ditulis
belakangan, dan sudah terbukti meleset pada kelayakan sinkron grade D dan E.

Yang sudah dibandingkan baris per baris: ambang grade B dan D cocok persis,
keempat kombinasi grade E cocok persis, dan seluruh angka `grade_rules` untuk
matching cocok persis. Dua hal sengaja lebih ketat dari tabel (lihat bagian
berikut), dan grade F tidak ada di tabel sama sekali — ia warisan
`GraderService` yang sudah berjalan.

### Menyetel tanpa deploy ulang

Ambang kriteria A-D ada di tabel `grade_criteria` dan bisa diubah lewat API —
lihat [`KONFIGURASI.md`](KONFIGURASI.md). Perubahan berlaku pada grading
berikutnya tanpa restart.


Pita skor, label keparahan, kelayakan sinkronisasi, dan teks kriteria ada di
tabel `grade_bands` — bukan di kode:

```sql
UPDATE grade_bands SET score_min = 75, severity_label = 'Baik Sekali'
 WHERE grade_id = 2;
```

---

## 6. Berkas enriched

Ditulis ke `s3://{s3Bucket}/uploads/{fileId}/enriched.parquet` (bisa ditimpa
lewat `enrichedParquetKey` pada muatan).

**Seluruh kolom asli dipertahankan**, ditambah delapan kolom sesuai
spesifikasi bagian 3.2:

| Kolom | Tipe |
|---|---|
| `nik_clean` | VARCHAR |
| `nik_prov` | VARCHAR |
| `nik_hari` | INTEGER — sudah dikurangi 40 untuk perempuan |
| `nik_bulan` | INTEGER |
| `nik_tahun` | INTEGER |
| `nik_trusted` | BOOLEAN |
| `is_anomaly` | BOOLEAN |
| `anomaly_notes` | VARCHAR — kosong bila baris bersih |

Contoh `anomaly_notes` sungguhan dari data uji:

```
Beda Jenis Kelamin: digit hari NIK mengindikasikan wanita (71), namun kolom jenis kelamin terisi L
Beda Tanggal Lahir: digit NIK menunjuk 7/7, kolom tanggal lahir terisi 06/07/1970
Kode provinsi NIK tidak dikenali: 99
NIK duplikat di dalam berkas (muncul 100 kali)
```

Berkas ini **selalu selesai diunggah sebelum callback dikirim** — portal
memverifikasi keberadaannya lewat `HeadObject` dan membalas 400 kalau belum ada.

---

## 7. Pemasangan dari nol

```bash
cd langflow-synchrono/infra

# 1. Container (SeaweedFS + Langflow)
docker compose up -d --build

# 2. Bucket sesuai spesifikasi (sekali saja)
curl -X POST http://localhost:8888/buckets/bucket-test/

# 3. Skema PostgreSQL — termasuk grading_jobs & grade_bands
python apply_schema.py

# 4. Bangun ketiga flow di Langflow
python buat_flow_grading.py

# 5. API key: buka http://localhost:7860 -> Settings -> Langflow API Keys
```

Langkah 4 idempoten — menjalankannya ulang memperbarui flow yang sama dan
**tidak** mengubah id node.

### Tiga flow yang terbentuk

| Flow | Endpoint | Untuk |
|---|---|---|
| Synchrono Grading Dispatch | `/api/v1/run/grading-dispatch` | API 1, dipanggil backend |
| Synchrono Grading Status | `/api/v1/run/grading-status` | API 2, dipanggil backend |
| Synchrono Grading Pipeline | `/api/v1/run/grading` | G1–G6 telanjang, untuk penelusuran di kanvas |

Flow ketiga **sinkron** — memanggilnya lewat API akan memblokir sampai selesai.
Ia ada supaya tiap tahap terlihat dan bisa dijalankan satu-satu saat menelusuri
masalah, bukan untuk dipakai backend.

---

## 8. Pengujian

### Data uji dengan grade yang sudah diketahui

```bash
docker exec synchrono-langflow python /synchrono/infra/buat_data_uji_grading.py
```

Membuat delapan berkas, masing-masing menguji satu cabang keputusan:

| Berkas | Harapan | Terverifikasi |
|---|---|---|
| `uji-a` | A, skor 100, 0 anomali | ✔ |
| `uji-b` | B, skor 87, 500 anomali dari 5 jenis kerusakan | ✔ |
| `uji-c` | C, skor 69, tanpa kolom NIK | ✔ |
| `uji-d` | D, skor 47 | ✔ |
| `uji-e` | E, skor 29, hanya 3 elemen | ✔ |
| `uji-f` | F, skor 0, kolom tak dikenali | ✔ |
| `uji-excel` | E, `hasExcelScientificNik: true`, 0 tepercaya | ✔ |
| `uji-float` | A, skor 100, NIK `.0` pulih jadi 16 digit | ✔ |

### Menjalankan pipeline tanpa Langflow

```bash
# per tahap, tanpa menulis enriched.parquet
docker exec synchrono-langflow python /synchrono/run_grading_local.py \
    --file-id uji-b --bucket bucket-test \
    --key uploads/uji-b/data.parquet --skip-write

# lewat tabel job + pekerja latar belakang, dengan polling
docker exec synchrono-langflow python /synchrono/run_grading_local.py \
    --file-id uji-b --bucket bucket-test \
    --key uploads/uji-b/data.parquet --job
```

> **Semua pengujian harus di dalam container.** Di mesin Windows ini extension
> `httpfs` DuckDB diblokir Application Control policy, jadi apa pun yang
> menyentuh SeaweedFS gagal kalau dijalankan dari host. `apply_schema.py` dan
> `buat_flow_grading.py` tetap bisa dari host karena keduanya tidak menyentuh S3.

---

## 9. Yang perlu diketahui

### Job bisa mangkrak, dan itu sudah ditangani

Pekerja grading adalah thread di dalam container Langflow — konsekuensi dari
memilih "tanpa service tambahan". Kalau container restart di tengah proses,
thread-nya mati tanpa sempat menulis apa pun.

Penawarnya: pekerja **berdetak** tiap ganti tahap. Job yang tidak berdetak lebih
dari 15 menit (`GRADING_STALE_MINUTES`) ditandai `FAILED` pada panggilan API
berikutnya, dengan pesan yang menjelaskan sebabnya. Backend tidak menunggu
selamanya, dan berkasnya bisa dikirim ulang.

Sudah diuji: job RUNNING berdetak 30 menit lalu dipanen; job RUNNING yang masih
berdetak tidak tersentuh.

### Batas konkurensi

Maksimal dua grading berjalan bersamaan (`GRADING_MAX_CONCURRENT`). Yang
menunggu giliran tetap berstatus `QUEUED` dan **tetap berdetak**, supaya tidak
keliru dipanen sebagai mangkrak.

### Grade 6 (F) dan matching

Grade F berarti berkasnya butuh pemetaan kolom kustom sebelum bisa dicocokkan.
Service matching di repo ini **belum mendukung grade 6** — node `n1_open_session`
menolaknya secara eksplisit.

### Flow dan API key tersimpan di volume — pastikan tetap begitu

Langflow 1.12.x menaruh SQLite-nya **di dalam folder paketnya sendiri**
(`/app/.venv/.../langflow/langflow.db`), bukan di `LANGFLOW_CONFIG_DIR`. Folder
itu ada di lapisan container, jadi `docker compose up -d` yang me-recreate
container akan **menghapus seluruh flow dan API key tanpa pesan galat apa pun**.

`docker-compose.yml` menyetel `LANGFLOW_DATABASE_URL` ke dalam volume untuk
menutup lubang ini. Sudah diuji: `docker compose down && up -d` lalu flow dan
API key-nya tetap ada.

Kalau suatu saat flow hilang sendiri, periksa env itu dulu sebelum hal lain.

### Callback yang gagal

Dicoba ulang tiga kali dengan jeda menaik (2s, 4s, 8s). Galat 4xx selain 429
tidak diulang — muatannya yang salah, mengulang tidak menolong. Kegagalan
callback **tidak** menggagalkan grading; hasilnya tetap tersimpan dan terbaca
lewat API 2.

### Kredensial pengembangan

Semua nilai di repo ini untuk mesin lokal: Langflow `admin`/`synchrono123`,
SeaweedFS `synchrono`/`synchrono123`, PostgreSQL `postgres` tanpa kata sandi.
**Ganti sebelum dipakai di luar mesin lokal.**

---

## 10. Berkas

```
langflow-synchrono/
├── components/grading/
│   ├── g1_open_grading.py     OpenGradingSession    buka DuckDB + S3 + PG
│   ├── g2_load_raw.py         LoadRawParquet        baca parquet, kenali elemen
│   ├── g3_flag_anomalies.py   FlagAnomalies         bersihkan NIK, tandai anomali
│   ├── g4_score_grade.py      ScoreAndGrade         grade A-F + skor mutu
│   ├── g5_write_enriched.py   WriteEnrichedParquet  tulis enriched ke S3
│   ├── g6_build_payload.py    BuildCallbackPayload  susun GradingCallbackPayload
│   ├── api1_dispatch.py       GradingDispatch       API 1
│   └── api2_status.py         GradingStatus         API 2
├── lib/
│   ├── _grading.py    seluruh logika grading (SQL DuckDB)
│   ├── _jobs.py       tabel grading_jobs + webhook
│   └── _worker.py     thread pekerja latar belakang
├── infra/
│   ├── schema_grading.sql          grading_jobs + grade_bands
│   ├── buat_flow_grading.py        bangun ketiga flow
│   ├── flow_util.py                pembangun flow (dipakai bersama matching)
│   └── buat_data_uji_grading.py    delapan berkas uji
└── run_grading_local.py            jalankan pipeline tanpa Langflow
```

Node di `components/grading/` sengaja tipis — semuanya memanggil fungsi di
`lib/`. Dengan begitu pekerja latar belakang dan node kanvas menjalankan
**jalur kode yang sama persis**, bukan dua salinan yang bisa menyimpang.
