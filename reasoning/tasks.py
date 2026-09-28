"""
Celery Tasks for AI Reasoning Engine.
Executes asynchronous reasoning jobs dispatched via message broker.
"""

from __future__ import annotations

import logging
import traceback

from reasoning.celery_app import celery_app
from reasoning.core import execute_reasoning
from reasoning.db import get_db_connection
from reasoning.jobs import update_job_status

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="reasoning.tasks.process_reasoning_batch")
def process_reasoning_batch(self, job: dict) -> dict:
    """
    Celery task that executes an AI reasoning job asynchronously.
    Updates reasoning_jobs status in PostgreSQL and tracks progress.
    """
    job_id = job.get("job_id") or self.request.id
    file_id = job.get("file_id")
    dry_run = job.get("dry_run", False)
    limit = job.get("limit")

    logger.info("Starting Celery reasoning task: job_id=%s, file_id=%s", job_id, file_id)

    with get_db_connection() as con:
        update_job_status(con, job_id, "RUNNING", stage="STARTING")

    try:
        summary = execute_reasoning(job, dry_run=dry_run, limit=limit)

        with get_db_connection() as con:
            update_job_status(
                con,
                job_id,
                "COMPLETED",
                stage="COMPLETED",
                result=summary,
                error=None,
            )
        logger.info("Completed Celery reasoning task: job_id=%s, total_rows=%s", job_id, summary.get("total_rows"))
        return summary

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"
        stack = traceback.format_exc()
        logger.error("Failed Celery reasoning task: job_id=%s, error=%s", job_id, err_msg)

        with get_db_connection() as con:
            update_job_status(
                con,
                job_id,
                "FAILED",
                stage="FAILED",
                error=f"{err_msg}\n{stack}",
            )
        raise exc
