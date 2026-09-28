-- Tabel kriteria grade yang bisa dikonfigurasi
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- ============================================================================
-- Kriteria grading A-D sebagai DATA, bukan kode.
--
-- Sebelum tabel ini ada, ambang kelengkapan tertanam di dalam rangkaian `if`
-- pada lib/_grading.py. Mengubah satu angka berarti menyunting kode dan
-- menjalankan ulang container.
--
-- KENAPA HANYA A-D
--
--   Keempatnya berbagi bentuk aturan yang IDENTIK — syarat kolom NIK, ambang
--   kelengkapan per elemen, dan ambang mutu NIK — sehingga muat dalam satu
--   baris tabel. Yang berbeda cuma angkanya.
--
--   Grade E berbentuk lain: bukan ambang persentase melainkan KOMBINASI kolom
--   yang harus ada (nama+tgl+JK, nama+tempat+tgl, dan seterusnya). Grade F
--   bukan aturan sama sekali, melainkan hasil "tidak satu pun di atas
--   terpenuhi". Keduanya sengaja tetap di kode.
--
-- URUTAN EVALUASI PENTING
--
--   Baris dievaluasi menaik menurut `urutan`, dan yang PERTAMA cocok menang.
--   Karena itu aturan yang lebih longgar tidak boleh mendahului yang lebih
--   ketat — kalau B dievaluasi sebelum A, tidak akan ada berkas yang pernah
--   mencapai A. infra/validasi_config.py menolak konfigurasi semacam itu.
-- ============================================================================

CREATE TABLE IF NOT EXISTS grade_criteria (
    grade_id  smallint PRIMARY KEY REFERENCES ref_grades (grade_id),

    -- Urutan evaluasi. Harus unik; kecil dulu.
    urutan    smallint NOT NULL,

    -- Syarat keberadaan KOLOM nik — beda dari ambang kelengkapannya.
    --   'wajib'     kolom nik harus ada       (grade A dan B)
    --   'terlarang' kolom nik harus TIDAK ada (grade C dan D)
    --   'abaikan'   tidak diperiksa
    --
    -- 'terlarang' tidak bisa diwakili angka, dan itulah yang memisahkan C/D
    -- dari A/B: berkas dengan kolom NIK terisi 50% tidak boleh jatuh ke C.
    nik_kolom varchar(10) NOT NULL DEFAULT 'abaikan'
              CHECK (nik_kolom IN ('wajib', 'terlarang', 'abaikan')),

    -- Ambang kelengkapan per elemen, pecahan 0-1. NULL = tidak diperiksa.
    -- Nilai di atas 0 sekaligus mensyaratkan kolomnya ada: kolom yang tidak
    -- ada selalu berkelengkapan 0.
    --
    -- `double precision`, BUKAN `real`. Tipe real hanya 4 byte: 0.6 tersimpan
    -- sebagai 0.6000000238, sedikit lebih besar dari 0.6 yang dihitung Python.
    -- Berkas dengan nama ibu terisi TEPAT 60% (mis. 120.000 dari 200.000 baris)
    -- akan gagal ambang yang tertulis 0.6 — perubahan perilaku yang nyata dan
    -- nyaris mustahil dilacak. Dengan 8 byte, angkanya sama persis di kedua
    -- sisi perbandingan.
    min_nik           double precision CHECK (min_nik           BETWEEN 0 AND 1),
    min_nama          double precision CHECK (min_nama          BETWEEN 0 AND 1),
    min_tempat_lahir  double precision CHECK (min_tempat_lahir  BETWEEN 0 AND 1),
    min_tanggal_lahir double precision CHECK (min_tanggal_lahir BETWEEN 0 AND 1),
    min_jenis_kelamin double precision CHECK (min_jenis_kelamin BETWEEN 0 AND 1),
    min_nama_ibu      double precision CHECK (min_nama_ibu      BETWEEN 0 AND 1),

    -- Proporsi baris ber-NIK tepercaya. NULL = tidak diperiksa.
    min_nik_trusted   double precision CHECK (min_nik_trusted   BETWEEN 0 AND 1),

    aktif      boolean     NOT NULL DEFAULT TRUE,
    diubah_at  timestamptz NOT NULL DEFAULT now(),
    diubah_oleh text
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_grade_criteria_urutan
    ON grade_criteria (urutan);
