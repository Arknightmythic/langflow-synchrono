-- Tabel referensi: grade, proses, status sinkron, hasil match
--
-- SEEDER — data awal, bukan skema. Memakai ON CONFLICT DO NOTHING, jadi ia
-- MENGISI YANG BELUM ADA dan TIDAK PERNAH MENIMPA yang sudah ada. Aman
-- dijalankan berulang; setelan yang sudah diubah operator lewat API config
-- tidak akan dikembalikan ke nilai bawaan oleh berkas ini.
--
-- Untuk sengaja mengembalikan ke bawaan: hapus barisnya dulu, lalu seed lagi.


INSERT INTO ref_grades (grade_id, grade_code) VALUES
 (1, 'A'), (2, 'B'), (3, 'C'), (4, 'D'), (5, 'E'), (6, 'F')
ON CONFLICT (grade_id) DO NOTHING;

INSERT INTO ref_process (process_id, process_name) VALUES
 (1, 'UPLOADED'), (2, 'GRADED')
ON CONFLICT (process_id) DO NOTHING;

INSERT INTO ref_sync_statuses (sync_status_id, status_code) VALUES
 (1, 'In Progress'), (2, 'Awaiting Action'), (3, 'Completed')
ON CONFLICT (sync_status_id) DO NOTHING;

INSERT INTO ref_match_results (match_result_id, match_result_name) VALUES
 (1, 'AUTO_MATCH'), (2, 'MANUAL_REVIEW'), (3, 'AUTO_UNMATCH'),
 (4, 'MANUAL_MATCH'), (5, 'MANUAL_UNMATCH')
ON CONFLICT (match_result_id) DO NOTHING;
