"""
Background worker for AI Reasoning.

Runs the reasoning engine asynchronously in a dedicated daemon thread.
Manages concurrency limits, periodic heartbeats, and status transitions:
  QUEUED -> RUNNING -> COMPLETED / FAILED.
"""

from __future__ import annotations

import os
import threading
import time
import traceback

from _reasoning import execute_reasoning
from _reasoning_jobs import heartbeat, update_job_status
from _shared import buka_koneksi

MAX_CONCURRENT = int(os.getenv("REASONING_MAX_CONCURRENT", "1"))
_slot = threading.Semaphore(MAX_CONCURRENT)
QUEUE_HEARTBEAT_INTERVAL = 10


def dispatch_worker(job: dict) -> None:
    """Spawn a background daemon thread to process the reasoning job."""
    thread = threading.Thread(
        target=_worker_entrypoint,
        args=(job,),
        daemon=True,
        name=f"reasoning-{job['job_id'][-8:]}",
    )
    thread.start()


def _worker_entrypoint(job: dict) -> None:
    job_id = job["job_id"]
    con = None
    try:
        con = buka_koneksi()
    except Exception:
        print(f"[REASONING WORKER] {job_id} failed to connect to database:\n{traceback.format_exc()}")
        return

    try:
        _wait_in_queue(con, job_id)
        try:
            _execute_job(con, job)
        finally:
            _slot.release()
    except Exception as err:
        print(f"[REASONING WORKER] {job_id} FAILED:\n{traceback.format_exc()}")
        update_job_status(con, job_id, "FAILED", error=str(err), stage="FAILED")
    finally:
        try:
            con.close()
        except Exception:
            pass


def _wait_in_queue(con, job_id: str) -> None:
    """Send heartbeats while waiting for an execution concurrency slot."""
    while not _slot.acquire(blocking=False):
        heartbeat(con, job_id, stage="WAITING_FOR_SLOT")
        time.sleep(QUEUE_HEARTBEAT_INTERVAL)


def _execute_job(con, job: dict) -> None:
    """Run reasoning pipeline and record completion."""
    job_id = job["job_id"]
    update_job_status(con, job_id, "RUNNING", stage="STARTING")

    summary = execute_reasoning(job, dry_run=False)

    update_job_status(
        con,
        job_id,
        "COMPLETED",
        stage="FINISHED",
        result=summary,
    )
    print(f"[REASONING WORKER] {job_id} completed successfully: {summary}")
