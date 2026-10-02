import time

from engine import grading, jobs, matching
from worker.celery_app import app as celery


def dispatch_grading(payload: dict, defaults: dict | None = None) -> dict:
    job = jobs.build_job(payload, defaults)
    reason = grading.rejected_format(job.get("raw_source_key") or job.get("csv_key")
                                     or job.get("parquet_key") or "")
    if reason:
        raise ValueError(reason)
    reaped = jobs.reap_stale()
    if reaped:
        print(f"[API1] {reaped} stale jobs marked failed", flush=True)
    jobs.register(job)
    celery.send_task("grading.run", args=[job], queue="grading")
    print(f"[API1] {job['job_id']} queued for file {job['file_id']}", flush=True)
    return {"status": "QUEUED", "jobId": job["job_id"], "fileId": job["file_id"],
            "message": "Grading job successfully queued for processing."}


def grading_status(file_id: str | None, job_id: str | None, reap: bool = True) -> dict:
    if reap:
        jobs.reap_stale()
    return jobs.status_body(jobs.fetch(file_id=file_id, job_id=job_id), file_id, job_id)


def grading_sync(payload: dict) -> dict:
    job = jobs.build_job(payload)
    started = time.perf_counter()
    result = grading.run(job)["result"]
    result["gradingDurationMs"] = int((time.perf_counter() - started) * 1000)
    return result


def dispatch_matching(payload: dict) -> dict:
    job = matching.build_job(payload)
    matching.register(job)
    celery.send_task("matching.run", args=[job], queue="matching")
    print(f"[M] {job['job_id']} accepted for file {job['file_id']} x master "
          f"{job['master_file_id']}", flush=True)
    return {"status": "IN_PROGRESS", "jobId": job["job_id"],
            "message": "Job pencocokan data diterima dan sedang diproses oleh "
                       "Data Science worker."}


def log_config(result: dict, label: str, dry_run: bool) -> None:
    if result["problems"]:
        print(f"[config] {label} REJECTED: {len(result['problems'])} problems", flush=True)
        for p in result["problems"]:
            print(f"   - {p}", flush=True)
    else:
        action = "validated" if dry_run else "saved"
        print(f"[config] {label} {action}, {len(result.get('changed') or [])} fields changed"
              + (f", version {result['configVersion']}" if result.get("configVersion") else ""),
              flush=True)
