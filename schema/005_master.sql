CREATE TABLE IF NOT EXISTS ${DB_MASTER}.persons (
    master_id       VARCHAR(128)  NOT NULL,
    nik             VARCHAR(64),
    nama_lengkap    VARCHAR(512),
    tempat_lahir    VARCHAR(255),
    tanggal_lahir   DATE,
    jenis_kelamin   VARCHAR(64),
    nama_ibu        VARCHAR(512),
    status_kematian VARCHAR(64),
    provinsi        VARCHAR(255),
    kabupaten       VARCHAR(255),
    kecamatan       VARCHAR(255),
    kelurahan       VARCHAR(255),
    name_v0         VARCHAR(512),
    name_v1         VARCHAR(512),
    name_v2         VARCHAR(512),
    name_v3         VARCHAR(512),
    name_v4         VARCHAR(512),
    name_v5         VARCHAR(512),
    name_v6         VARCHAR(512),
    name_v7         VARCHAR(512),
    mother_v0       VARCHAR(512),
    mother_v1       VARCHAR(512),
    mother_v2       VARCHAR(512),
    mother_v3       VARCHAR(512),
    mother_v4       VARCHAR(512),
    mother_v5       VARCHAR(512),
    mother_v6       VARCHAR(512),
    mother_v7       VARCHAR(512),
    pob_c           VARCHAR(255),
    dob_md          SMALLINT,
    sex_c           VARCHAR(4),
    alive_c         VARCHAR(4),
    prov_c          VARCHAR(255),
    kab_c           VARCHAR(255),
    kec_c           VARCHAR(255),
    kel_c           VARCHAR(255)
) DUPLICATE KEY (master_id, nik)
PARTITION BY (master_id)
DISTRIBUTED BY HASH(nik) BUCKETS ${BUCKETS}
PROPERTIES ("replication_num" = "${REPLICATION}");

CREATE TABLE IF NOT EXISTS ${DB_MASTER}.dictionary (
    element VARCHAR(32)  NOT NULL,
    value   VARCHAR(512) NOT NULL
) DUPLICATE KEY (element, value)
DISTRIBUTED BY HASH(value) BUCKETS 4
PROPERTIES ("replication_num" = "${REPLICATION}");
