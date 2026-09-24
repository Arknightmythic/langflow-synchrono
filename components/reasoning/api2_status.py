"""
NODE API-2: Reasoning Status (POST /api/v1/run/reasoning-status)

Polls current progress and results of an AI Reasoning job.
Returns current status (QUEUED, RUNNING, COMPLETED, FAILED) along with execution summary.
"""

import json

from _kolam import pinjam
from _reasoning_jobs import get_job, harvest_stale_jobs
from _shared import Component, Message, MessageTextInput, Output


class ReasoningStatus(Component):
    display_name = "API 2. Reasoning Status"
    description = "Check status and progress of an AI reasoning job."
    icon = "activity"
    name = "ReasoningStatus"

    inputs = [
        MessageTextInput(
            name="file_id",
            display_name="File ID",
            required=False,
            info="Target file ID to look up latest reasoning job.",
        ),
        MessageTextInput(
            name="job_id",
            display_name="Job ID",
            required=False,
            info="Optional specific job ID to look up.",
        ),
    ]
    outputs = [Output(display_name="Status", name="status", method="check_status")]

    def check_status(self) -> Message:
        file_id = (self.file_id or "").strip() or None
        job_id = (self.job_id or "").strip() or None

        if not file_id and not job_id:
            raise ValueError("Must provide either file_id or job_id")

        with pinjam() as con:
            harvest_stale_jobs(con)
            job = get_job(con, file_id=file_id, job_id=job_id)

        if job is None:
            response = {
                "found": False,
                "status": "NOT_FOUND",
                "fileId": file_id,
                "jobId": job_id,
                "message": "No reasoning job found for the specified file.",
            }
        else:
            response = {
                "found": True,
                "status": job["status"],
                "jobId": job["job_id"],
                "fileId": job["file_id"],
                "done": job["status"] in ("COMPLETED", "FAILED"),
                "stage": job["stage"],
                "queuedAt": job["created_at"],
                "heartbeatAt": job["heartbeat_at"],
                "updatedAt": job["updated_at"],
                "error": job["error"],
                "result": job["result"],
            }

        return Message(text=json.dumps(response, ensure_ascii=False))
