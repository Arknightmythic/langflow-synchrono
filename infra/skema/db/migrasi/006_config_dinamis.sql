-- Konfigurasi dinamis: bobot, elemen kosong, pembersihan nama, nilai global,
-- riwayat perubahan, dan versi konfigurasi
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- ============================================================================
-- Sebelum migrasi ini, bobot skor matching, daftar elemen yang dihitung
-- "kosong", kombinasi grade E, dan bobot skor mutu grading tertanam di kode.
-- Mengubahnya berarti menyunting kode dan deploy ulang, dan nilainya tidak
-- bisa dilihat dari luar tanpa membaca kode.
--
-- Sekarang semuanya DATA, dibaca dan diubah lewat API config.
--
-- NILAI YANG DIISIKAN DI SINI = PERSIS YANG TERTANAM DI KODE SEBELUMNYA, jadi
-- menerapkan migrasi ini tidak mengubah satu hasil pun. Mengubah ke nilai lain
-- adalah langkah terpisah lewat API, tercatat di config_riwayat.
-- ============================================================================


-- ─── Per grade: kolom baru di grade_rules ───────────────────────────────────
--
-- bobot          [[elemen, persen], ...] — DAFTAR, bukan objek. JSONB mengurutkan
--                ulang kunci objek, padahal urutan bobot ikut menentukan hasil
--                penjumlahan pecahan (0.6 + 0.3 tidak persis 0.9).
-- elemen_kosong  [elemen, ...] yang dihitung sebagai missing_count.
-- bersih_nama    {"titles": bool, "patronym": bool, "abbreviations": bool}
--
-- NULL pada ketiganya = bawaan di kode (lib/_shared.py), sama dengan nilai
-- yang diisikan di bawah.

ALTER TABLE grade_rules
    ADD COLUMN IF NOT EXISTS bobot         jsonb,
    ADD COLUMN IF NOT EXISTS elemen_kosong jsonb,
    ADD COLUMN IF NOT EXISTS bersih_nama   jsonb,
    ADD COLUMN IF NOT EXISTS diubah_at     timestamptz,
    ADD COLUMN IF NOT EXISTS diubah_oleh   text;

-- Basis data yang sudah berisi: isi dengan nilai yang selama ini berlaku.
-- Pada pemasangan baru grade_rules masih kosong di titik ini; seeder
-- 002_grade_rules.sql yang mengisinya, dengan nilai yang sama.

UPDATE grade_rules SET bobot = '[["nama", 100]]'
 WHERE grade_code = 1 AND bobot IS NULL;
UPDATE grade_rules SET bobot = '[["nama", 80], ["tempat_lahir", 10], ["nama_ibu", 10]]'
 WHERE grade_code = 2 AND bobot IS NULL;
UPDATE grade_rules SET bobot = '[["nama", 60], ["tempat_lahir", 20], ["tanggal_lahir", 20]]'
 WHERE grade_code = 3 AND bobot IS NULL;
UPDATE grade_rules SET bobot = '[["nama", 60], ["tanggal_lahir", 30], ["tempat_lahir", 5], ["nama_ibu", 5]]'
 WHERE grade_code = 4 AND bobot IS NULL;
UPDATE grade_rules SET bobot = '[["nama", 50], ["wilayah", 30], ["nama_ibu", 10], ["tanggal_lahir", 10]]'
 WHERE grade_code = 5 AND bobot IS NULL;

UPDATE grade_rules SET elemen_kosong = '[]'
 WHERE grade_code IN (1, 3) AND elemen_kosong IS NULL;
UPDATE grade_rules SET elemen_kosong = '["nama", "tempat_lahir", "nama_ibu"]'
 WHERE grade_code = 2 AND elemen_kosong IS NULL;
UPDATE grade_rules SET elemen_kosong = '["tanggal_lahir", "tempat_lahir", "nama_ibu"]'
 WHERE grade_code = 4 AND elemen_kosong IS NULL;
UPDATE grade_rules SET elemen_kosong = '["nama", "tanggal_lahir", "wilayah", "nama_ibu"]'
 WHERE grade_code = 5 AND elemen_kosong IS NULL;

UPDATE grade_rules
   SET bersih_nama = '{"titles": false, "patronym": false, "abbreviations": false}'
 WHERE grade_code BETWEEN 1 AND 5 AND bersih_nama IS NULL;


-- ─── Global: satu baris per kunci ───────────────────────────────────────────
--
--   grading.scoreWeights        {"kelengkapan": 0.6, "nik_tepercaya": 0.4}
--   grading.gradeECombinations  [["nama", "tanggal_lahir", "jenis_kelamin"], ...]
--   matching.conflictEpsilon    0
--   matching.contradictionJw    0.8
--
-- SENGAJA KOSONG setelah migrasi. Kunci yang tidak ada memakai env (dua kunci
-- matching: MATCHING_CONFLICT_EPSILON, MATCHING_KONTRA_JW — seperti sebelum
-- tabel ini ada) atau bawaan di kode. Mengisinya di sini akan diam-diam
-- mengalahkan env yang mungkin sudah disetel di server.

CREATE TABLE IF NOT EXISTS engine_config (
    kunci       text PRIMARY KEY,
    nilai       jsonb       NOT NULL,
    diubah_at   timestamptz NOT NULL DEFAULT now(),
    diubah_oleh text
);


-- ─── Riwayat perubahan lewat API ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS config_riwayat (
    id        bigserial PRIMARY KEY,
    waktu     timestamptz NOT NULL DEFAULT now(),
    oleh      text,
    -- 'grade' (satu grade) atau 'global'
    cakupan   varchar(10) NOT NULL CHECK (cakupan IN ('grade', 'global')),
    grade_id  smallint,
    -- [{"section", "field", "from", "to"}, ...]
    perubahan jsonb       NOT NULL,
    -- versi konfigurasi SESUDAH perubahan ini (config_versi.versi)
    versi     varchar(16)
);

CREATE INDEX IF NOT EXISTS idx_config_riwayat_grade
    ON config_riwayat (grade_id, id DESC);


-- ─── Versi konfigurasi yang pernah dipakai ──────────────────────────────────
--
-- `versi` = 12 heksa pertama SHA-256 dari isi konfigurasi yang menentukan
-- hasil (kriteria, pita, aturan matching, kueri blocking, global). Setiap job
-- grading & matching mencatat versinya; isinya disimpan di sini sekali saja.

CREATE TABLE IF NOT EXISTS config_versi (
    versi           varchar(16) PRIMARY KEY,
    isi             jsonb       NOT NULL,
    pertama_dipakai timestamptz NOT NULL DEFAULT now()
);
