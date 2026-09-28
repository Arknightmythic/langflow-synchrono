from __future__ import annotations

import threading
import time
import traceback

from reasoning.config import REASONING_MAX_CONCURRENT
from reasoning.core import execute_reasoning
from reasoning.db import buka_koneksi
from reasoning.jobs import heartbeat, update_job_status

_slot = threading.Semaphore(REASONING_MAX_CONCURRENT)
QUEUE_HEARTBEAT_INTERVAL = 10


def dispatch_worker(job: dict) -> None:
    """Spawn a background daemon thread to process the reasoning job."""
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
    con = buka_koneksi()

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
