"""
Synchrono AI Reasoning - Standalone & Pluggable Package.

Provides high-performance AI reasoning for manual review records using DuckDB,
Parquet S3 / DB Semi-Join Pushdown, and on-premise Gemma 3:12B via Ollama.

Can be run standalone:
    uvicorn reasoning.app:app --host 0.0.0.0 --port 8000

Or embedded ('dijahit') into any existing FastAPI application:
    from reasoning import reasoning_router
    app.include_router(reasoning_router)
"""

from reasoning.app import app
from reasoning.core import execute_reasoning
from reasoning.jobs import (
    build_job_payload,
    clear_pattern_cache,
    get_job,
    harvest_stale_jobs,
    record_job,
    update_job_status,
)
from reasoning.router import reasoning_router
from reasoning.schemas import (
    ClearCacheResponse,
    DispatchRequest,
    DispatchResponse,
    HealthResponse,
    JobStatusResponse,
    RunSyncResponse,
)
from reasoning.worker import dispatch_worker

__all__ = [
    "app",
    "reasoning_router",
    "execute_reasoning",
    "build_job_payload",
    "dispatch_worker",
    "get_job",
    "clear_pattern_cache",
    "record_job",
    "update_job_status",
    "harvest_stale_jobs",
    "DispatchRequest",
    "DispatchResponse",
    "JobStatusResponse",
    "ClearCacheResponse",
    "HealthResponse",
    "RunSyncResponse",
]
