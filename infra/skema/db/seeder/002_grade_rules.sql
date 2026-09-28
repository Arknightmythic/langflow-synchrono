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

INSERT INTO grade_rules (grade_code, auto_missing_max, auto_score_min,
                         review_missing_count, review_score_min, review_score_max)
VALUES
 (1, 99, 80.001, 99,  0.0,  80.001),
 (2,  1, 85.0,    2, 80.0,  85.0),
 (3, 99, 87.0,   99, 85.0,  87.0),
 (4,  1, 90.0,    2, 85.0,  90.0),
 (5,  1, 81.0,    2, 80.0,  81.0),
 (6,  1, 90.0, NULL, 70.0,  90.0)
ON CONFLICT (grade_code) DO NOTHING;
