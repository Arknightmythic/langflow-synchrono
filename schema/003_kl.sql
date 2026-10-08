CREATE TABLE IF NOT EXISTS ${KL}records (
    file_id        VARCHAR(255)  NOT NULL,
    row_id         VARCHAR(255)  NOT NULL,
    nik            VARCHAR(64),
    nama           VARCHAR(512),
    tempat_lahir   VARCHAR(255),
    tanggal_lahir  VARCHAR(64),
    jenis_kelamin  VARCHAR(64),
    nama_ibu       VARCHAR(512),
    provinsi       VARCHAR(255),
    kabupaten      VARCHAR(255),
    kecamatan      VARCHAR(255),
    kelurahan      VARCHAR(255),
    status_hidup   VARCHAR(64),
    nik_trusted    BOOLEAN,
    is_anomaly     BOOLEAN,
    anomaly_type   VARCHAR(1024),
    anomaly_notes  VARCHAR(4096),
    name_v0        VARCHAR(512),
    name_v1        VARCHAR(512),
    name_v2        VARCHAR(512),
    name_v3        VARCHAR(512),
    name_v4        VARCHAR(512),
    name_v5        VARCHAR(512),
    name_v6        VARCHAR(512),
    name_v7        VARCHAR(512),
    mother_v0      VARCHAR(512),
    mother_v1      VARCHAR(512),
    mother_v2      VARCHAR(512),
    mother_v3      VARCHAR(512),
    mother_v4      VARCHAR(512),
    mother_v5      VARCHAR(512),
    mother_v6      VARCHAR(512),
    mother_v7      VARCHAR(512),
    pob_c          VARCHAR(255),
    dob            DATE,
    dob_md         SMALLINT,
    sex_c          VARCHAR(4),
    alive_c        VARCHAR(4),
    prov_c         VARCHAR(255),
    kab_c          VARCHAR(255),
    kec_c          VARCHAR(255),
    kel_c          VARCHAR(255),
    grading_job_id VARCHAR(64)
) DUPLICATE KEY (file_id, row_id)
PARTITION BY (file_id)
DISTRIBUTED BY HASH(row_id) BUCKETS ${BUCKETS}
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${KL}enriched (
    file_id         VARCHAR(255)  NOT NULL,
    row_no          BIGINT        NOT NULL,
    nik_trusted     BOOLEAN,
    is_anomaly      BOOLEAN,
    anomaly_type    VARCHAR(1024),
    data            JSON          NOT NULL,
    grading_job_id  VARCHAR(64)
) DUPLICATE KEY (file_id, row_no)
PARTITION BY (file_id)
DISTRIBUTED BY HASH(row_no) BUCKETS 1
PROPERTIES ("replication_num" = "${REPLICATION}");
