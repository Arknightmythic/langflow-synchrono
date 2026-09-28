-- Kriteria kelengkapan & mutu NIK per grade (A-D, bisa diedit lewat API)
--
-- SEEDER — data awal, bukan skema. Memakai ON CONFLICT DO NOTHING, jadi ia
-- MENGISI YANG BELUM ADA dan TIDAK PERNAH MENIMPA yang sudah ada. Aman
-- dijalankan berulang; setelan yang sudah diubah operator lewat API config
-- tidak akan dikembalikan ke nilai bawaan oleh berkas ini.
--
-- Untuk sengaja mengembalikan ke bawaan: hapus barisnya dulu, lalu seed lagi.


-- ─── Nilai awal = persis yang tertanam di kode sebelumnya ───────────────────
--
-- min_nik sengaja NULL untuk B, C, dan D. Grade B tidak pernah memeriksa
-- kelengkapan kolom NIK-nya, hanya proporsi yang tepercaya; C dan D justru
-- mensyaratkan kolom itu tidak ada.

INSERT INTO grade_criteria (
    grade_id, urutan, nik_kolom,
    min_nik, min_nama, min_tempat_lahir, min_tanggal_lahir,
    min_jenis_kelamin, min_nama_ibu, min_nik_trusted
) VALUES
 (1, 1, 'wajib',     1.0,  1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
 (2, 2, 'wajib',     NULL, 1.0, 0.7, 0.7, 0.7, 0.6, 0.7),
 (3, 3, 'terlarang', NULL, 1.0, 1.0, 1.0, 1.0, 1.0, NULL),
 (4, 4, 'terlarang', NULL, 1.0, 0.7, 0.7, 0.7, 0.6, NULL)
ON CONFLICT (grade_id) DO NOTHING;
