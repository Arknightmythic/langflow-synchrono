import os
import secrets


def env(name: str, default: str = "", *aliases: str) -> str:
    for key in (name, *aliases):
        value = os.getenv(key)
        if value is not None and value.strip().strip('"') != "":
            return value.strip().strip('"')
    return default


def env_int(name: str, default: int, *aliases: str) -> int:
    value = env(name, "", *aliases)
    return int(value) if value else default


def env_flag(name: str, default: bool = False, *aliases: str) -> bool:
    value = env(name, "", *aliases).lower()
    return default if not value else value in ("1", "true", "yes", "on")


def env_bytes(name: str, default: int = 0) -> int:
    """'16GB', '512M' or a plain byte count; empty = default."""
    value = env(name).upper().replace(" ", "")
    if not value:
        return default
    for suffix, factor in (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024),
                           ("T", 1024 ** 4), ("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024),
                           ("B", 1)):
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * factor)
    return int(value)


SR_HOST = env("STARROCKS_HOST", "192.168.2.107")
SR_PORT = env_int("STARROCKS_PORT", 30930)
SR_USER = env("STARROCKS_USER", "root")
SR_PASSWORD = env("STARROCKS_PASSWORD")
SR_STREAM_LOAD_URL = env("STARROCKS_STREAM_LOAD_URL", "http://192.168.2.107:30888").rstrip("/")
SR_REPLICATION = env_int("STARROCKS_REPLICATION", 1)
SR_BUCKETS = env_int("STARROCKS_BUCKETS", 8)
SR_QUERY_TIMEOUT = env_int("STARROCKS_QUERY_TIMEOUT", 7200)
SR_ENABLE_SPILL = env("STARROCKS_ENABLE_SPILL", "true").lower() == "true"
# Per-query memory cap for this service's sessions only; 0 = the BE limit applies.
SR_QUERY_MEM_LIMIT = env_bytes("STARROCKS_QUERY_MEM_LIMIT")
# Pass 3 runs in parts of about this many blocking pairs, so a part fits in BE memory; 0 = one part.
SR_PASS3_TARGET_PAIRS = env_int("STARROCKS_PASS3_TARGET_PAIRS", 20_000_000)
# Every part scans the master again, so Pass 3 is only split above this many pairs.
SR_PASS3_SPLIT_ABOVE_PAIRS = env_int("STARROCKS_PASS3_SPLIT_ABOVE_PAIRS", 40_000_000)
# Counting the pairs costs one extra join, so smaller masters always run in one part.
SR_PASS3_MIN_MASTER_ROWS = env_int("STARROCKS_PASS3_MIN_MASTER_ROWS", 5_000_000)

# Service, K/L and portal tables share one database; each table name starts with the database
# it used to live in. T_* is "<database>.<table prefix>", e.g. f"{cfg.T_KL}records".
# The master keeps a database of its own.
DB = env("DB_STARROCK", "syncrono_starrocks")
DB_MASTER = env("DB_MASTER", "syncrono_master")
P_SERVICE, P_KL, P_PORTAL = "syncrono_service_", "syncrono_kl_", "syncrono_portal_"
T_SERVICE, T_KL, T_PORTAL = f"{DB}.{P_SERVICE}", f"{DB}.{P_KL}", f"{DB}.{P_PORTAL}"
# Drop a file's K/L rows once its matching job is done (background task kl.release); a later
# matching job loads them again from enriched.parquet. false keeps them.
KL_DROP_AFTER_MATCHING = env_flag("KL_DROP_AFTER_MATCHING", True)

S3_ENDPOINT = env("S3_ENDPOINT", "seaweedfs:8333")
S3_KEY = env("S3_ACCESS_KEY", "synchrono")
S3_SECRET = env("S3_SECRET_KEY", "synchrono123")
S3_USE_SSL = env("S3_USE_SSL", "false").lower() == "true"

DUCKDB_MEMORY_LIMIT = env("DUCKDB_MEMORY_LIMIT", "3GB")
DUCKDB_TEMP_DIR = env("DUCKDB_TEMP_DIR", "/tmp/duckdb-spill")
DUCKDB_THREADS = env_int("DUCKDB_THREADS", 0)

WORK_DIR = env("WORK_DIR", "/tmp/synchrono-work")
STREAM_LOAD_FILE_BYTES = env("STREAM_LOAD_FILE_BYTES", "256MB")

REGION_PARQUET = (os.getenv("REGION_PARQUET") if os.getenv("REGION_PARQUET") is not None
                  else os.getenv("WILAYAH_PARQUET",
                                 "s3://syncrono-master/wilayah/master_wilayah_nik.parquet")
                  ).strip().strip('"')
REGION_S3_ENDPOINT = env("REGION_S3_ENDPOINT", "", "WILAYAH_S3_ENDPOINT")
REGION_S3_KEY = env("REGION_S3_KEY", "", "WILAYAH_S3_KEY")
REGION_S3_SECRET = env("REGION_S3_SECRET", "", "WILAYAH_S3_SECRET")
REGION_STRICT_KECAMATAN = env("REGION_STRICT_KECAMATAN", "0", "WILAYAH_KECAMATAN_TEGAS") == "1"

API_KEYS = [k.strip() for k in env("SERVICE_API_KEY").split(",") if k.strip()]
AUTH_ENABLED = env("SERVICE_AUTH", "on").lower() not in ("off", "0", "false")
SUPERUSER = env("SERVICE_SUPERUSER", "admin", "LANGFLOW_SUPERUSER")
SUPERUSER_PASSWORD = env("SERVICE_SUPERUSER_PASSWORD", "", "LANGFLOW_SUPERUSER_PASSWORD")
SECRET_KEY = env("SERVICE_SECRET_KEY") or secrets.token_urlsafe(48)
ACCESS_TOKEN_SECONDS = env_int("SERVICE_ACCESS_TOKEN_SECONDS", 3600)
REFRESH_TOKEN_SECONDS = env_int("SERVICE_REFRESH_TOKEN_SECONDS", 7 * 86400)

BROKER_URL = env("CELERY_BROKER_URL", "redis://valkey:6379/0")
VISIBILITY_TIMEOUT = env_int("CELERY_VISIBILITY_TIMEOUT", 6 * 3600)

STALE_MINUTES = env_int("GRADING_STALE_MINUTES", 15)
CALLBACK_RETRIES = env_int("CALLBACK_RETRIES", 3, "GRADING_CALLBACK_RETRIES")
CALLBACK_TIMEOUT = env_int("CALLBACK_TIMEOUT", 20, "GRADING_CALLBACK_TIMEOUT")

COLUMN_AI_MODE = env("NORMALISASI_AI", "nama_saja").lower()
COLUMN_AI_MODEL = env("NORMALISASI_AI_MODEL", "gemma4:31b")
COLUMN_AI_TIMEOUT = env_int("NORMALISASI_AI_TIMEOUT", 60)
COLUMN_AI_SAMPLES = env_int("NORMALISASI_AI_SAMPEL", 5)
COLUMN_AI_ATTEMPTS = env_int("NORMALISASI_AI_PERCOBAAN", 3)
COLUMN_AI_RETRY_DELAY = float(env("NORMALISASI_AI_JEDA", "1.5"))
COLUMN_AI_BASE_URL = env("NORMALISASI_AI_BASE_URL", "", "OLLAMA_LOCAL_BASE_URL",
                         "OLLAMA_CLOUD_BASE_URL")
COLUMN_AI_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")
COLUMN_AI_EXTERNAL_SAMPLES = env("NORMALISASI_AI_IZIN_SAMPEL_LUAR", "0") == "1"

REASONING_AI_BASE_URL = env("REASONING_AI_BASE_URL")
REASONING_AI_MODEL = env("REASONING_AI_MODEL", "gemma3:12b")
REASONING_AI_API_KEY = env("REASONING_AI_API_KEY", "ollama")
REASONING_AI_TIMEOUT = env_int("REASONING_AI_TIMEOUT", 30)
REASONING_AI_RETRIES = env_int("REASONING_AI_RETRIES", 2)
REASONING_AI_MAX_PATTERNS = env_int("REASONING_AI_MAX_PATTERNS_PER_JOB", 25)
REASONING_AI_BUDGET_SECONDS = float(env("REASONING_AI_BUDGET_SECONDS", "90"))
REASONING_AI_ALLOW_EXTERNAL = env("REASONING_AI_ALLOW_EXTERNAL", "0") == "1"

CONVERTER_URLS = {
    "postgresql": env("KONVERTER_URL", "http://konverter:8390"),
    "sqlserver": env("KONVERTER_MSSQL_URL", "http://konverter-mssql:8391"),
    "oracle": env("KONVERTER_ORACLE_URL", "http://konverter-oracle:8392"),
    "mysql": env("KONVERTER_MYSQL_URL", "http://konverter-mysql:8393"),
}
CONVERTER_TIMEOUT = env_int("KONVERTER_BATAS_DETIK", 2100)

UDF_JAR_URL = env("UDF_JAR_URL")

ENGINE_ACTOR = "DataScienceMatchingEngine"
