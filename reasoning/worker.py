import logging
import threading
import time
import traceback

from reasoning.config import REASONING_MAX_CONCURRENT, USE_CELERY
from reasoning.core import execute_reasoning
from reasoning.db import get_duckdb_connection
from reasoning.jobs import heartbeat, update_job_status

logger = logging.getLogger(__name__)

_slot = threading.Semaphore(REASONING_MAX_CONCURRENT)
QUEUE_HEARTBEAT_INTERVAL = 10


def dispatch_worker(job: dict) -> None:
    """
    Dispatch reasoning job via Celery message broker (Redis/Postgres) or fallback thread.
    """
    if USE_CELERY:
        try:
            from reasoning.tasks import process_reasoning_batch
            process_reasoning_batch.apply_async(args=[job], task_id=job["job_id"])
            logger.info("Dispatched job %s to Celery queue", job["job_id"])
            return
        except Exception as exc:
            logger.warning("Failed to dispatch job %s to Celery (%s). Falling back to thread.", job["job_id"], exc)

    # In-process background thread fallback
    thread = threading.Thread(
        target=_worker_entrypoint,
        args=(job,),
        daemon=True,
        name=f"reasoning-worker-{job['job_id'][:16]}",
    )
    thread.start()


def _worker_entrypoint(job: dict) -> None:
    """Worker entrypoint: waits for an available concurrency slot, then runs the engine."""
    job_id = job["job_id"]
    con = get_duckdb_connection()

    try:
        acquired = False
        while not acquired:
            acquired = _slot.acquire(blocking=True, timeout=QUEUE_HEARTBEAT_INTERVAL)
            if not acquired:
                heartbeat(con, job_id, stage="WAITING_IN_QUEUE")

        try:
            update_job_status(con, job_id, "RUNNING", stage="STARTING")
            summary = execute_reasoning(job, dry_run=job.get("dry_run", False), limit=job.get("limit"))
            update_job_status(
                con,
                job_id,
                "COMPLETED",
                stage="COMPLETED",
                result=summary,
                error=None,
            )
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            stack = traceback.format_exc()
            update_job_status(
                con,
                job_id,
                "FAILED",
                stage="FAILED",
                error=f"{err_msg}\n{stack}",
            )
        finally:
            _slot.release()
    finally:
        con.close()
