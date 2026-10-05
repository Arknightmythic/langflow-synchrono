import base64
import glob
import http.client
import json
import os
import shutil
import threading
import time
import uuid
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

from . import settings as cfg

_local = threading.local()


class StreamLoadError(RuntimeError):
    pass


def _session(conn) -> None:
    statements = [f"SET query_timeout = {cfg.SR_QUERY_TIMEOUT}",
                  f"SET insert_timeout = {cfg.SR_QUERY_TIMEOUT}"]
    if cfg.SR_ENABLE_SPILL:
        statements += ["SET enable_spill = true", "SET spill_mode = 'auto'"]
    if cfg.SR_QUERY_MEM_LIMIT:
        statements.append(f"SET query_mem_limit = {cfg.SR_QUERY_MEM_LIMIT}")
    with conn.cursor() as cur:
        for statement in statements:
            try:
                cur.execute(statement)
            except pymysql.MySQLError:
                pass


def connect(database: str | None = None):
    conn = pymysql.connect(host=cfg.SR_HOST, port=cfg.SR_PORT, user=cfg.SR_USER,
                           password=cfg.SR_PASSWORD, database=database,
                           autocommit=True, charset="utf8mb4", connect_timeout=15,
                           cursorclass=pymysql.cursors.DictCursor)
    _session(conn)
    return conn


def conn():
    current = getattr(_local, "conn", None)
    if current is not None:
        try:
            current.ping(reconnect=False)
            return current
        except Exception:  # noqa: BLE001
            try:
                current.close()
            except Exception:  # noqa: BLE001
                pass
    _local.conn = connect()
    return _local.conn


def execute(sql: str) -> int:
    for attempt in (1, 2):
        try:
            with conn().cursor() as cur:
                return cur.execute(sql)
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
            _local.conn = None
            if attempt == 2:
                raise
    return 0


def query(sql: str) -> list[dict]:
    for attempt in (1, 2):
        try:
            with conn().cursor() as cur:
                cur.execute(sql)
                return list(cur.fetchall())
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
            _local.conn = None
            if attempt == 2:
                raise
    return []


def scalar(sql: str):
    rows = query(sql)
    return next(iter(rows[0].values())) if rows else None


def table_exists(database: str, table: str) -> bool:
    rows = query(f"SELECT count(*) AS n FROM information_schema.tables "
                 f"WHERE table_schema = '{database}' AND table_name = '{table}'")
    return bool(rows and rows[0]["n"])


def drop_tables(database: str, prefix: str) -> None:
    rows = query(f"SELECT table_name FROM information_schema.tables "
                 f"WHERE table_schema = '{database}' AND table_name LIKE '{prefix}%'")
    for row in rows:
        name = row.get("table_name") or row.get("TABLE_NAME")
        execute(f"DROP TABLE IF EXISTS {database}.`{name}` FORCE")


def _auth() -> str:
    token = base64.b64encode(f"{cfg.SR_USER}:{cfg.SR_PASSWORD}".encode()).decode()
    return f"Basic {token}"


def stream_load_file(database: str, table: str, path: str, columns: list[str],
                     label: str | None = None, timeout: int = 3600) -> dict:
    target = urlparse(cfg.SR_STREAM_LOAD_URL)
    size = os.path.getsize(path)
    headers = {
        "Authorization": _auth(),
        "Expect": "100-continue",
        "label": label or f"syn_{uuid.uuid4().hex}",
        "format": "gzip" if path.endswith(".gz") else "csv",
        "column_separator": "\\x01",
        "columns": ",".join(f"`{c}`" for c in columns),
        "max_filter_ratio": "0",
        "timeout": str(timeout),
        "Content-Length": str(size),
    }
    for attempt in (1, 2, 3):
        connection = http.client.HTTPConnection(target.hostname, target.port or 80,
                                                timeout=timeout + 60)
        try:
            with open(path, "rb") as body:
                connection.request("PUT", f"/api/{database}/{table}/_stream_load",
                                   body=body, headers=headers)
            response = connection.getresponse()
            text = response.read().decode(errors="replace")
        except (ConnectionError, http.client.HTTPException, OSError) as e:
            if attempt == 3:
                raise StreamLoadError(f"stream load {database}.{table} failed: {e}") from e
            time.sleep(2 * attempt)
            continue
        finally:
            connection.close()
        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            raise StreamLoadError(f"stream load {database}.{table}: HTTP {response.status} "
                                  f"{text[:300]}") from e
        status = result.get("Status")
        if status in ("Success", "Publish Timeout"):
            return result
        if status == "Label Already Exists" and result.get("ExistingJobStatus") == "FINISHED":
            return result
        raise StreamLoadError(f"stream load {database}.{table}: {status} - "
                              f"{result.get('Message')}")
    raise StreamLoadError("unreachable")


def stream_load_query(duck, select_sql: str, database: str, table: str,
                      columns: list[str], label_prefix: str | None = None) -> dict:
    """Export a DuckDB query as \\x01-separated CSV chunks and Stream Load each one."""
    os.makedirs(cfg.WORK_DIR, exist_ok=True)
    folder = os.path.join(cfg.WORK_DIR, f"sl_{uuid.uuid4().hex}")
    sep = chr(1)
    quote = chr(2)
    started = time.perf_counter()
    try:
        duck.execute(f"""COPY ({select_sql}) TO '{folder}'
                         (FORMAT csv, DELIMITER '{sep}', QUOTE '{quote}', HEADER false,
                          NULLSTR '\\N', FILE_SIZE_BYTES '{cfg.STREAM_LOAD_FILE_BYTES}',
                          PER_THREAD_OUTPUT true, COMPRESSION gzip, FILE_EXTENSION 'csv.gz',
                          DATEFORMAT '%Y-%m-%d', TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S')""")
        export_ms = int((time.perf_counter() - started) * 1000)
        files = sorted(glob.glob(os.path.join(folder, "*.gz")))
        prefix = label_prefix or f"syn_{uuid.uuid4().hex[:12]}"
        rows, size = 0, 0
        load_started = time.perf_counter()
        for index, path in enumerate(files):
            size += os.path.getsize(path)
            result = stream_load_file(database, table, path, columns,
                                      label=f"{prefix}_{index}")
            rows += int(result.get("NumberLoadedRows") or 0)
            os.remove(path)
        return {"rows": rows, "files": len(files), "bytes": size, "exportMs": export_ms,
                "loadMs": int((time.perf_counter() - load_started) * 1000)}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def clean_text(expr: str) -> str:
    """Remove characters that would break the \\x01-separated CSV stream."""
    return f"regexp_replace(CAST({expr} AS VARCHAR), '[\\x01\\x02\\r\\n]', ' ', 'g')"


def attach(duck, database: str | None = None) -> None:
    duck.execute("LOAD mysql")
    dsn = (f"host={cfg.SR_HOST} port={cfg.SR_PORT} user={cfg.SR_USER} "
           f"password={cfg.SR_PASSWORD} database={database or cfg.DB_SERVICE}")
    duck.execute(f"ATTACH '{dsn}' AS sr (TYPE mysql, READ_ONLY)")


def pull(duck, table: str, sql: str, key: str | None = None, chunk_rows: int = 400_000,
         retries: int = 4) -> int:
    """Materialise a StarRocks query as a DuckDB table, in hash chunks when it is large."""
    chunks = 1
    if key:
        total = int(scalar(f"SELECT count(*) FROM ({sql}) t") or 0)
        chunks = max(1, -(-total // chunk_rows))
    duck.execute(f"DROP TABLE IF EXISTS {table}")
    for part in range(chunks):
        query = sql if chunks == 1 else (
            f"SELECT * FROM ({sql}) t WHERE abs(murmur_hash3_32(CAST({key} AS VARCHAR))) "
            f"% {chunks} = {part}")
        escaped = query.replace("'", "''")
        verb = (f"CREATE TABLE {table} AS" if part == 0 else f"INSERT INTO {table}")
        for attempt in range(1, retries + 1):
            try:
                duck.execute(f"{verb} SELECT * FROM mysql_query('sr', '{escaped}')")
                break
            except Exception:  # noqa: BLE001
                if attempt == retries:
                    raise
                time.sleep(3 * attempt)
    return duck.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
