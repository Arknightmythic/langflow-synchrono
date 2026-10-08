import threading
import time
import traceback

from engine import conversion, grading, jobs, master, matching

from .celery_app import app


def _summary(error: BaseException) -> str:
    return f"{type(error).__name__}: {' '.join(str(error).split())}"[:500]


class Heartbeat:
    def __init__(self, beat, every: int = 30):
        self.beat, self.every = beat, every
        self.stage = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self.every):
            try:
                self.beat(self.stage)
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


@app.task(name="grading.run")
def grade(job: dict) -> None:
    job_id = job["job_id"]
    try:
        jobs.set_status(job_id, "RUNNING", stage="G1 open session")
        started = time.perf_counter()
        with Heartbeat(lambda stage: jobs.heartbeat(job_id, stage)) as pulse:
            def report(stage: str) -> None:
                pulse.stage = stage
                jobs.heartbeat(job_id, stage)
            if conversion.convert_first(job, report=report):
                jobs.set_status(job_id, "RUNNING", stage="G1 open session")
            output = grading.run(job, report=report)
        result, session = output["result"], output["session"]
        duration = int((time.perf_counter() - started) * 1000)
        result["gradingDurationMs"] = duration
        jobs.set_status(job_id, "COMPLETED", stage="selesai", result=result,
                        enriched_key=session["enriched_key"],
                        parquet_size_bytes=session.get("parquet_size_bytes"),
                        row_count=session["row_count"], grading_duration_ms=duration,
                        kl_load_ms=(session.get("kl_load") or {}).get("totalMs"), error=None)
        jobs.send_callback(job_id, job.get("callback_url"), job.get("callback_token"), result)
    except Exception as e:  # noqa: BLE001
        print(f"[W] {job_id} FAILED:\n{traceback.format_exc()}", flush=True)
        message = _summary(e)
        try:
            jobs.set_status(job_id, "FAILED", stage="gagal", error=message)
            jobs.send_callback(job_id, job.get("callback_url"), job.get("callback_token"),
                               {"fileId": job["file_id"], "status": "FAILED", "error": message})
        except Exception:  # noqa: BLE001
            print(f"[W] {job_id} failure could not be recorded:\n{traceback.format_exc()}")


@app.task(name="matching.run")
def match(job: dict) -> None:
    try:
        with Heartbeat(lambda stage: matching.mark(job["job_id"])):
            matching.run(job, report=lambda stage: print(f"[M] {job['job_id']} {stage}",
                                                         flush=True))
        try:
            release_kl.delay(job["file_id"], job["job_id"])
        except Exception as e:  # noqa: BLE001
            print(f"[M] {job['job_id']} K/L release not queued: {_summary(e)}", flush=True)
    except matching.Cancelled:
        print(f"[M] {job['job_id']} cancelled by operator", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[M] {job['job_id']} FAILED:\n{traceback.format_exc()}", flush=True)
        matching.fail(job, _summary(e))


@app.task(name="kl.release")
def release_kl(file_id: str, job_id: str) -> None:
    """Queued after a matching job has finished and sent its callback, so dropping the file's
    K/L rows never holds up the job or the API."""
    matching.release_incoming({"file_id": file_id, "job_id": job_id})


@app.task(name="master.load")
def load_master(master_id: str, source: str) -> None:
    master.load(master_id, source, report=lambda stage: print(f"[MASTER] {master_id} {stage}",
                                                              flush=True))
