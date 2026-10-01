-- Kueri blocking membawa SEMUA elemen yang bisa diberi bobot
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- ============================================================================
-- Skor Pass 3 dihitung dari kolom yang DIKELUARKAN kueri blocking. Kueri yang
-- disalin dari sistem lama hanya membawa kolom yang dipakai rumus lama grade
-- itu — grade C tidak membawa nama ibu, grade A hanya nama. Akibatnya bobot
-- yang bisa dipasang lewat API (migrasi 006) terbatas oleh kueri: bobot nama
-- ibu di grade C, misalnya, tidak bisa dihitung sama sekali.
--
-- Migrasi ini MENAMBAH kolom yang kurang ke daftar SELECT setiap kueri:
-- `i.<elemen>_clean, m.<elemen>_master_clean` disisipkan tepat sebelum setiap
-- `FROM incoming_df i` (grade D punya empat cabang; keempatnya disisipi urutan
-- yang sama, jadi UNION ALL-nya tetap sejajar).
--
-- YANG TIDAK BERUBAH: syarat JOIN, jadi kandidat yang ditemukan persis sama.
-- Hanya kolom keluarannya yang bertambah. Nilai kolom tambahan bergantung pada
-- baris incoming & master yang sama, sehingga SELECT DISTINCT grade D/E juga
-- tidak menggabung atau memecah pasangan yang sebelumnya berbeda.
--
-- Per elemen, dan HANYA kalau kolom master-nya belum disebut di kueri itu —
-- jadi aman untuk kueri yang teksnya sedikit berbeda dari repo (grade A/B di
-- server 192.168.2.107 tanpa penjaga `nik_trusted`), dan aman dijalankan pada
-- kueri yang sudah membawa kolomnya. Berkas seed `matching_queries.json`
-- memuat hasil yang sama untuk pemasangan baru.
-- ============================================================================

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.tempat_lahir_clean,
    m.tempat_lahir_master_clean\1', 'g')
 WHERE position('tempat_lahir_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.tanggal_lahir_clean,
    m.tanggal_lahir_master_clean\1', 'g')
 WHERE position('tanggal_lahir_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.jenis_kelamin_clean,
    m.jenis_kelamin_master_clean\1', 'g')
 WHERE position('jenis_kelamin_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.nama_ibu_clean,
    m.nama_ibu_master_clean\1', 'g')
 WHERE position('nama_ibu_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.provinsi_clean,
    m.provinsi_master_clean\1', 'g')
 WHERE position('provinsi_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.kabupaten_clean,
    m.kabupaten_master_clean\1', 'g')
 WHERE position('kabupaten_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.kecamatan_clean,
    m.kecamatan_master_clean\1', 'g')
 WHERE position('kecamatan_master_clean' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(matching_query, '(\n[ \t]*FROM incoming_df i)', ',
    i.kelurahan_clean,
    m.kelurahan_master_clean\1', 'g')
 WHERE position('kelurahan_master_clean' in matching_query) = 0;
