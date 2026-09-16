-- ============================================================================
-- Skema tambahan untuk service GRADING (menyusul schema.sql milik matching)
--
-- Dua tabel:
--   grading_jobs -- status job; inilah yang dibaca API polling
--   grade_bands  -- pita skor & kelayakan sinkronisasi per grade (DATA, bukan
--                   kode — bisa disetel tanpa deploy ulang)
-- ============================================================================

-- ─── Antrean & riwayat job grading ──────────────────────────────────────────
--
-- SATU BARIS PER PERMINTAAN, bukan per berkas. Kalau tombol "Coba Lagi
-- Evaluasi" ditekan, baris baru ditambahkan dan yang lama tetap tersimpan
-- sebagai riwayat. API status selalu mengambil yang TERBARU untuk file_id itu.
--
-- `heartbeat_at` adalah kunci ketahanannya: pekerja grading berjalan sebagai
-- thread di dalam container Langflow, jadi container yang restart di tengah
-- proses akan meninggalkan job berstatus RUNNING tanpa ada yang mengerjakannya.
-- Detak yang berhenti membuat keadaan itu bisa DIKENALI (lihat panen_mangkrak
-- di lib/_jobs.py) alih-alih menggantung selamanya.

CREATE TABLE IF NOT EXISTS grading_jobs (
    job_id              text PRIMARY KEY,
    file_id             text NOT NULL,

    -- QUEUED -> RUNNING -> COMPLETED | FAILED
    status              text NOT NULL,
    -- Tahap yang sedang berjalan, mis. "G5 score & grade". Untuk progress di UI.
    stage               text,

    filename            text,
    s3_bucket           text,
    s3_endpoint         text,
    csv_key             text,
    parquet_key         text,
    enriched_key        text,
    parquet_size_bytes  bigint,
    row_count           integer,

    institution_id      text,
    institution_name    text,

    -- Webhook ke portal Synchrono. Kosong = backend memilih polling saja.
    callback_url        text,
    callback_token      text,
    -- PENDING | SENT | FAILED | SKIPPED
    callback_status     text,
    callback_attempts   integer DEFAULT 0,
    callback_error      text,

    -- Muatan callback utuh. Disimpan supaya API polling bisa mengembalikan
    -- hasil yang SAMA PERSIS dengan yang dikirim lewat webhook — backend tidak
    -- perlu menangani dua bentuk data yang berbeda.
    result              jsonb,
    error               text,

    grading_duration_ms integer,
    created_at          timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz,
    finished_at         timestamptz,
    heartbeat_at        timestamptz
);

CREATE INDEX IF NOT EXISTS idx_grading_jobs_file
    ON grading_jobs (file_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_grading_jobs_status
    ON grading_jobs (status, heartbeat_at);

-- ─── Pita skor per grade ────────────────────────────────────────────────────
--
-- Grade (A-F) ditentukan aturan STRUKTURAL di lib/_grading.py; tabel ini yang
-- menerjemahkannya jadi angka 0-100, label keparahan, dan kelayakan sinkron.
-- Memisahkannya ke sini berarti kebijakan mutu bisa diubah lewat UPDATE.
--
-- `can_proceed` MENGIKUTI TABEL ATURAN INDOSAT, bukan grading-engine-integration-
-- spec.md bagian 7. Keduanya berbeda untuk grade D dan E: spec menyatakan
-- keduanya tidak layak sinkron, sementara tabel aturan menunjukkan keduanya
-- punya kolom Automatis / Manual / Tidak Padan yang terisi penuh — artinya
-- memang diproses matching.
--
-- Kenyataan produksi memihak tabel aturan: berkas 825fc484 bergrade D dan
-- dicocokkan penuh, 200.020 baris (115.551 auto-match). Menyetelnya FALSE akan
-- membuat backend memblokir berkas yang selama ini justru diproses.
--
-- Hanya F yang tidak boleh lanjut, dan bukan karena mutunya: grade F berarti
-- kolomnya tidak dikenali, jadi matching belum punya pemetaan untuk dipakai.

CREATE TABLE IF NOT EXISTS grade_bands (
    grade_id             smallint PRIMARY KEY REFERENCES ref_grades (grade_id),
    grade_letter         varchar(2)  NOT NULL,
    score_min            smallint    NOT NULL,
    score_max            smallint    NOT NULL,
    severity_label       text        NOT NULL,
    can_proceed          boolean     NOT NULL,
    criteria_description text
);

INSERT INTO grade_bands (grade_id, grade_letter, score_min, score_max,
                         severity_label, can_proceed, criteria_description)
VALUES
 (1, 'A',  90, 100, 'Sangat Baik',    TRUE,
  'Kualitas Data Sangat Baik. Seluruh 6 elemen skema wajib terisi lengkap dan 100% NIK valid serta tepercaya.'),
 (2, 'B',  70,  89, 'Baik',           TRUE,
  'Kualitas Data Baik. Berkas memenuhi ambang batas sinkronisasi. Baris dengan NIK anomali ditandai otomatis agar tidak memblokir proses sinkronisasi.'),
 (3, 'C',  50,  69, 'Cukup',          TRUE,
  '5 Elemen Terpenuhi tanpa kolom NIK. Berkas dapat diproses untuk pencocokan probabilistik berbasis nama, tempat/tanggal lahir, dan nama ibu.'),
 (4, 'D',  30,  49, 'Kurang',         TRUE,
  '5 elemen individu tanpa kolom NIK, dengan kelengkapan di bawah 100%. Tetap dapat dicocokkan; ambang pencocokannya paling ketat di antara semua grade (skor minimal 90% untuk otomatis).'),
 (5, 'E',  10,  29, 'Sangat Kurang',  TRUE,
  'Hanya kombinasi minimal elemen identitas yang tersedia (tiga elemen). Tetap dapat dicocokkan secara probabilistik, dengan tinjauan manusia yang lebih banyak.'),
 (6, 'F',   0,   9, 'Kritis',         FALSE,
  'Kolom berkas tidak dikenali sebagai elemen kependudukan. Memerlukan pemetaan kolom kustom sebelum dapat dicocokkan.')
ON CONFLICT (grade_id) DO NOTHING;

-- Baris di atas hanya berlaku untuk pemasangan baru: ON CONFLICT DO NOTHING
-- sengaja dipakai supaya penyetelan yang sudah dilakukan lewat UPDATE tidak
-- tertimpa saat apply_schema.py dijalankan ulang.
--
-- Karena itu koreksi di bawah ditulis sebagai UPDATE tersendiri — tanpa ini,
-- basis data yang sudah terlanjur menyimpan nilai lama tidak akan pernah ikut
-- terperbaiki.
--
-- `criteria_description` HARUS ikut diperbarui, bukan hanya `can_proceed`.
-- Teks itu dikirim apa adanya ke portal sebagai `summary.criteriaDescription`;
-- kalau hanya can_proceed yang berubah, muatan callback akan menyatakan berkas
-- boleh disinkronkan sekaligus menerangkan bahwa ia belum layak disinkronkan.

UPDATE grade_bands
   SET can_proceed = TRUE,
       criteria_description = '5 elemen individu tanpa kolom NIK, dengan kelengkapan di bawah 100%. Tetap dapat dicocokkan; ambang pencocokannya paling ketat di antara semua grade (skor minimal 90% untuk otomatis).'
 WHERE grade_id = 4;

UPDATE grade_bands
   SET can_proceed = TRUE,
       criteria_description = 'Hanya kombinasi minimal elemen identitas yang tersedia (tiga elemen). Tetap dapat dicocokkan secara probabilistik, dengan tinjauan manusia yang lebih banyak.'
 WHERE grade_id = 5;

UPDATE grade_bands
   SET can_proceed = FALSE,
       criteria_description = 'Kolom berkas tidak dikenali sebagai elemen kependudukan. Memerlukan pemetaan kolom kustom sebelum dapat dicocokkan.'
 WHERE grade_id = 6;
