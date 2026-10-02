import json
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from . import settings as cfg
from . import sr
from .sql import now_text, sjson, sq

TABLE = f"{cfg.DB_SERVICE}.grading_jobs"
STALE_MESSAGE = ("Pekerja berhenti tanpa kabar (kemungkinan worker restart). "
                 "Job ditandai gagal karena tidak berdetak lebih dari "
                 f"{cfg.STALE_MINUTES} menit.")

REGISTER_COLUMNS = ["job_id", "file_id", "status", "filename", "s3_bucket", "s3_endpoint",
                    "csv_key", "raw_source_key", "parquet_key", "enriched_key", "row_count",
                    "institution_id", "institution_name", "callback_url", "callback_token",
                    "callback_status", "callback_attempts", "created_at", "heartbeat_at"]


def new_job_id() -> str:
    return f"ds-grade-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8]}"


def build_job(payload: dict, defaults: dict | None = None) -> dict:
    p = {**(defaults or {}), **(payload or {})}

    def pick(*names, required=False):
        for name in names:
            value = p.get(name)
            if value not in (None, ""):
                return value
        if required:
            raise ValueError(f"Field wajib tidak ada: {names[0]}. "
                             f"Diterima: {sorted(k for k in p if p[k])}")
        return None

    institution = p.get("institution") or {}
    callback = p.get("callback") or {}
    rows = int(pick("rowCount", "row_count") or 0) or None
    return {
        "job_id": new_job_id(),
        "file_id": str(pick("fileId", "file_id", required=True)),
        "filename": pick("filename", "original_filename"),
        "s3_bucket": str(pick("s3Bucket", "s3_bucket", required=True)),
        "s3_endpoint": pick("s3Endpoint", "s3_endpoint"),
        "csv_key": pick("csvKey", "csv_key"),
        "raw_source_key": pick("rawSourceKey", "raw_source_key"),
        "log_key": pick("logKey", "log_key"),
        "sql_dialect": pick("sqlDialect", "sql_dialect"),
        "source_table": pick("sourceTable", "source_table"),
        "parquet_key": str(pick("parquetKey", "parquet_key", required=True)),
        "enriched_key": pick("enrichedParquetKey", "enriched_key"),
        "row_count": rows,
        "institution_id": institution.get("id") or pick("institution_id"),
        "institution_name": institution.get("name") or pick("institution_name"),
        "callback_url": callback.get("url") or pick("callback_url"),
        "callback_token": callback.get("secretToken") or pick("callback_token"),
    }


def register(job: dict) -> None:
    values = dict(job)
    values["status"] = "QUEUED"
    values["callback_status"] = "PENDING" if job.get("callback_url") else "SKIPPED"
    values["callback_attempts"] = 0
    values["created_at"] = now_text()
    values["heartbeat_at"] = now_text()
    sr.execute(f"INSERT INTO {TABLE} ({', '.join(REGISTER_COLUMNS)}) VALUES "
               f"({', '.join(sq(values.get(c)) for c in REGISTER_COLUMNS)})")


def set_status(job_id: str, status: str, **columns) -> None:
    parts = [f"status = {sq(status)}", f"heartbeat_at = {sq(now_text())}"]
    if status == "RUNNING":
        parts.append(f"started_at = COALESCE(started_at, {sq(now_text())})")
    if status in ("COMPLETED", "FAILED"):
        parts.append(f"finished_at = {sq(now_text())}")
    for key, value in columns.items():
        parts.append(f"{key} = {sjson(value) if key == 'result' else sq(value)}")
    sr.execute(f"UPDATE {TABLE} SET {', '.join(parts)} WHERE job_id = {sq(job_id)}")


def heartbeat(job_id: str, stage: str | None = None) -> None:
    extra = f", stage = {sq(stage)}" if stage else ""
    sr.execute(f"UPDATE {TABLE} SET heartbeat_at = {sq(now_text())}{extra} "
               f"WHERE job_id = {sq(job_id)}")


_last_reap = [0.0]


def reap_stale(every_seconds: int = 60) -> int:
    if time.monotonic() - _last_reap[0] < every_seconds:
        return 0
    _last_reap[0] = time.monotonic()
    stale = sr.query(f"SELECT job_id FROM {TABLE} WHERE status = 'RUNNING' AND "
                     f"heartbeat_at < date_sub(now(), INTERVAL {cfg.STALE_MINUTES} MINUTE)")
    for row in stale:
        sr.execute(f"UPDATE {TABLE} SET status = 'FAILED', finished_at = {sq(now_text())}, "
                   f"error = {sq(STALE_MESSAGE)} WHERE job_id = {sq(row['job_id'])}")
    return len(stale)


READ_COLUMNS = ("job_id, file_id, status, stage, filename, s3_bucket, parquet_key, enriched_key, "
                "parquet_size_bytes, row_count, institution_name, callback_status, "
                "callback_attempts, callback_error, error, grading_duration_ms, kl_load_ms, "
                "created_at, started_at, finished_at, heartbeat_at, "
                "CAST(result AS VARCHAR) AS result")


def fetch(file_id: str | None = None, job_id: str | None = None) -> dict | None:
    if job_id:
        where = f"job_id = {sq(job_id)}"
    elif file_id:
        where = f"file_id = {sq(file_id)}"
    else:
        raise ValueError("Butuh salah satu dari file_id atau job_id")
    rows = sr.query(f"SELECT {READ_COLUMNS} FROM {TABLE} WHERE {where} "
                    f"ORDER BY created_at DESC LIMIT 1")
    if not rows:
        return None
    job = rows[0]
    for key in ("created_at", "started_at", "finished_at", "heartbeat_at"):
        if job.get(key) is not None:
            job[key] = job[key].isoformat() + "+00:00"
    if job.get("result"):
        job["result"] = json.loads(job["result"])
    return job


def status_body(job: dict | None, file_id: str | None, job_id: str | None) -> dict:
    if job is None:
        return {"found": False, "status": "NOT_FOUND", "fileId": file_id, "jobId": job_id,
                "message": "Belum ada job grading untuk berkas ini."}
    return {
        "found": True, "status": job["status"], "jobId": job["job_id"],
        "fileId": job["file_id"], "done": job["status"] in ("COMPLETED", "FAILED"),
        "stage": job["stage"], "queuedAt": job["created_at"], "startedAt": job["started_at"],
        "finishedAt": job["finished_at"], "gradingDurationMs": job["grading_duration_ms"],
        "klLoadMs": job.get("kl_load_ms"), "enrichedParquetKey": job["enriched_key"],
        "parquetSizeBytes": job["parquet_size_bytes"], "recordCount": job["row_count"],
        "callbackStatus": job["callback_status"], "callbackError": job["callback_error"],
        "error": job["error"], "result": job["result"],
    }


def post_json(url: str, body: dict, headers: dict | None = None) -> tuple[bool, str, int]:
    data = json.dumps(body, ensure_ascii=False, default=str).encode()
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    error = None
    for attempt in range(1, cfg.CALLBACK_RETRIES + 1):
        try:
            request = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
            with urllib.request.urlopen(request, timeout=cfg.CALLBACK_TIMEOUT) as response:
                return True, f"HTTP {response.status}", attempt
        except urllib.error.HTTPError as e:
            error = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if 400 <= e.code < 500 and e.code != 429:
                return False, error, attempt
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        if attempt < cfg.CALLBACK_RETRIES:
            time.sleep(2 ** attempt)
    return False, error or "unknown", cfg.CALLBACK_RETRIES


def send_callback(job_id: str, url: str | None, token: str | None, body: dict) -> None:
    if not url:
        return
    headers = {}
    if token:
        headers = {"X-Grading-Signature": token, "Authorization": f"Bearer {token}"}
    ok, detail, attempts = post_json(url, body, headers)
    sr.execute(f"UPDATE {TABLE} SET callback_status = {sq('SENT' if ok else 'FAILED')}, "
               f"callback_attempts = {attempts}, "
               f"callback_error = {sq(None if ok else detail)} WHERE job_id = {sq(job_id)}")
