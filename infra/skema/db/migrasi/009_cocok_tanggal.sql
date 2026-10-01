-- Cara menilai tanggal lahir di skor matching, per grade
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- ============================================================================
-- `matching.dateMatch` di API config:
--
--   similarity  Jaro-Winkler atas teks yyyy-mm-dd — engine lama. Beda tahun
--               tetap bernilai ±0,9.
--   exact       1 kalau tanggalnya sama persis, selain itu 0.
--
-- Diukur dengan data uji ber-kunci jawaban (test-data-csv/uji-master-ae):
-- hampir semua AUTO yang salah orang di grade C/D/E berasal dari orang LAIN
-- bernama sama yang lahir di hari & bulan yang sama tapi tahun berbeda —
-- blocking C/D/E sudah mensyaratkan hari & bulan sama, dan nilai teks tanggal
-- nyaris tidak menghukum beda tahun.
--
-- NULL = bawaan (similarity), jadi menambah kolom ini tidak mengubah apa pun.
-- Nilai `exact` dipasang lewat API, tercatat di config_riwayat.
-- ============================================================================

ALTER TABLE grade_rules
    ADD COLUMN IF NOT EXISTS cocok_tanggal varchar(12)
        CHECK (cocok_tanggal IN ('similarity', 'exact'));
