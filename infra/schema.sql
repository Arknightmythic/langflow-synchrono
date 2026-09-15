-- ============================================================================
-- Skema PostgreSQL untuk service matching Synchrono (berdiri sendiri)
--
-- Dipakai oleh flow Langflow. Backend Synchrono memanggil flow itu lewat API.
--
-- PERBEDAAN PENTING DARI VERSI STARROCKS:
--
--   `institution` dan `manual_matches` punya UNIQUE (file_id, id_incoming).
--
--   Di StarRocks constraint ini tidak ada, dan matching ulang selalu MENAMBAH
--   baris alih-alih menimpa. Akibatnya tabel institution di sana berisi 22,9
--   juta baris untuk hanya 14,2 juta record unik — 34% duplikat, dengan satu
--   file mencapai rasio 15x. Semua COUNT dan persentase jadi menggelembung.
--
--   Di PostgreSQL hal itu bisa dicegah di level skema. Penulisan memakai
--   ON CONFLICT DO UPDATE (upsert), sehingga matching ulang MEMPERBARUI baris
--   yang sama, bukan menumpuknya.
-- ============================================================================

-- ─── Tabel referensi ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ref_grades (
    grade_id    smallint PRIMARY KEY,
    grade_code  varchar(10) NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_process (
    process_id   smallint PRIMARY KEY,
    process_name varchar(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_sync_statuses (
    sync_status_id smallint PRIMARY KEY,
    status_code    varchar(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS ref_match_results (
    match_result_id   smallint PRIMARY KEY,
    match_result_name varchar(20) NOT NULL
);

-- ─── Konfigurasi matching (DATA, bukan kode) ────────────────────────────────
-- Perilaku matching diubah lewat UPDATE di dua tabel ini, tanpa deploy ulang.

CREATE TABLE IF NOT EXISTS grade_rules (
    grade_code           smallint PRIMARY KEY,
    auto_missing_max     integer,
    auto_score_min       double precision,
    review_missing_count integer,
    review_score_min     double precision,
    review_score_max     double precision
);

-- matching_query adalah SQL DuckDB yang merujuk view `incoming_df` dan
-- `master_df`. Nama itu dibuat oleh node PrepareIncoming / PrepareMaster.
CREATE TABLE IF NOT EXISTS matching_queries (
    grade_code     smallint PRIMARY KEY,
    matching_query text NOT NULL
);

-- ─── Master (registri referensi) ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS master (
    id              bigserial PRIMARY KEY,
    nik             varchar(32),
    nama_lengkap    text,
    tempat_lahir    text,
    tanggal_lahir   date,
    jenis_kelamin   varchar(16),
    nama_ibu        text,
    status_kematian varchar(16),
    provinsi        text,
    kabupaten       text,
    kecamatan       text,
    kelurahan       text
);

CREATE INDEX IF NOT EXISTS idx_master_nik ON master (nik);

-- ─── Registri file ──────────────────────────────────────────────────────────
-- Backend Synchrono yang mengisi tabel ini saat user mengunggah file.
-- Service matching hanya MEMBACA-nya.

CREATE TABLE IF NOT EXISTS uploaded_files (
    file_id              varchar(64) PRIMARY KEY,
    original_filename    text,
    institution_name     text,
    -- Path S3 lengkap, mis. s3://synchrono/curated/xxx.parquet
    parquet_path         text NOT NULL,
    upload_timestamp     timestamptz DEFAULT now(),
    row_count            integer,
    grade                smallint REFERENCES ref_grades (grade_id),
    processing_status    smallint REFERENCES ref_process (process_id),
    sync_status          smallint REFERENCES ref_sync_statuses (sync_status_id),
    is_sync              boolean DEFAULT false,
    matching_task_status varchar(20) DEFAULT 'IDLE',
    matching_time_ms     numeric,
    is_custom_ready      boolean DEFAULT false
);

-- ─── Hasil matching ─────────────────────────────────────────────────────────
-- Menyimpan VONIS, bukan datanya. Nilai field aslinya tetap di parquet dan
-- dibaca saat dibutuhkan — menyalin 200 ribu baris x 12 kolom per file ke sini
-- akan meledakkan ukuran tabel tanpa manfaat.

-- Tanpa surrogate key: (file_id, id_incoming) SUDAH merupakan kunci alaminya.
-- Selain lebih jujur secara model, ini juga praktis — DuckDB menulis ke
-- PostgreSQL lewat COPY dan mengabaikan daftar kolom, sehingga kolom
-- `bigserial` maupun kolom ber-DEFAULT akan terkirim sebagai NULL dan ditolak.
-- Karena itu SEMUA kolom di tabel ini diisi eksplisit oleh node 7.
CREATE TABLE IF NOT EXISTS institution (
    file_id       varchar(64) NOT NULL,
    id_incoming   text        NOT NULL,
    nik_master    varchar(32),
    match_score   double precision,
    -- 1 AUTO_MATCH, 2 MANUAL_REVIEW, 3 AUTO_UNMATCH,
    -- 4 MANUAL_MATCH, 5 MANUAL_UNMATCH (4 & 5 hanya dari aksi manusia)
    match_result  smallint REFERENCES ref_match_results (match_result_id),
    upload_date   timestamptz,
    inserted_date timestamptz,
    PRIMARY KEY (file_id, id_incoming)
);

CREATE INDEX IF NOT EXISTS idx_institution_file   ON institution (file_id);
CREATE INDEX IF NOT EXISTS idx_institution_result ON institution (file_id, match_result);

-- ─── Antrean tinjauan manusia ───────────────────────────────────────────────
-- Hanya baris ber-match_result = 2. Field incoming SENGAJA disalin ke sini
-- supaya pembandingan incoming vs master tidak perlu membaca parquet per baris.

CREATE TABLE IF NOT EXISTS manual_matches (
    file_id                varchar(64) NOT NULL,
    id_incoming            text        NOT NULL,
    nama_incoming          text,
    tempat_lahir_incoming  text,
    tanggal_lahir_incoming text,
    jenis_kelamin_incoming text,
    nama_ibu_incoming      text,
    reason                 text,
    pattern_name           varchar(255),
    reasoning_source       varchar(20),
    reasoning_status       varchar(30) NOT NULL,
    PRIMARY KEY (file_id, id_incoming)
);

CREATE INDEX IF NOT EXISTS idx_manual_file   ON manual_matches (file_id);
CREATE INDEX IF NOT EXISTS idx_manual_status ON manual_matches (file_id, reasoning_status);

-- ============================================================================
-- CATATAN: kolom nik_incoming dan area_incoming dari skema StarRocks SENGAJA
-- TIDAK dibawa ke sini. Keduanya selalu diisi NULL oleh kode matching
-- (nik_incoming 90% NULL, area_incoming 100% NULL dari 933 ribu baris), dan
-- keberadaannya justru menyesatkan: menggoda orang menulis
-- JOIN master ON manual_matches.nik_incoming = master.nik yang hampir selalu
-- mengembalikan kosong. Jalur yang benar lewat institution.nik_master.
-- ============================================================================
