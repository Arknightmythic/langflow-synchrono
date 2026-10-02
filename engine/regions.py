import re

from . import settings as cfg

TABLE = "ref_region"
EMPTY = (f"CREATE OR REPLACE TEMP TABLE {TABLE} (code6 VARCHAR, prov_code VARCHAR, "
         f"prov_name VARCHAR, kec_code VARCHAR, kec_name VARCHAR)")


def _bucket(path: str) -> str:
    m = re.match(r"^s3://([^/]+)/", path)
    return m.group(1) if m else ""


def load(con) -> dict:
    source = cfg.REGION_PARQUET
    if cfg.REGION_S3_ENDPOINT:
        endpoint = cfg.REGION_S3_ENDPOINT
        con.execute(f"""
            CREATE OR REPLACE SECRET region (
                TYPE s3, KEY_ID '{cfg.REGION_S3_KEY}', SECRET '{cfg.REGION_S3_SECRET}',
                ENDPOINT '{re.sub(r"^https?://", "", endpoint).rstrip("/")}',
                URL_STYLE 'path', USE_SSL {str(endpoint.lower().startswith("https://")).lower()},
                SCOPE 's3://{_bucket(source)}')""")
    try:
        if not source:
            raise ValueError("WILAYAH_PARQUET kosong")
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE {TABLE} AS
            SELECT DISTINCT trim(CAST(kode_nik_6digit AS VARCHAR)) AS code6,
                   trim(CAST(kode_provinsi AS VARCHAR)) AS prov_code,
                   trim(CAST(nama_provinsi AS VARCHAR)) AS prov_name,
                   trim(CAST(kode_kecamatan AS VARCHAR)) AS kec_code,
                   trim(CAST(nama_kecamatan AS VARCHAR)) AS kec_name
              FROM read_parquet('{source}')
             WHERE kode_nik_6digit IS NOT NULL""")
        n, provinces = con.execute(
            f"SELECT count(*), count(DISTINCT prov_code) FROM {TABLE}").fetchone()
        print(f"[REGION] {n:,} kecamatan, {provinces} provinces ({source})")
        return {"available": True, "kecamatan": n, "provinces": provinces, "source": source}
    except Exception as e:  # noqa: BLE001
        message = " ".join(str(e).split())[:150]
        print(f"[REGION] reference not readable, region checks disabled: {message}")
        con.execute(EMPTY)
        return {"available": False, "kecamatan": 0, "provinces": 0, "source": source,
                "error": message}


def province_codes(con) -> set[str]:
    try:
        return {r[0] for r in con.execute(f"SELECT DISTINCT prov_code FROM {TABLE}").fetchall()
                if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def province_valid_sql(nik_column: str) -> str:
    return f"substr({nik_column}, 1, 2) IN (SELECT prov_code FROM {TABLE})"


def kecamatan_valid_sql(nik_column: str) -> str:
    return f"substr({nik_column}, 1, 6) IN (SELECT code6 FROM {TABLE})"
