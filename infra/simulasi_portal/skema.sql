-- ═══════════════════════════════════════════════════════════════════════════
-- TIRUAN skema DB portal untuk matching — HANYA untuk pengujian lokal.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Tabel-tabel ini MILIK PORTAL. Di produksi portal yang membuatnya, dan engine
-- hanya menulis ke sana (opsi B: engine menyuntik hasil langsung ke DB portal).
-- Berkas ini ada karena kredensial DB portal belum diberikan, dan menguji
-- penyuntikan terhadap tiruan jauh lebih aman daripada terhadap DB sungguhan.
--
-- ASAL SETIAP KOLOM
--
-- Spesifikasi integrasi versi 27 Sep 2026 MENGHAPUS definisi tabelnya — yang
-- tersisa hanya contoh `INSERT`/`UPDATE` di §5.1. Jadi tipe kolom di bawah
-- diambil dari versi 21 Sep 2026, yang masih memuatnya, ditambah dua kolom yang
-- baru muncul di versi 27 Sep:
--
--   syncrono_matching_job.result_parquet_key   (dari contoh UPDATE §5.1)
--   syncrono_matching_result.reasoning         (§4.1 dan §6)
--
-- Kalau skema portal yang sebenarnya berbeda dari ini, YANG BENAR ADALAH SKEMA
-- PORTAL. Berkas ini tiruan, bukan kontrak.
--
-- Menyiapkan (sekali):
--     CREATE DATABASE portal_sim;
--     \c portal_sim
--     \i skema.sql

CREATE TABLE IF NOT EXISTS syncrono_matching_job (
    id                      VARCHAR(36)  PRIMARY KEY,
    file_id                 VARCHAR(255) NOT NULL,
    master_file_id          VARCHAR(255) NOT NULL,
    -- PENDING, IN_PROGRESS, BLOCKING, MATCHING, SCORING, COMPLETED, FAILED, CANCELLED
    status                  VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
    current_stage           VARCHAR(32),
    started_at              TIMESTAMP,
    completed_at            TIMESTAMP,
    failed_at               TIMESTAMP,
    last_error              TEXT,
    total_incoming          INTEGER,
    total_candidates        INTEGER,
    avg_candidates_per_row  DOUBLE PRECISION,
    pass1_count             INTEGER,
    pass2_count             INTEGER,
    scoring_count           INTEGER,
    auto_count              INTEGER,
    review_count            INTEGER,
    unmatch_count           INTEGER,
    conflict_count          INTEGER,
    stage_durations         JSONB,
    blocking_metrics        JSONB,
    peak_rss_mb             INTEGER,
    result_parquet_key      VARCHAR(512),
    created_at              TIMESTAMP    NOT NULL DEFAULT now(),
    created_by              VARCHAR(255),
    updated_at              TIMESTAMP,
    updated_by              VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS syncrono_matching_result (
    id                  VARCHAR(36)  PRIMARY KEY,
    csv_file_id         VARCHAR(255) NOT NULL,
    master_file_id      VARCHAR(255) NOT NULL,
    -- FK sungguhan, sengaja: memaksa engine tidak bisa menyuntik hasil untuk
    -- job yang tidak pernah dibuat portal. Kalau DB portal memasang FK yang
    -- sama, pengujian ini sudah membuktikan urutannya benar.
    job_id              VARCHAR(36)  NOT NULL REFERENCES syncrono_matching_job (id),
    id_incoming         VARCHAR(255) NOT NULL,
    master_nik          VARCHAR(64),
    score               DOUBLE PRECISION NOT NULL,
    -- AUTO, REVIEW, UNMATCH, CONFLICT
    status              VARCHAR(32)  NOT NULL,
    -- PASS1_NIK_NAMA, PASS2_NAMA_TGL_IBU, SCORING, MANUAL
    method              VARCHAR(32)  NOT NULL,
    rank_conflict       BOOLEAN      NOT NULL,
    -- SPELLING_NAME, TITLE_DEGREE, SWAPPED_DOB, NIK_CONFLICT, GENERAL_REVIEW
    pattern_group       VARCHAR(64),
    reasoning           TEXT,
    incoming_snapshot   JSONB        NOT NULL,
    master_snapshot     JSONB,
    -- Diisi OPERATOR di portal, bukan engine: NULL, MATCH, NOT_MATCH.
    review_decision     VARCHAR(32),
    created_at          TIMESTAMP    NOT NULL,
    updated_at          TIMESTAMP    NOT NULL,
    created_by          VARCHAR(255) NOT NULL,
    updated_by          VARCHAR(255) NOT NULL
);

-- `DELETE ... WHERE csv_file_id = ? AND master_file_id = ?` dijalankan di awal
-- setiap penyuntikan (idempotensi, spesifikasi §5.1). Tanpa indeks ini, satu
-- DELETE memindai seluruh tabel hasil — yang tumbuh dengan setiap berkas.
CREATE INDEX IF NOT EXISTS idx_matching_result_pasangan
    ON syncrono_matching_result (csv_file_id, master_file_id);

CREATE INDEX IF NOT EXISTS idx_matching_result_job
    ON syncrono_matching_result (job_id);
