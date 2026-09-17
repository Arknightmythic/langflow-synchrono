-- Pita skor, label, dan kelayakan sinkron per grade
--
-- SEEDER — data awal, bukan skema. Memakai ON CONFLICT DO NOTHING, jadi ia
-- MENGISI YANG BELUM ADA dan TIDAK PERNAH MENIMPA yang sudah ada. Aman
-- dijalankan berulang; setelan yang sudah diubah operator lewat API config
-- tidak akan dikembalikan ke nilai bawaan oleh berkas ini.
--
-- Untuk sengaja mengembalikan ke bawaan: hapus barisnya dulu, lalu seed lagi.

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
