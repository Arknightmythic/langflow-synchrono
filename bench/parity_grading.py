"""Grading parity: DuckDB service (srb-old) vs engine.grading on the same files."""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/srv")

from engine import duck, grading, sr  # noqa: E402
from engine import settings as cfg  # noqa: E402

OLD = "http://srb-old:8000/api/v1/grading/run"
FILES = sys.argv[1:] or ["um-A/raw/data.csv", "um-B/raw/data.csv", "um-C/raw/data.csv",
                         "um-D/raw/data.csv", "um-E/raw/data.csv", "fmt-csv/raw/data.csv",
                         "fmt-pq/raw/data.parquet", "fmt-xlsx/raw/data.xlsx"]
IGNORE = {"gradingDurationMs", "configVersion", "klStorage", "enrichedParquetKey",
          "parquetSizeBytes", "referenceData", "normalization"}


def old_grade(file_id: str, key: str) -> dict:
    body = {"fileId": file_id, "s3Bucket": "bench", "rawSourceKey": key, "parquetKey": key,
            "enrichedParquetKey": f"{file_id}/old/enriched.parquet"}
    request = urllib.request.Request(OLD, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "x-api-key": "bench-key"})
    with urllib.request.urlopen(request, timeout=3600) as response:
        return json.loads(response.read())


def diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k in IGNORE:
                continue
            out += diff(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, float) or isinstance(b, float):
        return [] if a is not None and b is not None and abs(a - b) < 1e-9 else (
            [] if a == b else [f"{path}: {a!r} != {b!r}"])
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


con = duck.connect()
for key in FILES:
    file_id = "p-" + key.split("/")[0]
    t = time.perf_counter()
    old = old_grade(file_id, key)
    old_s = time.perf_counter() - t
    job = {"job_id": f"parity-{file_id}", "file_id": file_id, "s3_bucket": "bench",
           "raw_source_key": key, "parquet_key": key,
           "enriched_key": f"{file_id}/new/enriched.parquet"}
    t = time.perf_counter()
    new = grading.run(job)["result"]
    new_s = time.perf_counter() - t
    problems = diff(old, new)
    a = f"s3://bench/{file_id}/old/enriched.parquet"
    b = f"s3://bench/{file_id}/new/enriched.parquet"
    cols_a = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{a}'").fetchall()]
    cols_b = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{b}'").fetchall()]
    rows = con.execute(f"SELECT count(*) FROM (SELECT * FROM '{a}' EXCEPT ALL "
                       f"SELECT * FROM '{b}')").fetchone()[0] if cols_a == cols_b else "n/a"
    kl = sr.scalar(f"SELECT count(*) FROM {cfg.DB_KL}.records WHERE file_id = '{file_id}'")
    print(f"{key:28s} grade old {old['summary']['gradeLetter']} new {new['summary']['gradeLetter']}"
          f" | score {old['summary']['qualityScore']}/{new['summary']['qualityScore']}"
          f" | json diffs {len(problems)} | enriched cols same {cols_a == cols_b}, rows differ {rows}"
          f" | kl rows {kl:,} | old {old_s:.1f}s new {new_s:.1f}s")
    for p in problems[:8]:
        print("     ", p)
