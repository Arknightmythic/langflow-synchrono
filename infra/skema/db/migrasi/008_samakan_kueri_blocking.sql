-- Samakan kueri blocking lama di basis data dengan versi repo
--
-- MIGRASI — dijalankan SEKALI, lalu dicatat di `schema_migrations`.
-- Jangan disunting setelah pernah diterapkan di mana pun: migrate.py
-- membandingkan checksum dan akan memperingatkan kalau berubah. Perubahan
-- skema berikutnya ditulis sebagai berkas migrasi BARU bernomor lebih besar.

-- ============================================================================
-- Seeder `matching_queries` memakai ON CONFLICT DO NOTHING, jadi perbaikan kueri
-- di `matching_queries.json` tidak pernah sampai ke basis data yang sudah terisi
-- sebelumnya. Terbaca 1 Okt 2026 di server 192.168.2.107, dua kueri masih versi
-- sistem lama:
--
--   Grade A & B  `ON i.nik = m.nik` — NIK TIDAK tepercaya (tanggal lahir di NIK
--                tidak cocok, panjang salah, rusak Excel, ...) ikut dipakai
--                sebagai kunci. Repo: `ON CASE WHEN i.nik_trusted THEN i.nik END
--                = m.nik`. Pada data uji server hasilnya identik (tidak satu baris
--                pun berubah); penjaga ini mencegah NIK rusak menunjuk orang lain.
--
--   Grade E      `ON i.status_hidup_clean = m.status_hidup_master_clean` —
--                berkas grade E hampir tidak pernah punya kolom status hidup,
--                sehingga syarat ini TIDAK PERNAH terpenuhi dan Pass 3 grade E tidak
--                mendapat satu kandidat pun. Terukur pada 52.493 baris: AUTO 30.137
--                (hanya Pass 1/2) vs 47.818 dengan versi repo, yang tidak
--                mensyaratkan status hidup kalau berkasnya tidak memuatnya.
--
-- Penggantiannya terarah: hanya syarat itu, hanya kalau penjaganya belum ada,
-- dan spasi aslinya dipertahankan — hasilnya identik dengan teks repo. Pada
-- pemasangan baru (kueri dari seed repo) migrasi ini tidak mengubah apa pun.
-- ============================================================================

UPDATE matching_queries
   SET matching_query = regexp_replace(
         matching_query,
         'ON(\s+)i\.nik\s*=\s*m\.nik\y',
         'ON\1CASE WHEN i.nik_trusted THEN i.nik END = m.nik')
 WHERE grade_code IN (1, 2)
   AND position('nik_trusted' in matching_query) = 0;

UPDATE matching_queries
   SET matching_query = regexp_replace(
         matching_query,
         'ON(\s+)i\.status_hidup_clean\s*=(\s*)m\.status_hidup_master_clean',
         'ON\1(i.status_hidup_clean IS NULL OR i.status_hidup_clean =\2m.status_hidup_master_clean)')
 WHERE grade_code = 5
   AND position('status_hidup_clean IS NULL' in matching_query) = 0;
