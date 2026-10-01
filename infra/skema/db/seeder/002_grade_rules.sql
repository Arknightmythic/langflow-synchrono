-- Ambang pencocokan per grade (dipakai saat matching)
--
-- SEEDER — data awal, bukan skema. Memakai ON CONFLICT DO NOTHING, jadi ia
-- MENGISI YANG BELUM ADA dan TIDAK PERNAH MENIMPA yang sudah ada. Aman
-- dijalankan berulang; setelan yang sudah diubah operator lewat API config
-- tidak akan dikembalikan ke nilai bawaan oleh berkas ini.
--
-- Untuk sengaja mengembalikan ke bawaan: hapus barisnya dulu, lalu seed lagi.


-- Nilainya disalin apa adanya dari sistem yang sudah berjalan.
-- auto_score_min 80.001 pada grade 1 memang ganjil dan memang disengaja:
-- ia membuat pita auto dan pita review tidak tumpang tindih di angka 80.
--
-- bobot / elemen_kosong / bersih_nama (migrasi 006) = rumus skor sistem lama,
-- persis. Bobot berupa DAFTAR [[elemen, persen], ...] karena urutannya ikut
-- menentukan hasil penjumlahan pecahan. Grade 6 tidak dicocokkan: NULL.

INSERT INTO grade_rules (grade_code, auto_missing_max, auto_score_min,
                         review_missing_count, review_score_min, review_score_max,
                         bobot, elemen_kosong, bersih_nama)
VALUES
 (1, 99, 80.001, 99,  0.0,  80.001,
  '[["nama", 100]]',
  '[]',
  '{"titles": false, "patronym": false, "abbreviations": false}'),
 (2,  1, 85.0,    2, 80.0,  85.0,
  '[["nama", 80], ["tempat_lahir", 10], ["nama_ibu", 10]]',
  '["nama", "tempat_lahir", "nama_ibu"]',
  '{"titles": false, "patronym": false, "abbreviations": false}'),
 (3, 99, 87.0,   99, 85.0,  87.0,
  '[["nama", 60], ["tempat_lahir", 20], ["tanggal_lahir", 20]]',
  '[]',
  '{"titles": false, "patronym": false, "abbreviations": false}'),
 (4,  1, 90.0,    2, 85.0,  90.0,
  '[["nama", 60], ["tanggal_lahir", 30], ["tempat_lahir", 5], ["nama_ibu", 5]]',
  '["tanggal_lahir", "tempat_lahir", "nama_ibu"]',
  '{"titles": false, "patronym": false, "abbreviations": false}'),
 (5,  1, 81.0,    2, 80.0,  81.0,
  '[["nama", 50], ["wilayah", 30], ["nama_ibu", 10], ["tanggal_lahir", 10]]',
  '["nama", "tanggal_lahir", "wilayah", "nama_ibu"]',
  '{"titles": false, "patronym": false, "abbreviations": false}'),
 (6,  1, 90.0, NULL, 70.0,  90.0, NULL, NULL, NULL)
ON CONFLICT (grade_code) DO NOTHING;
