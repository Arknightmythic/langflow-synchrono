from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status

from reasoning.config import MASTER_PARQUET_PATH, REASONING_AI_MODEL
from reasoning.core import execute_reasoning
from reasoning.db import buka_koneksi, pinjam_koneksi
from reasoning.jobs import (
    build_job_payload,
    clear_pattern_cache,
    get_job,
    harvest_stale_jobs,
    record_job,
)
from reasoning.schemas import (
    ClearCacheResponse,
    DispatchRequest,
    DispatchResponse,
    HealthResponse,
    JobStatusResponse,
    RunSyncResponse,
)
from reasoning.worker import dispatch_worker

reasoning_router = APIRouter(prefix="/v1/reasoning", tags=["AI Reasoning"])


@reasoning_router.post(
    "/clear-cache",
    response_model=ClearCacheResponse,
    summary="Clear AI reasoning pattern cache",
    description="Truncates reasoning_patterns in PostgreSQL so subsequent runs regenerate prompts via LLM."
)
def clear_cache():
    try:
        with pinjam_koneksi() as con:
            deleted = clear_pattern_cache(con)
        return ClearCacheResponse(
            status="SUCCESS",
            deleted_patterns=deleted,
            message=f"Successfully cleared {deleted} cached reasoning pattern(s)."
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear pattern cache: {exc}"
        )


@reasoning_router.post(
    "/dispatch",
    response_model=DispatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue an AI reasoning job via JSON body"
)
def dispatch_reasoning(request: DispatchRequest):
    payload = request.model_dump(by_alias=True)
    job = build_job_payload(payload)

    with pinjam_koneksi() as con:
        harvest_stale_jobs(con)
        record_job(con, job)

    dispatch_worker(job)

    return DispatchResponse(
        status="QUEUED",
        jobId=job["job_id"],
        fileId=job["file_id"],
        message="AI Reasoning job successfully queued for processing."
    )


@reasoning_router.post(
    "/trigger/{file_id:path}",
    response_model=DispatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an AI reasoning job with path-based file_id (supports slashes)",
    description="Supports file IDs that contain slashes (e.g. /trigger/uploads/2026/file.csv or /trigger/test/1)."
)
def trigger_reasoning_by_path(
    file_id: str,
    master_parquet_path: Optional[str] = Query(None, alias="masterParquetPath"),
    llm_model: Optional[str] = Query("gemma3:12b", alias="llmModel"),
    dry_run: bool = Query(False, alias="dryRun"),
    clear_cache: bool = Query(False, alias="clearCache"),
    limit: Optional[int] = Query(None)
):
    payload = {
        "file_id": file_id,
        "master_parquet_path": master_parquet_path,
        "llm_model": llm_model,
        "dry_run": dry_run,
        "clear_cache": clear_cache,
        "limit": limit
    }
    job = build_job_payload(payload)

    with pinjam_koneksi() as con:
        harvest_stale_jobs(con)
        record_job(con, job)

    dispatch_worker(job)

    return DispatchResponse(
        status="QUEUED",
        jobId=job["job_id"],
        fileId=job["file_id"],
        message=f"AI Reasoning job successfully triggered for file '{file_id}'."
    )


@reasoning_router.get(
    "/status/{file_id:path}",
    response_model=JobStatusResponse,
    summary="Check reasoning job status by file_id (supports slashes)"
)
def check_status(
    file_id: str,
    job_id: Optional[str] = Query(None, alias="jobId")
):
    clean_file_id = file_id.strip() if file_id else None
    with pinjam_koneksi() as con:
        harvest_stale_jobs(con)
        job = get_job(con, file_id=clean_file_id, job_id=job_id)

    if job is None:
        return JobStatusResponse(
            found=False,
            status="NOT_FOUND",
            fileId=clean_file_id,
            jobId=job_id,
            message="No reasoning job found for the specified file."
        )

    is_done = job.get("status") in ("COMPLETED", "FAILED")
    return JobStatusResponse(
        found=True,
        status=job["status"],
        jobId=job.get("job_id"),
        fileId=job.get("file_id"),
        done=is_done,
        stage=job.get("stage"),
        error=job.get("error"),
        result=job.get("result"),
        queuedAt=str(job.get("created_at") or ""),
        heartbeatAt=str(job.get("heartbeat_at") or ""),
        updatedAt=str(job.get("updated_at") or "")
    )


@reasoning_router.post(
    "/run-sync/{file_id:path}",
    response_model=RunSyncResponse,
    summary="Execute reasoning synchronously (blocking, useful for testing & direct embedding)"
)
def run_reasoning_sync(
    file_id: str,
    master_parquet_path: Optional[str] = Query(None, alias="masterParquetPath"),
    llm_model: Optional[str] = Query("gemma3:12b", alias="llmModel"),
    dry_run: bool = Query(False, alias="dryRun"),
    clear_cache: bool = Query(False, alias="clearCache"),
    limit: Optional[int] = Query(None)
):
    job = {
        "file_id": file_id,
        "master_parquet_path": master_parquet_path,
        "llm_model": llm_model,
        "dry_run": dry_run,
        "clear_cache": clear_cache,
        "limit": limit
    }
    try:
        summary = execute_reasoning(job, dry_run=dry_run, limit=limit)
        return RunSyncResponse(
            status=summary.get("status", "COMPLETED"),
            fileId=file_id,
            total_rows=summary.get("total_rows", 0),
            patterns_generated=summary.get("patterns_generated", 0),
            cache_hits=summary.get("cache_hits", 0),
            llm_hits=summary.get("llm_hits", 0),
            llm_calls=summary.get("llm_calls", 0),
            master_source=summary.get("master_source", ""),
            duration_seconds=summary.get("duration_seconds", 0.0),
            dry_run=summary.get("dry_run", dry_run)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution error: {exc}"
        )


@reasoning_router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check for AI Reasoning service"
)
def health_check():
    db_status = "UNKNOWN"
    try:
        with pinjam_koneksi() as con:
            con.execute("SELECT 1 FROM pg.public.reasoning_jobs LIMIT 1")
            db_status = "CONNECTED"
    except Exception as e:
        db_status = f"ERROR: {e}"

    return HealthResponse(
        status="HEALTHY" if db_status == "CONNECTED" else "DEGRADED",
        database=db_status,
        llm_model=REASONING_AI_MODEL,
        master_parquet_path=MASTER_PARQUET_PATH
    )
