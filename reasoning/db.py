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


def get_duckdb_connection() -> duckdb.DuckDBPyConnection:
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
def get_db_connection():
    """Borrow a DuckDB connection (using pool if available in environment, or standalone)."""
    try:
        from _kolam import pinjam
        with pinjam() as con:
            yield con
    except (ImportError, ModuleNotFoundError):
        con = get_duckdb_connection()
        try:
            yield con
        finally:
            con.close()


@contextmanager
def get_pg_connection():
    """Borrow a direct native PostgreSQL connection for atomic locks and state machines."""
    import psycopg2
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


# Backwards compatibility aliases
buka_koneksi = get_duckdb_connection
pinjam_koneksi = get_db_connection
pinjam_pg = get_pg_connection


