# Mekanisme Matching & AI Reasoning (Parquet Version)

Dokumentasi ini menjelaskan arsitektur terpadu antara **Mesin Pencocokan (Matching Engine)** dan **Mesin Penalaran AI (AI Reasoning Engine)** yang bekerja langsung di atas data master kependudukan skala masif (**100 Juta – 275 Juta baris Parquet**).

---

## 1. Arsitektur Alur Data End-to-End

```
┌────────────────────────────────────────────────────────────────────────┐
│                        DATA INCOMING (CSV / PARQUET)                   │
│         nik, nama, tempat_lahir, tanggal_lahir, jenis_kelamin, ibu     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                    TAHAP 1: MATCHING ENGINE (DUCKDB)                   │
│                                                                        │
│   Incoming (3.000 baris)  ◄─── JOIN ───►  Master (100.000.000 baris)   │
│                                           `uji-master-100juta.parquet` │
│                                                                        │
│   1. Normalisasi string, tanggal, & gender                             │
│   2. Jaro-Winkler Similarity berbobot per grade:                       │
│      Skor = 0.8*Nama + 0.1*TempatLahir + 0.1*NamaIbu                   │
│   3. Klasifikasi Ambang Batas (Threshold Classification):             │
│      • Skor ≥ 85.0       ──► Auto-Match (Hasil: 1)                     │
│      • Skor 75.0 - 85.0  ──► MANUAL REVIEW (Hasil: 2) ──┐              │
│      • Skor < 75.0       ──► Unmatch (Hasil: 3)         │              │
└─────────────────────────────────────────────────────────┼──────────────┘
                                                          │
                                                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      POSTGRESQL TRANSACTIONAL STORE                    │
│                                                                        │
│   • Tabel `institution`    : Menyimpan seluruh status (1, 2, 3)        │
│   • Tabel `manual_matches` : HANYA baris Hasil = 2 (Status: 'PENDING') │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                 TAHAP 2: AI REASONING ENGINE (PARQUET)                 │
│                                                                        │
│   1. Two-Phase Semi-Join ke 100M Parquet (Ambil Master NIK Target)     │
│   2. SQL Pre-Verdict: Bandingkan 5 atribut secara deterministik        │
│   3. Cek Cache Pola (MD5 Signature Hash):                              │
│      ├─ Cache HIT (Sudah Ada)  ──► Ambil template kalimat langsung     │
│      └─ Cache MISS (Pola Baru) ──► Panggil On-Premise LLM              │
│   4. Bulk Template Hydration & Sanitasi Output                         │
│   5. Update `manual_matches` (Status: 'COMPLETED')                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mekanisme Matching & Flagging Manual Review

### 2.1 Mengapa File CSV Tidak Membutuhkan Flag "Manual Review"?
File masukan (CSV dari dinas/instansi) murni merupakan data mentah kependudukan. Flag `MANUAL_REVIEW` **bukan ditentukan oleh manusia penginput**, melainkan hasil kalkulasi algoritma kemiripan data.

### 2.2 Algoritma Penilaian (Scoring Logic)
Mesin matching menggunakan algoritma **Jaro-Winkler Similarity** yang dioptimalkan di level mesin DuckDB melalui macro:
$$\text{Skor} = (w_1 \cdot J(\text{nama}) + w_2 \cdot J(\text{tempat\_lahir}) + w_3 \cdot J(\text{nama\_ibu})) \times 100$$

Bobot standar untuk identitas umum (Grade 2):
* **Nama Lengkap:** `0.8` (Bobot terbesar, memverifikasi kemiripan fonetik/spelling)
* **Tempat Lahir:** `0.1` (Memverifikasi kesesuaian wilayah lahir)
* **Nama Ibu Kandung:** `0.1` (Kunci verifikasi kekerabatan)

### 2.3 Aturan Ambang Batas (Classification Rules)
Berdasarkan aturan verifikasi kependudukan:
1. **Hasil = 1 (`AUTO_MATCH`):** Skor $\ge 85.0$. Data dianggap identik; langsung disetujui sistem tanpa campur tangan manusia.
2. **Hasil = 2 (`MANUAL_REVIEW`):** Skor $75.0 \le \text{Skor} < 85.0$ atau terdapat perbedaan minor pada atribut vital. Data ini berada di zona abu-abu (*borderline*) dan **wajib ditinjau oleh operator**.
3. **Hasil = 3 (`UNMATCH`):** Skor $< 75.0$ atau NIK tidak ditemukan di data master.

### 2.4 Penyimpanan Otomatis ke Database
Saat matching selesai:
* Seluruh 3.000 baris dicatat ke tabel `institution` untuk audit trail.
* Khusus baris berstatus **Hasil = 2 (`MANUAL_REVIEW`)**, sistem otomatis melakukan *insert snapshot* ke tabel `manual_matches` dengan field `reasoning_status = 'PENDING'`.

---

## 3. Spesifikasi Skema Basis Data PostgreSQL (Kontrak Antar-Sistem)

Bagian ini adalah **kontrak teknis tunggal** agar tim pengembang aplikasi, tim data matching, dan tim frontend memiliki pemahaman skema database yang 100% selaras.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 ALUR DATA ANTAR-SISTEM                                 │
└────────────────────────────────────────────────────────────────────────────────────────┘

 [1. Incoming CSV] ──────────► [2. Matching Engine] ◄──────── [100M Parquet Master]
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │   POSTGRESQL DATABASE     │
                        ├───────────────────────────┤
                        │ • institution             │ (Semua baris, audit trail)
                        │ • manual_matches (PENDING)│ (Khusus baris skor 75 - 85)
                        └─────────────┬─────────────┘
                                      │
 [3. Aplikasi Backend] ───────────────┼───────────────► Trigger POST /reasoning-dispatch
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  LANGFLOW AI REASONING    │
                        │    (Model: gemma3:12b)    │
                        └─────────────┬─────────────┘
                                      │
                         Update reason & COMPLETED
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │   manual_matches          │
                        │   status = 'COMPLETED'    │
                        └─────────────┬─────────────┘
                                      │
 [4. Operator UI] ◄───────────────────┴─────────────── Query alasan & verifikasi
```

### 3.1 Kamus Data & DDL Tabel Inti

#### 1. Tabel `public.institution` (Rekam Jejak Hasil Matching & Audit Trail)
Menyimpan seluruh baris data incoming yang telah dicocokkan beserta skor dan statusnya.

```sql
CREATE TABLE IF NOT EXISTS public.institution (
    file_id        VARCHAR(64)      NOT NULL,
    id_incoming    TEXT             NOT NULL,
    nik_master     VARCHAR(32),
    match_score    DOUBLE PRECISION,
    match_result   SMALLINT         REFERENCES ref_match_results(match_result_id),
    upload_date    TIMESTAMPTZ,
    inserted_date  TIMESTAMPTZ      DEFAULT NOW(),
    PRIMARY KEY (file_id, id_incoming)
);

CREATE INDEX IF NOT EXISTS idx_institution_file ON public.institution (file_id);
CREATE INDEX IF NOT EXISTS idx_institution_result ON public.institution (file_id, match_result);
```

#### 2. Tabel `public.manual_matches` (Antrean Review & Output Penjelasan AI)
Menyimpan snapshot data yang membutuhkan peninjauan manual beserta teks kalimat alasan yang dihasilkan oleh AI Reasoning (`gemma3:12b`).

```sql
CREATE TABLE IF NOT EXISTS public.manual_matches (
    file_id                VARCHAR(64)  NOT NULL,
    id_incoming            TEXT         NOT NULL,
    nama_incoming          TEXT,
    tempat_lahir_incoming  TEXT,
    tanggal_lahir_incoming TEXT,
    jenis_kelamin_incoming TEXT,
    nama_ibu_incoming      TEXT,
    reason                 TEXT,                 -- Teks narasi AI reasoning (gemma3:12b)
    pattern_name           VARCHAR(255),         -- Pola perbedaan (misal pattern_NAMA_DIFF_...)
    reasoning_source       VARCHAR(20),          -- 'LLM' atau 'CACHE'
    reasoning_status       VARCHAR(30)  NOT NULL DEFAULT 'PENDING',
    PRIMARY KEY (file_id, id_incoming)
);

CREATE INDEX IF NOT EXISTS idx_manual_file ON public.manual_matches (file_id);
CREATE INDEX IF NOT EXISTS idx_manual_status ON public.manual_matches (file_id, reasoning_status);
```

#### 3. Tabel `public.reasoning_patterns` (Tabel Cache Pola & Template)
Menyimpan template kalimat ber-placeholder untuk setiap kombinasi perbedaan yang pernah ditemukan. Mencegah pemanggilan LLM berulang kali.

```sql
CREATE TABLE IF NOT EXISTS public.reasoning_patterns (
    pattern_hash       VARCHAR(64)  PRIMARY KEY,  -- MD5 hash dari pola perbedaan
    pattern_name       VARCHAR(255) NOT NULL,
    pattern_signature  TEXT         NOT NULL,     -- Format: jenis_kelamin:SAME|nama_ibu:DIFF|...
    reason_template    TEXT         NOT NULL,     -- Template: Full name is different (Institution: {incoming.nama_lengkap} vs Master: {master.nama_lengkap})...
    sample_id          TEXT,
    hit_count          INTEGER      DEFAULT 1,
    created_at         TIMESTAMPTZ  DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reasoning_patterns_signature ON public.reasoning_patterns (pattern_signature);
```

#### 4. Tabel `public.reasoning_jobs` (Tabel Pelacakan Job Asinkron)
Digunakan oleh Langflow Custom Component dan Background Worker untuk mencatat status antrean eksekusi dan heartbeat.

```sql
CREATE TABLE IF NOT EXISTS public.reasoning_jobs (
    job_id        VARCHAR(64) PRIMARY KEY,
    file_id       VARCHAR(64) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'QUEUED', -- QUEUED, RUNNING, COMPLETED, FAILED
    stage         TEXT,                                  -- BUILDING_VERDICT, RESOLVING_PATTERNS, HYDRATING_TEXT, FINISHED
    error         TEXT,
    result        JSONB,
    heartbeat_at  TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reasoning_jobs_file ON public.reasoning_jobs (file_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reasoning_jobs_status ON public.reasoning_jobs (status);
```

#### 5. Tabel `public.ref_match_results` (Referensi Kode Status Pencocokan)
Standardisasi kode status pencocokan kependudukan nasional:

| `match_result_id` | `match_result_name` | Deskripsi Bisnis |
| :---: | :--- | :--- |
| **`1`** | `AUTO_MATCH` | Skor $\ge 85.0$, disetujui otomatis oleh sistem |
| **`2`** | `MANUAL_REVIEW` | Skor $75.0 \le \text{Skor} < 85.0$, wajib direview operator |
| **`3`** | `AUTO_UNMATCH` | Skor $< 75.0$ atau NIK tidak ditemukan di Master |
| **`4`** | `MANUAL_MATCH` | Disetujui secara manual oleh verifikator (*Approved*) |
| **`5`** | `MANUAL_UNMATCH` | Ditolak secara manual oleh verifikator (*Rejected*) |

---

### 3.2 Siklus Hidup Status (*State Machine*)

1. **Siklus Status AI Reasoning (`manual_matches.reasoning_status`):**
   $$\text{PENDING} \longrightarrow \text{RUNNING} \longrightarrow \text{COMPLETED} \quad (\text{atau } \text{FAILED})$$
   * `PENDING`: Data baru dimasukkan oleh mesin matching; menunggu diproses AI.
   * `RUNNING`: Sedang dianalisis oleh worker Langflow / DuckDB.
   * `COMPLETED`: Kolom `reason` telah terisi dengan kalimat penjelasan AI `gemma3:12b`.
   * `FAILED`: Terjadi error sistem (disediakan pesan error fallback otomatis).

2. **Siklus Keputusan Operator (`institution.match_result`):**
   $$\text{MANUAL\_REVIEW (2)} \xrightarrow[\text{Verifikator Manusia}]{\text{Review via UI}} \begin{cases} \text{MANUAL\_MATCH (4)} & \text{Operator menekan tombol APPROVE} \\ \text{MANUAL\_UNMATCH (5)} & \text{Operator menekan tombol REJECT} \end{cases}$$

---

### 3.3 Panduan Query SQL untuk Tim Pengembang Aplikasi Eksternal

#### Query A: Mengambil Daftar Antrean Manual Review untuk Ditampilkan di UI Operator
```sql
SELECT 
    mm.id_incoming,
    mm.nama_incoming,
    mm.tempat_lahir_incoming,
    mm.tanggal_lahir_incoming,
    mm.jenis_kelamin_incoming,
    mm.nama_ibu_incoming,
    i.nik_master,
    i.match_score,
    mm.reason,             -- <=== Kalimat penjelasan AI Gemma 3:12B
    mm.reasoning_status
FROM public.manual_matches mm
JOIN public.institution i ON (mm.file_id = i.file_id AND mm.id_incoming = i.id_incoming)
WHERE mm.file_id = :target_file_id
  AND mm.reasoning_status = 'COMPLETED'
  AND i.match_result = 2   -- Hanya yang belum diputuskan operator
ORDER BY i.match_score DESC;
```

#### Query B: Menyimpan Keputusan Operator Saat Tombol "APPROVE" Ditekan
```sql
-- 1. Perbarui status hasil pencocokan menjadi disetujui manual (4)
UPDATE public.institution
SET match_result = 4, inserted_date = NOW()
WHERE file_id = :target_file_id AND id_incoming = :target_id_incoming;

-- 2. (Opsional) Beri tanda pada tabel manual_matches
UPDATE public.manual_matches
SET reasoning_status = 'APPROVED'
WHERE file_id = :target_file_id AND id_incoming = :target_id_incoming;
```

#### Query C: Menyimpan Keputusan Operator Saat Tombol "REJECT" Ditekan
```sql
-- 1. Perbarui status hasil pencocokan menjadi ditolak manual (5)
UPDATE public.institution
SET match_result = 5, inserted_date = NOW()
WHERE file_id = :target_file_id AND id_incoming = :target_id_incoming;

-- 2. (Opsional) Beri tanda pada tabel manual_matches
UPDATE public.manual_matches
SET reasoning_status = 'REJECTED'
WHERE file_id = :target_file_id AND id_incoming = :target_id_incoming;
```

---

## 4. Kebutuhan AI Reasoning untuk Manual Review

### 4.1 Masalah di Lapangan (Pain Point Verifikator)
Operator kependudukan yang membuka ribuan baris data *Manual Review* kesulitan melihat **di mana letak perbedaannya** secara cepat. Membandingkan 5 kolom teks secara manual untuk ribuan baris memakan waktu dan rentan *human error*.

### 4.2 Peran AI Reasoning
AI bertugas membaca perbedaan data tersebut dan merangkumnya menjadi **satu kalimat penjelasan berbahasa baku dalam sekejap**:
> *"Full name is different (Institution: Ciaobella Icha Yuliarti vs Master: Ciaobella Hesti Lazuardi), place of birth is different (Institution: Sawahlunto vs Master: Langsa), and mother's name is different (Institution: Farah Rahmawati vs Master: Carla Wijayanti)."*

Verifikator cukup membaca satu kalimat ini untuk langsung membuat keputusan (*Approve/Reject*).

---

## 5. Mekanisme AI Reasoning di Atas 100 Juta Parquet

### 5.1 Step 1: Two-Phase Semi-Join Pushdown (Zero-OOM)
Daripada me-load seluruh 100 juta baris master ke memori, DuckDB hanya menarik NIK yang sedang berada di antrean `manual_matches`:

```sql
WITH pending_records AS (
    SELECT file_id, id_incoming, nik_master, ...
    FROM pg.public.manual_matches mm
    JOIN pg.public.institution i USING (file_id, id_incoming)
    WHERE mm.file_id = 'xxx' AND mm.reasoning_status = 'PENDING'
),
target_master AS (
    SELECT nik, nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu
    FROM read_parquet('uji-master-100juta.parquet')
    WHERE nik IN (SELECT DISTINCT nik_master FROM pending_records)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY nik) = 1
)
SELECT ... 
FROM pending_records p
LEFT JOIN target_master m ON p.nik_master = m.nik;
```
* **Hasil:** Query join atas file 3.01 GB selesai hanya dalam **~2.4 detik**.

### 5.2 Step 2: Ground-Truth Verdict & Pattern Caching
Setiap baris dihitung status 5 atributnya (`SAME`, `DIFFERENT`, `EMPTY_IN_INSTITUTION`, `EMPTY_IN_MASTER`).
* Ruang pola kombinasinya terbatas: $4^5 = 1.024$ kemungkinan pola.
* Sistem membuat **MD5 Signature Hash**, contoh:
  `pattern_NAMA_DIFF_IBU_DIFF_2667d460`
* **Cache Check:**
  * Jika hash pola sudah ada di tabel `reasoning_patterns`, template teks diambil seketika (**0.001 detik, tanpa memanggil LLM**).
  * Jika pola baru muncul, LLM on-premise (`gemma3:12b`) dipanggil sekali untuk membuat template penjelasannya.

### 5.3 Step 3: Bulk Template Hydration & Sanitasi
Template kalimat seperti:
`Full name is different (Institution: {incoming.nama_lengkap} vs Master: {master.nama_lengkap}).`
dihidrasi secara massal menggunakan fungsi SQL `REPLACE` DuckDB, dibersihkan dari titik koma (`;`), lalu ditulis kembali ke PostgreSQL dalam satu transaksi cepat.

---

## 6. Panduan Operasional / Perintah Eksekusi

### 6.1 Menjalankan Matching Mandiri (CSV ➔ Database)
Gunakan skrip `run_matching_csv_to_db.py` untuk memproses file CSV incoming:
```bash
python3 run_matching_csv_to_db.py \
  --file-id batch_grade_b_01 \
  --csv /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji_gradeB.csv \
  --master-parquet /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet \
  --grade 2
```
*Keluaran:* Mengklasifikasikan 3.000 baris dan memasukkan baris `MANUAL_REVIEW` ke database berstatus `PENDING`.

### 6.2 Menjalankan AI Reasoning (Database ➔ Selesai)
Jalankan engine AI reasoning untuk menyelesaikan seluruh antrean `PENDING`:
```bash
python3 run_reasoning_local.py \
  --file-id batch_grade_b_01 \
  --master-parquet /mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet \
  --live
```
*Keluaran:* Memproses seluruh baris manual review dalam waktu ~2.6 detik dan mengubah statusnya menjadi `COMPLETED`.

---

## 7. Metrik Kinerja yang Terverifikasi

* **Model LLM AI Reasoning:** **`gemma3:12b`** (Alami, luwes/tidak kaku, adherence 100%, akurasi 100%)
* **Dataset Master:** 100.000.000 Baris Parquet (3.01 GB)
* **Kecepatan Matching CSV:** 3.000 baris selesai dalam **7.53 detik**
* **Kecepatan Reasoning:** 77 baris manual review selesai dalam **2.66 detik** (~29 baris/detik)
* **Tingkat Akurasi Penjelasan (Faithfulness):** **100.0%** (Terverifikasi bebas halusinasi)
* **Kepatuhan Tanda Baca & Format:** **100.0%** (Bebas titik koma, kapitalisasi baku)
* **Test Suite:** **8 / 8 Tests PASSED** (`pytest tests/test_reasoning.py`)

---

## 8. Panduan Integrasi REST API Langflow (Untuk Backend Eksternal)

Jika tim aplikasi eksternal ingin memicu proses reasoning via HTTP REST API (misal dari backend Node.js, Go, Java, atau Python), gunakan endpoint Langflow berikut:

### 8.1 API 1: Trigger Reasoning Dispatch (Non-Blocking / Asinkron)
* **Metode:** `POST`
* **URL:** `http://<HOST_LANGFLOW>:7860/api/v1/run/reasoning-dispatch?stream=false`
* **Headers:**
  - `Content-Type: application/json`
  - `x-api-key: <LANGFLOW_API_KEY>`

**Body Request:**
```json
{
  "tweaks": {
    "ReasoningDispatch-04344": {
      "payload": "{\"fileId\": \"csv_batch_001\", \"masterParquetPath\": \"/mnt/c/Users/ISGS/Downloads/Data_Test_Syncrono/uji-master-100juta.parquet\", \"llmModel\": \"gemma3:12b\"}"
    }
  }
}
```

**Respon Sukses (HTTP 200 / Body 202 QUEUED):**
```json
{
  "status": "QUEUED",
  "jobId": "reasoning-20260925-e3f2a8b0",
  "fileId": "csv_batch_001",
  "message": "AI Reasoning job successfully queued for processing."
}
```

---

### 8.2 API 2: Polling Status Pekerjaan Reasoning
* **Metode:** `POST`
* **URL:** `http://<HOST_LANGFLOW>:7860/api/v1/run/reasoning-status?stream=false`
* **Headers:**
  - `Content-Type: application/json`
  - `x-api-key: <LANGFLOW_API_KEY>`

**Body Request:**
```json
{
  "tweaks": {
    "ReasoningStatus-246bb": {
      "file_id": "csv_batch_001"
    }
  }
}
```

**Respon Sukses Saat Selesai (`COMPLETED`):**
```json
{
  "found": true,
  "status": "COMPLETED",
  "jobId": "reasoning-20260925-e3f2a8b0",
  "fileId": "csv_batch_001",
  "done": true,
  "stage": "FINISHED",
  "queuedAt": "2026-09-25 07:16:53.400051+00",
  "updatedAt": "2026-09-25 07:17:03.328985+00",
  "error": null,
  "result": {
    "status": "COMPLETED",
    "file_id": "csv_batch_001",
    "total_rows": 77,
    "patterns_generated": 3,
    "cache_hits": 74,
    "llm_hits": 3,
    "duration_seconds": 9.92
  }
}
```
Ketika field `"done": true`, backend eksternal dapat langsung mengabari frontend untuk me-refresh data review operator.

---

## 9. Panduan Tingkat Lanjut & Event-Driven Architecture

Untuk pembahasan teknis mengenai:
1. **Pengujian Skala Masif (>100.000 s/d 1.000.000 data Manual Review).**
2. **Arsitektur Event-Driven Paralel Anti Double-Hit LLM (*Pattern Locking / Row-Claiming*).**

Silakan merujuk pada dokumen arsitektur lanjutan:  
👉 **[`LARGE_SCALE_EVENT_DRIVEN_REASONING.md`](./LARGE_SCALE_EVENT_DRIVEN_REASONING.md)** (Panduan Stress-Testing & Event-Driven Anti Double-Hit)  
👉 **[`PLUGGABLE_REASONING_ARCHITECTURE.md`](./PLUGGABLE_REASONING_ARCHITECTURE.md)** (Panduan Penggantian Sumber Data Master & API Layer FastAPI/Langflow)


