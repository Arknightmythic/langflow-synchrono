"""
Job status tracking for AI Reasoning.

Persists async job state in the PostgreSQL reasoning_jobs table.
Provides status transitions (QUEUED -> RUNNING -> COMPLETED / FAILED),
worker heartbeats, and stale job harvesting across container restarts.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

STALE_MINUTES = int(os.getenv("REASONING_STALE_MINUTES", "15"))

STALE_ERROR_MESSAGE = (
    f"Worker terminated unexpectedly (likely container restart). "
    f"Job marked as FAILED due to missed heartbeat for > {STALE_MINUTES} minutes."
)


def q(value) -> str:
    """Format values safely for raw PostgreSQL execution string."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return "'" + str(value).replace("'", "''") + "'"


def execute_pg(con, sql: str) -> None:
    """Execute a raw SQL statement directly on PostgreSQL via DuckDB extension."""
    con.execute(f"CALL postgres_execute('pg', {q(sql)})")


def get_current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_job_id() -> str:
    return f"reasoning-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"


JOB_COLUMNS = [
    "job_id",
    "file_id",
    "status",
    "stage",
    "error",
    "result",
    "created_at",
    "heartbeat_at",
    "updated_at",
]


def build_job_payload(payload: dict, defaults: dict | None = None) -> dict:
    """
    Parse and validate incoming payload into a standardized job dictionary.
    Supports both camelCase (API) and snake_case (CLI / tests).
    """
    merged = {**(defaults or {}), **(payload or {})}

    def extract(*keys, required=False):
        for k in keys:
            val = merged.get(k)
            if val not in (None, ""):
                return val
        if required:
            raise ValueError(f"Missing required field: {keys[0]}")
        return None

    file_id = str(extract("fileId", "file_id", required=True))
    llm_model = extract("llmModel", "llm_model") or "gemma3:12b"
    master_parquet_path = extract("masterParquetPath", "master_parquet_path")

    return {
        "job_id": generate_job_id(),
        "file_id": file_id,
        "llm_model": llm_model,
        "master_parquet_path": master_parquet_path,
        "status": "QUEUED",
        "stage": "QUEUED",
        "error": None,
        "result": None,
    }


def record_job(con, job: dict) -> None:
    """Record a newly queued reasoning job into PostgreSQL."""
    now = get_current_utc_iso()
    record = {
        "job_id": job["job_id"],
        "file_id": job["file_id"],
        "status": "QUEUED",
        "stage": "QUEUED",
        "error": None,
        "result": None,
        "created_at": now,
        "heartbeat_at": now,
        "updated_at": now,
    }
    values_str = ", ".join(q(record.get(col)) for col in JOB_COLUMNS)
    execute_pg(
        con,
        f"""
        INSERT INTO reasoning_jobs ({', '.join(JOB_COLUMNS)})
        VALUES ({values_str})
        ON CONFLICT (job_id) DO NOTHING
        """,
    )


def update_job_status(con, job_id: str, status: str, **kwargs) -> None:
    """Update job status, heartbeat, and optional result/stage/error fields."""
    now = get_current_utc_iso()
    updates = [f"status = {q(status)}", f"heartbeat_at = {q(now)}", f"updated_at = {q(now)}"]

    for k, v in kwargs.items():
        if k == "result" and v is not None:
            updates.append(f"result = {q(v)}::jsonb")
        else:
            updates.append(f"{k} = {q(v)}")

    execute_pg(
        con,
        f"""
        UPDATE reasoning_jobs
        SET {', '.join(updates)}
        WHERE job_id = {q(job_id)}
        """,
    )


def heartbeat(con, job_id: str, stage: str | None = None) -> None:
    """Update heartbeat timestamp to signify active background processing."""
    now = get_current_utc_iso()
    stage_update = f", stage = {q(stage)}" if stage else ""
    execute_pg(
        con,
        f"""
        UPDATE reasoning_jobs
        SET heartbeat_at = {q(now)}, updated_at = {q(now)}{stage_update}
        WHERE job_id = {q(job_id)}
        """,
    )


def harvest_stale_jobs(con) -> int:
    """Identify and fail jobs whose worker thread stopped sending heartbeats."""
    now = get_current_utc_iso()
    before_count = con.execute(
        """
        SELECT count(*) FROM pg.public.reasoning_jobs
        WHERE status IN ('QUEUED', 'RUNNING')
        """
    ).fetchone()[0]

    execute_pg(
        con,
        f"""
        UPDATE reasoning_jobs
        SET status = 'FAILED',
            updated_at = {q(now)},
            error = {q(STALE_ERROR_MESSAGE)}
        WHERE status IN ('QUEUED', 'RUNNING')
          AND heartbeat_at < now() - interval '{STALE_MINUTES} minutes'
        """,
    )

    after_count = con.execute(
        """
        SELECT count(*) FROM pg.public.reasoning_jobs
        WHERE status IN ('QUEUED', 'RUNNING')
        """
    ).fetchone()[0]

    return before_count - after_count


READ_COLUMNS = (
    "job_id, file_id, status, stage, error, "
    "CAST(result AS VARCHAR) AS result, "
    "CAST(created_at AS VARCHAR) AS created_at, "
    "CAST(heartbeat_at AS VARCHAR) AS heartbeat_at, "
    "CAST(updated_at AS VARCHAR) AS updated_at"
)


def get_job(con, file_id: str | None = None, job_id: str | None = None) -> dict | None:
    """Fetch the latest reasoning job by file_id or specific job_id."""
    if job_id:
        where_clause = f"job_id = {q(job_id)}"
    elif file_id:
        where_clause = f"file_id = {q(file_id)} ORDER BY created_at DESC LIMIT 1"
    else:
        raise ValueError("Must provide either file_id or job_id")

    query = f"SELECT {READ_COLUMNS} FROM pg.public.reasoning_jobs WHERE {where_clause}"
    cursor = con.execute(query)
    row = cursor.fetchone()
    if not row:
        return None

    names = [desc[0] for desc in cursor.description]
    res = dict(zip(names, row))
    if res.get("result"):
        try:
            res["result"] = json.loads(res["result"])
        except (json.JSONDecodeError, TypeError):
            pass
    return res
