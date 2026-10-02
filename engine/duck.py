import os
import re

import duckdb

from . import settings as cfg

JW_MACRO = """
CREATE OR REPLACE MACRO j(a, b) AS
    CASE WHEN a IS NULL OR b IS NULL OR a = '' OR b = ''
         THEN 0.0
         ELSE jaro_winkler_similarity(a, b) END
"""


def connect(s3: bool = True) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if cfg.DUCKDB_MEMORY_LIMIT:
        con.execute(f"SET memory_limit = '{cfg.DUCKDB_MEMORY_LIMIT}'")
    if cfg.DUCKDB_TEMP_DIR:
        os.makedirs(cfg.DUCKDB_TEMP_DIR, exist_ok=True)
        con.execute(f"SET temp_directory = '{cfg.DUCKDB_TEMP_DIR}'")
    if cfg.DUCKDB_THREADS:
        con.execute(f"SET threads = {cfg.DUCKDB_THREADS}")
    con.execute("SET enable_progress_bar = false")
    if s3:
        con.execute("LOAD httpfs")
        set_s3(con, cfg.S3_ENDPOINT, cfg.S3_KEY, cfg.S3_SECRET, cfg.S3_USE_SSL)
    con.execute(JW_MACRO)
    return con


def set_s3(con, endpoint: str, key: str, secret: str, use_ssl: bool) -> None:
    host = re.sub(r"^https?://", "", endpoint).rstrip("/")
    con.execute(f"""
        CREATE OR REPLACE SECRET seaweed (
            TYPE s3, KEY_ID '{key}', SECRET '{secret}', ENDPOINT '{host}',
            URL_STYLE 'path', USE_SSL {str(use_ssl).lower()}
        )""")
