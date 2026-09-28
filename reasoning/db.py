import os
from contextlib import contextmanager
import duckdb

from reasoning.config import (
    DUCKDB_MEMORY_LIMIT,
    DUCKDB_TEMP_DIR,
    PG_DSN,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_SECRET_KEY,
    S3_USE_SSL,
)

SQL_MACRO = """
CREATE OR REPLACE MACRO j(a, b) AS
    CASE WHEN a IS NULL OR b IS NULL OR a = '' OR b = ''
         THEN 0.0
         ELSE jaro_winkler_similarity(a, b) END
"""


def buka_koneksi() -> duckdb.DuckDBPyConnection:
    """Connect to in-memory DuckDB with httpfs, S3, and PostgreSQL attached."""
    con = duckdb.connect()

    if DUCKDB_MEMORY_LIMIT:
        con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    if DUCKDB_TEMP_DIR:
        con.execute(f"SET temp_directory = '{DUCKDB_TEMP_DIR}'")

    for ext in ("httpfs", "postgres"):
        con.execute(f"INSTALL {ext}")
        con.execute(f"LOAD {ext}")

    con.execute(f"""
        CREATE OR REPLACE SECRET seaweed (
            TYPE s3,
            KEY_ID '{S3_ACCESS_KEY}',
            SECRET '{S3_SECRET_KEY}',
            ENDPOINT '{S3_ENDPOINT}',
            URL_STYLE 'path',
            USE_SSL {str(S3_USE_SSL).lower()}
        )
    """)

    con.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE postgres)")
    con.execute(SQL_MACRO)
    return con


@contextmanager
def pinjam_koneksi():
    """Borrow a connection (using pool if available in environment, or standalone)."""
    try:
        from _kolam import pinjam
        with pinjam() as con:
            yield con
    except (ImportError, ModuleNotFoundError):
        con = buka_koneksi()
        try:
            yield con
        finally:
            con.close()
