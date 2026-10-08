"""syncrono_kl_enriched against enriched.parquet: python bench/parity_enriched.py [fileId ...]

Without file ids it checks the last 20 files graded since the table exists (their grading result
has enrichedStorage). Every parquet row must be in the table, in parquet order (row_no), with the
same columns and values (NaN and infinity become null), and enrichedStorage.columns in the
grading result must be the parquet's column order. Only reads.
"""
import json
import sys

sys.path.insert(0, "/srv")

from engine import duck, grading, sr  # noqa: E402
from engine import settings as cfg  # noqa: E402
from engine.sql import sq  # noqa: E402

TABLE = f"{cfg.T_KL}enriched"


def latest_jobs(file_ids: list[str]) -> list[dict]:
    where = (f"AND file_id IN ({', '.join(sq(f) for f in file_ids)})" if file_ids
             else "AND json_query(result, '$.enrichedStorage') IS NOT NULL")
    rows = sr.query(f"SELECT file_id, s3_bucket, enriched_key, CAST(result AS STRING) AS result, "
                    f"row_number() OVER (PARTITION BY file_id ORDER BY created_at DESC) AS k "
                    f"FROM {cfg.T_SERVICE}grading_jobs WHERE status = 'COMPLETED' {where}")
    rows = sorted((r for r in rows if r["k"] == 1), key=lambda r: r["file_id"])
    return rows if file_ids else rows[-20:]


def check(con, job: dict) -> bool:
    path = f"s3://{job['s3_bucket']}/{job['enriched_key']}"
    described = [(r[0], r[1]) for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}', file_row_number = true)").fetchall()
        if r[0] != "file_row_number"]
    fields = ", ".join(f"{grading.q(n)}: {grading._json_value(n, k)}" for n, k in described)
    expected = {r[0]: r[1:] for r in con.execute(f"""
        SELECT file_row_number + 1, nik_trusted, is_anomaly, {sr.clean_text('anomaly_type')},
               to_json({{{fields}}})
          FROM read_parquet('{path}', file_row_number = true)""").fetchall()}
    got = sr.query(f"SELECT row_no, nik_trusted, is_anomaly, anomaly_type, "
                   f"CAST(data AS STRING) AS data FROM {TABLE} WHERE file_id = {sq(job['file_id'])}")
    differ = 0
    for r in got:
        e = expected.get(r["row_no"])
        flags = tuple(None if r[k] is None else bool(r[k]) for k in ("nik_trusted", "is_anomaly"))
        if e is None or flags != e[:2] or r["anomaly_type"] != e[2] \
                or json.loads(r["data"]) != json.loads(e[3]):
            differ += 1
    stored = (json.loads(job["result"] or "{}").get("enrichedStorage") or {}).get("columns")
    order_ok = stored == [n for n, _ in described]
    ok = len(got) == len(expected) and differ == 0 and order_ok
    print(f"{'ok  ' if ok else 'FAIL'} {job['file_id']}: parquet {len(expected):,} rows, "
          f"table {len(got):,} rows, {differ:,} rows differ, column order "
          f"{'same' if order_ok else 'DIFFERENT (or missing in the grading result)'}", flush=True)
    return ok


def safe_check(con, job: dict) -> bool:
    try:
        return check(con, job)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {job['file_id']}: {type(e).__name__}: {' '.join(str(e).split())[:200]}",
              flush=True)
        return False


con = duck.connect()
jobs = latest_jobs(sys.argv[1:])
if not jobs:
    raise SystemExit("no completed grading job with enrichedStorage found")
results = [safe_check(con, job) for job in jobs]
print(f"{sum(results)} of {len(results)} files identical")
raise SystemExit(0 if all(results) else 1)
