-- seed: grade_bands
INSERT INTO ${SERVICE}grade_bands
 (grade_id, grade_letter, score_min, score_max, severity_label, can_proceed,
  criteria_description, created_date, created_by) VALUES
 (1, 'A', 90, 100, 'Sangat Baik', TRUE,
  'Kualitas Data Sangat Baik. Seluruh 6 elemen skema wajib terisi lengkap dan 100% NIK valid serta tepercaya.', ${CREATED_DATE}, ${CREATED_BY}),
 (2, 'B', 70, 89, 'Baik', TRUE,
  'Kualitas Data Baik. Berkas memenuhi ambang batas sinkronisasi. Baris dengan NIK anomali ditandai otomatis agar tidak memblokir proses sinkronisasi.', ${CREATED_DATE}, ${CREATED_BY}),
 (3, 'C', 50, 69, 'Cukup', TRUE,
  '5 Elemen Terpenuhi tanpa kolom NIK. Berkas dapat diproses untuk pencocokan probabilistik berbasis nama, tempat/tanggal lahir, dan nama ibu.', ${CREATED_DATE}, ${CREATED_BY}),
 (4, 'D', 30, 49, 'Kurang', TRUE,
  '5 elemen individu tanpa kolom NIK, dengan kelengkapan di bawah 100%. Tetap dapat dicocokkan; ambang pencocokannya paling ketat di antara semua grade (skor minimal 90% untuk otomatis).', ${CREATED_DATE}, ${CREATED_BY}),
 (5, 'E', 10, 29, 'Sangat Kurang', TRUE,
  'Hanya kombinasi minimal elemen identitas yang tersedia (tiga elemen). Tetap dapat dicocokkan secara probabilistik, dengan tinjauan manusia yang lebih banyak.', ${CREATED_DATE}, ${CREATED_BY}),
 (6, 'F', 0, 9, 'Kritis', FALSE,
  'Kolom berkas tidak dikenali sebagai elemen kependudukan. Memerlukan pemetaan kolom kustom sebelum dapat dicocokkan.', ${CREATED_DATE}, ${CREATED_BY});

-- seed: grade_criteria
INSERT INTO ${SERVICE}grade_criteria
 (grade_id, eval_order, nik_column, min_nik, min_nama, min_tempat_lahir, min_tanggal_lahir,
  min_jenis_kelamin, min_nama_ibu, min_nik_trusted, active, updated_at, updated_by,
  created_date, created_by) VALUES
 (1, 1, 'required',  1.0,  1.0, 1.0, 1.0, 1.0, 1.0, 1.0,  TRUE, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}),
 (2, 2, 'required',  NULL, 1.0, 0.7, 0.7, 0.7, 0.6, 0.7,  TRUE, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}),
 (3, 3, 'forbidden', NULL, 1.0, 1.0, 1.0, 1.0, 1.0, NULL, TRUE, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}),
 (4, 4, 'forbidden', NULL, 1.0, 0.7, 0.7, 0.7, 0.6, NULL, TRUE, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY});

-- seed: grade_rules
INSERT INTO ${SERVICE}grade_rules
 (grade_code, auto_missing_max, auto_score_min, review_missing_count, review_score_min,
  review_score_max, weights, missing_elements, name_cleaning, date_match, updated_at, updated_by,
  created_date, created_by)
SELECT 1, 99, 80.001, 99, 0.0, 80.001, parse_json('[["nama", 100]]'), parse_json('[]'),
       parse_json('{"titles": false, "patronym": false, "abbreviations": false}'), NULL, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 2, 1, 85.0, 2, 80.0, 85.0,
       parse_json('[["nama", 80], ["tempat_lahir", 10], ["nama_ibu", 10]]'),
       parse_json('["nama", "tempat_lahir", "nama_ibu"]'),
       parse_json('{"titles": false, "patronym": false, "abbreviations": false}'), NULL, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 3, 99, 87.0, 99, 85.0, 87.0,
       parse_json('[["nama", 60], ["tempat_lahir", 20], ["tanggal_lahir", 20]]'), parse_json('[]'),
       parse_json('{"titles": false, "patronym": false, "abbreviations": false}'), NULL, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 4, 1, 90.0, 2, 85.0, 90.0,
       parse_json('[["nama", 60], ["tanggal_lahir", 30], ["tempat_lahir", 5], ["nama_ibu", 5]]'),
       parse_json('["tanggal_lahir", "tempat_lahir", "nama_ibu"]'),
       parse_json('{"titles": false, "patronym": false, "abbreviations": false}'), NULL, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 5, 1, 81.0, 2, 80.0, 81.0,
       parse_json('[["nama", 50], ["wilayah", 30], ["nama_ibu", 10], ["tanggal_lahir", 10]]'),
       parse_json('["nama", "tanggal_lahir", "wilayah", "nama_ibu"]'),
       parse_json('{"titles": false, "patronym": false, "abbreviations": false}'), NULL, NULL, NULL, ${CREATED_DATE}, ${CREATED_BY};

-- seed: matching_queries
INSERT INTO ${SERVICE}matching_queries (grade_code, blocking, description, created_date, created_by)
SELECT 1, parse_json('{"join": "left", "branches": ["(CASE WHEN i.nik_trusted THEN i.nik END) = m.nik"]}'),
       'NIK berkas (hanya yang tepercaya) = NIK master', ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 2, parse_json('{"join": "left", "branches": ["(CASE WHEN i.nik_trusted THEN i.nik END) = m.nik"]}'),
       'NIK berkas (hanya yang tepercaya) = NIK master', ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 3, parse_json('{"join": "left", "branches": ["i.sex_c = m.sex_c AND i.dob_md = m.dob_md AND left(i.{name}, 3) = left(m.{name}, 3)"]}'),
       'jenis kelamin, hari & bulan lahir, 3 huruf awal nama', ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 4, parse_json('{"join": "inner", "distinct": true, "branches": [
  "i.sex_c IS NULL AND i.pob_c IS NOT NULL AND i.pob_c <> '''' AND m.pob_c IS NOT NULL AND m.pob_c <> '''' AND left(i.pob_c, 3) = left(m.pob_c, 3) AND i.dob_md = m.dob_md",
  "i.pob_c IS NULL AND i.sex_c = m.sex_c AND i.{name} IS NOT NULL AND i.{name} <> '''' AND m.{name} IS NOT NULL AND m.{name} <> '''' AND left(i.{name}, 3) = left(m.{name}, 3) AND i.dob_md = m.dob_md",
  "i.dob IS NULL AND i.sex_c = m.sex_c AND i.pob_c IS NOT NULL AND i.pob_c <> '''' AND m.pob_c IS NOT NULL AND m.pob_c <> '''' AND left(i.pob_c, 3) = left(m.pob_c, 3) AND i.{name} IS NOT NULL AND i.{name} <> '''' AND m.{name} IS NOT NULL AND m.{name} <> '''' AND left(i.{name}, 3) = left(m.{name}, 3)",
  "i.sex_c = m.sex_c AND left(i.{name}, 3) = left(m.{name}, 3) AND i.dob_md = m.dob_md"]}'),
       'empat cabang: jenis kelamin kosong, tempat kosong, tanggal kosong, lengkap', ${CREATED_DATE}, ${CREATED_BY}
UNION ALL
SELECT 5, parse_json('{"join": "inner", "distinct": true, "branches": [
  "(i.alive_c IS NULL OR i.alive_c = m.alive_c) AND i.{name} IS NOT NULL AND i.{name} <> '''' AND m.{name} IS NOT NULL AND m.{name} <> '''' AND left(i.{name}, 3) = left(m.{name}, 3) AND i.dob IS NOT NULL AND m.tanggal_lahir IS NOT NULL AND i.dob_md = m.dob_md"]}'),
       'status hidup, 3 huruf awal nama, hari & bulan lahir', ${CREATED_DATE}, ${CREATED_BY};
