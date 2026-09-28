from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from reasoning.db import get_db_connection
from reasoning.jobs import harvest_stale_jobs
from reasoning.router import reasoning_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with get_db_connection() as con:
            harvested = harvest_stale_jobs(con)
            if harvested:
                print(f"[FASTAPI REASONING] Harvested {harvested} stale jobs at startup")
    except Exception as e:
        print(f"[FASTAPI REASONING] Startup warning: {e}")
    yield


app = FastAPI(
    title="Synchrono AI Reasoning Service",
    description="Standalone lightweight FastAPI microservice for AI Reasoning on DuckDB & Gemma 3:12B",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(reasoning_router)


@app.get("/")
def root():
    return {
        "service": "Synchrono AI Reasoning",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "dispatch": "POST /v1/reasoning/dispatch",
            "trigger": "POST /v1/reasoning/trigger/{file_id:path}",
            "status": "GET /v1/reasoning/status/{file_id:path}",
            "clear_cache": "POST /v1/reasoning/clear-cache",
            "run_sync": "POST /v1/reasoning/run-sync/{file_id:path}",
            "health": "GET /v1/reasoning/health",
        },
    }
