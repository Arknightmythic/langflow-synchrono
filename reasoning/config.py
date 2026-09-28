import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

REASONING_AI_BASE_URL = (
    os.getenv("REASONING_AI_BASE_URL", "").strip().strip('"')
    or os.getenv("OLLAMA_LOCAL_BASE_URL", "").strip().strip('"')
    or os.getenv("NORMALISASI_AI_BASE_URL", "").strip().strip('"')
    or "https://ollama.mcp-tools.my.id"
)

REASONING_AI_MODEL = os.getenv("REASONING_AI_MODEL", "").strip() or "gemma3:12b"
REASONING_AI_API_KEY = os.getenv("REASONING_AI_API_KEY", "").strip() or os.getenv("OLLAMA_API_KEY", "").strip()
REASONING_AI_TIMEOUT = int(os.getenv("REASONING_AI_TIMEOUT", "120"))
REASONING_AI_RETRIES = int(os.getenv("REASONING_AI_RETRIES", "3"))
REASONING_AI_RETRY_DELAY = float(os.getenv("REASONING_AI_RETRY_DELAY", "1.5"))

REASONING_MAX_CONCURRENT = int(os.getenv("REASONING_MAX_CONCURRENT", "1"))
REASONING_STALE_MINUTES = int(os.getenv("REASONING_STALE_MINUTES", "15"))

# Default master parquet path (empty by default to fall back to pg.public.master unless configured)
MASTER_PARQUET_PATH = os.getenv("MASTER_PARQUET_PATH", "").strip()

# PostgreSQL DSN
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB = os.getenv("PG_DB", "synchrono")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

PG_DSN = (
    os.getenv("PG_DSN", "").strip()
    or os.getenv("DATABASE_URL", "").strip()
    or f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER}"
    + (f" password={PG_PASSWORD}" if PG_PASSWORD else "")
)

def _resolve_s3_endpoint() -> str:
    raw = os.getenv("S3_ENDPOINT", "").strip()
    if not raw:
        raw = "seaweedfs:8333"

    clean = raw.replace("http://", "").replace("https://", "").rstrip("/")
    host = clean.split(":")[0]
    port = clean.split(":")[1] if ":" in clean else "8333"

    import socket
    try:
        socket.gethostbyname(host)
        return clean
    except (socket.gaierror, OSError):
        return f"172.18.0.2:{port}"

# S3 / SeaweedFS settings
S3_ENDPOINT = _resolve_s3_endpoint()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "synchrono").strip()
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "synchrono123").strip()
S3_USE_SSL = os.getenv("S3_USE_SSL", "false").strip().lower() in ("1", "true", "yes")

# DuckDB limits
DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "3GB").strip()
DUCKDB_TEMP_DIR = os.getenv("DUCKDB_TEMP_DIR", "/tmp/duckdb_spill").strip()

# Redis & Celery Event-Driven Settings
REDIS_HOST = os.getenv("REDIS_HOST", "localhost").strip()
REDIS_PORT = os.getenv("REDIS_PORT", "6379").strip()
REDIS_DB = os.getenv("REDIS_DB", "0").strip()
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "").strip()

REDIS_URL = (
    os.getenv("REDIS_URL", "").strip()
    or (f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}" if REDIS_PASSWORD
        else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")
)

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "").strip() or REDIS_URL
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "").strip() or REDIS_URL
CELERY_CONCURRENCY = int(os.getenv("CELERY_CONCURRENCY", "4"))
USE_CELERY = os.getenv("USE_CELERY", "true").lower() in ("true", "1", "yes")

# Distributed Pattern Lock & Anti-Thundering-Herd
PATTERN_LOCK_TIMEOUT_SECONDS = int(os.getenv("PATTERN_LOCK_TIMEOUT_SECONDS", "45"))
PATTERN_LOCK_POLL_INTERVAL = float(os.getenv("PATTERN_LOCK_POLL_INTERVAL", "0.3"))
PATTERN_LOCK_MAX_WAIT = int(os.getenv("PATTERN_LOCK_MAX_WAIT", "30"))

