CREATE TABLE IF NOT EXISTS ${SERVICE}grading_jobs (
    job_id              VARCHAR(64)    NOT NULL,
    file_id             VARCHAR(255)   NOT NULL,
    status              VARCHAR(16)    NOT NULL,
    stage               VARCHAR(128),
    filename            VARCHAR(512),
    s3_bucket           VARCHAR(255),
    s3_endpoint         VARCHAR(255),
    csv_key             VARCHAR(1024),
    raw_source_key      VARCHAR(1024),
    parquet_key         VARCHAR(1024),
    enriched_key        VARCHAR(1024),
    parquet_size_bytes  BIGINT,
    row_count           BIGINT,
    institution_id      VARCHAR(255),
    institution_name    VARCHAR(512),
    callback_url        VARCHAR(1024),
    callback_token      VARCHAR(512),
    callback_status     VARCHAR(16),
    callback_attempts   INT,
    callback_error      VARCHAR(2048),
    result              JSON,
    error               VARCHAR(65533),
    grading_duration_ms BIGINT,
    kl_load_ms          BIGINT,
    created_at          DATETIME       NOT NULL,
    started_at          DATETIME,
    finished_at         DATETIME,
    heartbeat_at        DATETIME,
    created_date        DATETIME,
    created_by          VARCHAR(255)
) PRIMARY KEY (job_id)
DISTRIBUTED BY HASH(job_id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}grade_criteria (
    grade_id          INT          NOT NULL,
    eval_order        INT          NOT NULL,
    nik_column        VARCHAR(16)  NOT NULL,
    min_nik           DOUBLE,
    min_nama          DOUBLE,
    min_tempat_lahir  DOUBLE,
    min_tanggal_lahir DOUBLE,
    min_jenis_kelamin DOUBLE,
    min_nama_ibu      DOUBLE,
    min_nik_trusted   DOUBLE,
    active            BOOLEAN      NOT NULL,
    updated_at        DATETIME,
    updated_by        VARCHAR(255),
    created_date      DATETIME,
    created_by        VARCHAR(255)
) PRIMARY KEY (grade_id)
DISTRIBUTED BY HASH(grade_id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}grade_bands (
    grade_id             INT           NOT NULL,
    grade_letter         VARCHAR(2)    NOT NULL,
    score_min            INT           NOT NULL,
    score_max            INT           NOT NULL,
    severity_label       VARCHAR(64)   NOT NULL,
    can_proceed          BOOLEAN       NOT NULL,
    criteria_description VARCHAR(1024),
    created_date         DATETIME,
    created_by           VARCHAR(255)
) PRIMARY KEY (grade_id)
DISTRIBUTED BY HASH(grade_id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}grade_rules (
    grade_code           INT          NOT NULL,
    auto_missing_max     INT,
    auto_score_min       DOUBLE,
    review_missing_count INT,
    review_score_min     DOUBLE,
    review_score_max     DOUBLE,
    weights              JSON,
    missing_elements     JSON,
    name_cleaning        JSON,
    date_match           VARCHAR(16),
    updated_at           DATETIME,
    updated_by           VARCHAR(255),
    created_date         DATETIME,
    created_by           VARCHAR(255)
) PRIMARY KEY (grade_code)
DISTRIBUTED BY HASH(grade_code) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}matching_queries (
    grade_code  INT          NOT NULL,
    blocking    JSON         NOT NULL,
    description VARCHAR(1024),
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (grade_code)
DISTRIBUTED BY HASH(grade_code) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}engine_config (
    config_key VARCHAR(128) NOT NULL,
    value      JSON,
    updated_at DATETIME,
    updated_by VARCHAR(255),
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (config_key)
DISTRIBUTED BY HASH(config_key) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}config_versions (
    version    VARCHAR(16) NOT NULL,
    content    JSON,
    first_used DATETIME,
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (version)
DISTRIBUTED BY HASH(version) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}masters (
    master_id  VARCHAR(128)  NOT NULL,
    source     VARCHAR(1024),
    status     VARCHAR(16)   NOT NULL,
    row_count  BIGINT,
    load_ms    BIGINT,
    detail     JSON,
    error      VARCHAR(4096),
    updated_at DATETIME,
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (master_id)
DISTRIBUTED BY HASH(master_id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}config_history (
    id         BIGINT       NOT NULL,
    changed_at DATETIME     NOT NULL,
    changed_by VARCHAR(255),
    scope      VARCHAR(10)  NOT NULL,
    grade_id   INT,
    changes    JSON,
    version    VARCHAR(16),
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}reasoning_patterns (
    pattern_hash      VARCHAR(64)   NOT NULL,
    pattern_name      VARCHAR(255)  NOT NULL,
    pattern_signature VARCHAR(4096) NOT NULL,
    reason_template   VARCHAR(65533) NOT NULL,
    sample_id         VARCHAR(1024),
    hit_count         BIGINT,
    created_at        DATETIME,
    updated_at        DATETIME,
    created_date      DATETIME,
    created_by        VARCHAR(255)
) PRIMARY KEY (pattern_hash)
DISTRIBUTED BY HASH(pattern_hash) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${SERVICE}service_api_keys (
    id           VARCHAR(36)  NOT NULL,
    name         VARCHAR(512),
    key_hash     VARCHAR(64)  NOT NULL,
    key_prefix   VARCHAR(16)  NOT NULL,
    key_length   INT          NOT NULL,
    user_id      VARCHAR(36)  NOT NULL,
    created_at   DATETIME     NOT NULL,
    last_used_at DATETIME,
    total_uses   BIGINT,
    is_active    BOOLEAN      NOT NULL,
    expires_at   DATETIME,
    created_date DATETIME,
    created_by   VARCHAR(255)
) PRIMARY KEY (id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");
