-- Koreksi can_proceed & deskripsi untuk grade D, E, F
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- Kenapa ini migrasi, bukan bagian seeder.
--
-- Seeder memakai ON CONFLICT DO NOTHING supaya tidak pernah menimpa setelan
-- operator. Akibatnya, basis data yang terlanjur menyimpan nilai LAMA tidak
-- akan pernah ikut terperbaiki oleh seeder. Koreksi nilai yang sudah terlanjur
-- ada adalah perubahan sekali-jalan — dan itulah definisi migrasi.
--
-- Pada pemasangan baru berkas ini tidak berpengaruh apa-apa: grade_bands masih
-- kosong saat migrasi berjalan, dan seeder sudah memuat nilai yang benar.
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
