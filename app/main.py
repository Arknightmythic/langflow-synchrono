import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

from fastapi import (APIRouter, Body, Depends, FastAPI, HTTPException, Path, Query,
                     Request, Response, status)
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from engine import auth, config, master, sr, udf
from engine import settings as cfg
from engine.sql import ZONE, sq
from worker.celery_app import app as celery

from . import langflow, service

STARTED = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not cfg.AUTH_ENABLED:
        print("[start] x-api-key check OFF (SERVICE_AUTH=off)", flush=True)
    else:
        print(f"[start] x-api-key: {len(cfg.API_KEYS)} keys from SERVICE_API_KEY + table "
              f"{auth.TABLE}", flush=True)
        if not cfg.SUPERUSER_PASSWORD:
            print("[start] SERVICE_SUPERUSER_PASSWORD empty — /api/v1/login disabled",
                  flush=True)
    auth.start_flusher()
    yield
    try:
        auth.flush_uses()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="Synchrono Service (StarRocks)", version="2.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unexpected(request: Request, e: Exception):
    detail = " ".join(str(e).split())[:500]
    print(f"[500] {request.method} {request.url.path} -> {type(e).__name__}: {detail}", flush=True)
    return JSONResponse({"error": type(e).__name__, "detail": detail}, status_code=500)


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok", "uptimeSeconds": round(time.time() - STARTED, 1),
            "storage": "starrocks", "python": sys.version.split()[0]}


@app.get("/health/db", tags=["health"])
def health_db() -> dict:
    started = time.perf_counter()
    n = sr.scalar(f"SELECT count(*) FROM {cfg.T_SERVICE}grading_jobs")
    return {"status": "ok", "gradingJobs": n,
            "roundtripMs": round((time.perf_counter() - started) * 1000, 2)}


@app.get("/udf/synchrono-udf.jar", tags=["health"], include_in_schema=False)
def udf_jar():
    if not udf.jar_version():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "UDF jar not built into this image")
    return FileResponse(udf.JAR_PATH, media_type="application/java-archive")


@app.get("/health_check", tags=["health"])
def health_check():
    try:
        sr.query(f"SELECT 1 FROM {cfg.T_SERVICE}grade_rules LIMIT 1")
        db = "ok"
    except Exception as e:  # noqa: BLE001
        db = f"error: {type(e).__name__}: {' '.join(str(e).split())[:300]}"
    body = {"status": "ok" if db == "ok" else "nok", "chat": "ok", "db": db}
    return body if db == "ok" else JSONResponse(status_code=500, content=body)


@app.post("/api/v1/login", tags=["auth"])
async def login(request: Request):
    form = parse_qs((await request.body()).decode(errors="replace"), keep_blank_values=True)
    missing = [f for f in ("username", "password") if f not in form]
    if missing:
        return JSONResponse(status_code=422, content={"detail": [
            {"type": "missing", "loc": ["body", f], "msg": "Field required", "input": None}
            for f in missing]})
    result = auth.login(form["username"][0], form["password"][0])
    if result is None:
        return JSONResponse(status_code=401, content={"detail": "Incorrect username or password"},
                            headers={"WWW-Authenticate": "Bearer"})
    return result


@app.get("/api/v1/api_key/", tags=["auth"], dependencies=[Depends(auth.require_bearer)])
def list_keys():
    return auth.list_keys()


@app.post("/api/v1/api_key/", tags=["auth"], dependencies=[Depends(auth.require_bearer)])
def create_key(body: dict | None = Body(default=None)):
    name = (body or {}).get("name")
    return auth.create_key(None if name is None else str(name))


@app.delete("/api/v1/api_key/{key_id}", tags=["auth"],
            dependencies=[Depends(auth.require_bearer)])
def delete_key(key_id: str):
    if not auth.delete_key(key_id):
        raise HTTPException(status_code=404, detail="API Key not found")
    return {"detail": "API Key deleted"}


@app.post("/api/v1/run/{flow_id_or_name}", tags=["langflow"],
          dependencies=[Depends(auth.require_key)])
def run_flow(flow_id_or_name: str, body: dict | None = Body(default=None),
             stream: bool = Query(default=False)):
    flow = langflow.find(flow_id_or_name)
    if flow is None:
        raise HTTPException(status_code=404, detail=f"Flow identifier {flow_id_or_name} not found")
    body = body or {}
    tweaks = body.get("tweaks") or {}
    if not isinstance(tweaks, dict):
        raise HTTPException(status_code=422, detail="tweaks harus objek JSON {id_node: {...}}")
    try:
        text = langflow.timed_run(flow, tweaks)
    except langflow.ComponentError as e:
        return JSONResponse(status_code=500, content={"detail": e.detail()})
    return langflow.envelope(flow, text, body.get("input_value"), body.get("session_id"))


grading_routes = APIRouter(prefix="/api/v1/grading", tags=["grading"])


@grading_routes.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
def grading_jobs(response: Response, payload: dict = Body(...)) -> dict:
    try:
        result = service.dispatch_grading({k: v for k, v in payload.items() if v is not None})
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    response.headers["Location"] = f"/api/v1/grading/jobs/{result['fileId']}"
    return result


@grading_routes.get("/jobs/by-id/{job_id}")
def grading_job(job_id: str, panen: bool = Query(default=True)) -> dict:
    return service.grading_status(None, job_id, panen)


@grading_routes.get("/jobs/{file_id}")
def grading_file(file_id: str, panen: bool = Query(default=True)) -> dict:
    return service.grading_status(file_id, None, panen)


@grading_routes.post("/run")
def grading_run(payload: dict = Body(...)) -> dict:
    try:
        return service.grading_sync({k: v for k, v in payload.items() if v is not None})
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


class RulePatch(BaseModel):
    criteria: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    matching: dict[str, Any] | None = None
    updatedBy: str | None = None
    dryRun: bool = False


class GlobalPatch(BaseModel):
    grading: dict[str, Any] | None = None
    matching: dict[str, Any] | None = None
    updatedBy: str | None = None
    dryRun: bool = False


config_routes = APIRouter(prefix="/api/v1/config", tags=["config"])
GRADE = Path(ge=1, le=6)


def _config_answer(result: dict, label: str, dry_run: bool):
    service.log_config(result, label, dry_run)
    if result["problems"]:
        return JSONResponse(json.loads(json.dumps(result, default=str)),
                            status_code=status.HTTP_409_CONFLICT)
    return result


@config_routes.get("/rules")
def rules_all() -> dict:
    return config.read_all(None)


@config_routes.get("/rules/{grade_id}")
def rules_one(grade_id: int = GRADE) -> dict:
    result = config.read_all(grade_id)
    if not result["grades"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Grade {grade_id} tidak ada di konfigurasi.")
    return result


@config_routes.patch("/rules/{grade_id}")
def rules_patch(patch: RulePatch, grade_id: int = GRADE):
    try:
        result = config.update(grade_id, {"criteria": patch.criteria, "score": patch.score,
                                          "matching": patch.matching},
                               by=patch.updatedBy, dry_run=patch.dryRun)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _config_answer(result, f"grade {grade_id}", patch.dryRun)


@config_routes.patch("/global")
def global_patch(patch: GlobalPatch):
    try:
        result = config.update_global({"grading": patch.grading, "matching": patch.matching},
                                      by=patch.updatedBy, dry_run=patch.dryRun)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _config_answer(result, "global", patch.dryRun)


@config_routes.get("/history")
def config_history(gradeId: int | None = Query(None, ge=1, le=6),
                   limit: int = Query(50, ge=1, le=500)) -> dict:
    return {"items": config.history(gradeId, limit)}


@config_routes.get("/versions/{version}")
def config_version(version: str = Path(pattern="^[0-9a-f]{12}$")) -> dict:
    result = config.read_version(version)
    if not result:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Versi konfigurasi {version} tidak dikenal.")
    return result


matching_routes = APIRouter(prefix="/api/v1/matching", tags=["matching"])


@matching_routes.post("/jobs", status_code=202)
def matching_jobs(payload: dict = Body(...)) -> dict:
    try:
        return service.dispatch_matching(payload)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@matching_routes.get("/jobs/{job_id}")
def matching_job(job_id: str) -> dict:
    rows = sr.query(f"SELECT id, file_id, master_file_id, status, current_stage, started_at, "
                    f"completed_at, failed_at, last_error, total_incoming, auto_count, "
                    f"review_count, unmatch_count, conflict_count, "
                    f"CAST(stage_durations AS VARCHAR) AS stage_durations "
                    f"FROM {cfg.T_PORTAL}matching_jobs WHERE id = {sq(job_id)}")
    if not rows:
        return {"found": False, "jobId": job_id, "status": "NOT_FOUND"}
    row = rows[0]
    for key in ("started_at", "completed_at", "failed_at"):
        if row.get(key) is not None:
            row[key] = row[key].isoformat() + ZONE
    if row.get("stage_durations"):
        row["stage_durations"] = json.loads(row["stage_durations"])
    return {"found": True, "done": row["status"] in ("COMPLETED", "FAILED", "CANCELLED"), **row}


master_routes = APIRouter(prefix="/api/v1/masters", tags=["master"])


@master_routes.post("/{master_id}/load", status_code=202)
def master_load(master_id: str, payload: dict = Body(...)) -> dict:
    source = str(payload.get("source") or "").strip()
    if not source:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "source (s3:// uri) wajib diisi")
    celery.send_task("master.load", args=[master_id, source], queue="matching")
    return {"status": "QUEUED", "masterId": master_id, "source": source}


@master_routes.get("/{master_id}")
def master_state(master_id: str) -> dict:
    state = master.status(master_id)
    return {"found": bool(state), **(state or {"masterId": master_id})}


for router in (grading_routes, config_routes, matching_routes, master_routes):
    app.include_router(router, dependencies=[Depends(auth.require_key_rest)])
