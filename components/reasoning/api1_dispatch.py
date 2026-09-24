"""
NODE API-1: Reasoning Dispatch (POST /api/v1/run/reasoning-dispatch)

Queues an AI Reasoning job, spawns a background worker thread,
and immediately responds with 202 Accepted.
"""

import json

from _kolam import pinjam
from _reasoning_jobs import build_job_payload, harvest_stale_jobs, record_job
from _reasoning_worker import dispatch_worker
from _shared import Component, Message, MessageTextInput, Output


class ReasoningDispatch(Component):
    display_name = "API 1. Reasoning Dispatch"
    description = "Queue AI reasoning job, release background worker, return 202 QUEUED."
    icon = "send"
    name = "ReasoningDispatch"

    inputs = [
        MessageTextInput(
            name="payload",
            display_name="Payload JSON",
            required=False,
            info="Full JSON payload containing fileId or file_id.",
        ),
        MessageTextInput(
            name="file_id",
            display_name="File ID",
            required=False,
            info="File identifier to process pending manual matches.",
        ),
        MessageTextInput(
            name="llm_model",
            display_name="LLM Model",
            required=False,
            info="Optional local LLM model override.",
        ),
    ]
    outputs = [Output(display_name="Accepted", name="accepted", method="dispatch")]

    def dispatch(self) -> Message:
        payload_data = {}
        raw_text = (self.payload or "").strip()
        if raw_text:
            try:
                payload_data = json.loads(raw_text)
            except json.JSONDecodeError as err:
                raise ValueError(f"Payload is not valid JSON: {err}") from err
            if not isinstance(payload_data, dict):
                raise ValueError("Payload must be a JSON object")

        job = build_job_payload(
            payload_data,
            defaults={
                "file_id": self.file_id,
                "llm_model": self.llm_model,
            },
        )

        with pinjam() as con:
            harvested = harvest_stale_jobs(con)
            if harvested:
                print(f"[REASONING DISPATCH] Harvested {harvested} stale jobs")
            record_job(con, job)

        dispatch_worker(job)
        print(f"[REASONING DISPATCH] Queued job {job['job_id']} for file {job['file_id']}")

        return Message(
            text=json.dumps(
                {
                    "status": "QUEUED",
                    "jobId": job["job_id"],
                    "fileId": job["file_id"],
                    "message": "AI Reasoning job successfully queued for processing.",
                },
                ensure_ascii=False,
            )
        )
