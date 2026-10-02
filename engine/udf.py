import hashlib
import os
import threading
import time

from . import settings as cfg
from . import sr

JAR_PATH = os.getenv("UDF_JAR_PATH", "/srv/udf/synchrono-udf.jar")
_lock = threading.Lock()
_state: dict = {}


def jar_version() -> str | None:
    try:
        with open(JAR_PATH, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()[:10]
    except OSError:
        return None


def names() -> dict[str, str]:
    key = hashlib.md5(f"{jar_version()}|{cfg.UDF_JAR_URL}".encode()).hexdigest()[:10]
    return {"jw": f"{cfg.DB_SERVICE}.synchrono_jw_{key}",
            "round": f"{cfg.DB_SERVICE}.synchrono_round_{key}"}


def _works(fn: dict) -> bool:
    try:
        row = sr.query(f"SELECT {fn['jw']}('martha', 'marhta') AS jw, "
                       f"{fn['round']}(CAST(82.25 AS DOUBLE), 1) AS r")[0]
        return abs(row["jw"] - 0.9611111111111111) < 1e-12 and row["r"] == 82.3
    except Exception:  # noqa: BLE001
        return False


def _create(fn: dict) -> None:
    for key, args, ret, symbol in (("jw", "STRING, STRING", "DOUBLE", "synchrono.udf.JaroWinkler"),
                                   ("round", "DOUBLE, INT", "DOUBLE", "synchrono.udf.DuckRound")):
        try:
            sr.execute(f'CREATE FUNCTION {fn[key]}({args}) RETURNS {ret} PROPERTIES ('
                       f'"symbol" = "{symbol}", "type" = "StarrocksJar", '
                       f'"file" = "{cfg.UDF_JAR_URL}")')
        except Exception as e:  # noqa: BLE001
            if "exist" not in str(e).lower():
                raise


def ensure() -> dict | None:
    """Register the Jaro-Winkler/round UDFs once; None means score in DuckDB instead."""
    with _lock:
        if "result" in _state and (_state["result"] or time.monotonic() - _state["at"] < 300):
            return _state["result"]
        result = None
        if cfg.UDF_JAR_URL and not jar_version():
            print(f"[UDF] jar not found at {JAR_PATH}, scoring stays in DuckDB", flush=True)
        elif cfg.UDF_JAR_URL:
            fn = names()
            try:
                if not _works(fn):
                    _create(fn)
                if _works(fn):
                    result = fn
                else:
                    print("[UDF] functions registered but not returning DuckDB values", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[UDF] not available, scoring stays in DuckDB: "
                      f"{' '.join(str(e).split())[:200]}", flush=True)
        _state.update(result=result, at=time.monotonic())
        return result
