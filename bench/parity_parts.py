"""Pass 3 split into parts vs one part, row by row: python bench/parity_parts.py [--target N] [A B ...]

Grades each file once with engine.grading, then runs engine.matching twice on it: Pass 3 as one
statement, and Pass 3 forced into parts of about N blocking pairs (default 200,000). Both
results must be identical; no DuckDB service is needed.
"""
import json
import sys
import time

sys.path.insert(0, "/srv")

from engine import duck, grading, matching, sr  # noqa: E402
from engine import settings as cfg  # noqa: E402

GRADES = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
HARNESS = "http://srb-cb:8000"
COLUMNS = ("id_incoming, master_nik, score, status, method, rank_conflict, pattern_group, "
           "reasoning, CAST(incoming_snapshot AS VARCHAR) AS i_snap, "
           "CAST(master_snapshot AS VARCHAR) AS m_snap")

args = sys.argv[1:]
target = 200_000
if args[:1] == ["--target"]:
    target, args = int(args[1]), args[2:]
letters = args or list(GRADES)
stamp = int(time.time())
con = duck.connect(s3=False)
sr.attach(con)


def grade(letter: str) -> str:
    file_id, key = f"pp-um-{letter}", f"um-{letter}/raw/data.csv"
    grading.run({"job_id": f"pp-grade-{letter}-{stamp}", "file_id": file_id, "s3_bucket": "bench",
                 "raw_source_key": key, "parquet_key": key,
                 "enriched_key": f"{file_id}/new/enriched.parquet"})
    return file_id


def match(letter: str, file_id: str, label: str, target_pairs: int, min_rows: int) -> dict:
    cfg.SR_PASS3_TARGET_PAIRS, cfg.SR_PASS3_MIN_MASTER_ROWS = target_pairs, min_rows
    cfg.SR_PASS3_SPLIT_ABOVE_PAIRS = 0
    job_id = f"pp-{label}-{letter}-{stamp}"
    job = matching.build_job({
        "jobId": job_id, "fileId": file_id, "masterFileId": "um-master", "actor": "bench",
        "callbackUrl": f"{HARNESS}/cb/new", "s3Bucket": "bench",
        "incomingFile": {"s3Key": f"{file_id}/new/enriched.parquet"},
        "masterDataFile": {"s3Key": "master/um-master.parquet"}, "grade": GRADES[letter]})
    matching.register(job)
    t = time.perf_counter()
    matching.run(job)
    seconds = time.perf_counter() - t
    sr.pull(con, label, f"SELECT {COLUMNS} FROM {cfg.T_PORTAL}matching_results "
                        f"WHERE job_id = '{job_id}'")
    meta = sr.query(f"SELECT CAST(blocking_metrics AS VARCHAR) AS b, "
                    f"CAST(stage_durations AS VARCHAR) AS s FROM {cfg.T_PORTAL}matching_jobs "
                    f"WHERE id = '{job_id}'")[0]
    return {"seconds": seconds, "pass3": json.loads(meta["b"] or "{}").get("pass3") or {},
            "stages": json.loads(meta["s"] or "{}")}


for letter in letters:
    file_id = grade(letter)
    one = match(letter, file_id, "one", 0, 0)
    split = match(letter, file_id, "split", target, 0)
    rows = con.execute("SELECT (SELECT count(*) FROM one), (SELECT count(*) FROM split)").fetchone()
    only = [con.execute(f"SELECT count(*) FROM (SELECT * FROM {a} EXCEPT ALL SELECT * FROM {b})")
            .fetchone()[0] for a, b in (("one", "split"), ("split", "one"))]
    statuses = con.execute("SELECT status, count(*) FROM split GROUP BY 1 ORDER BY 1").fetchall()
    p3 = split["pass3"]
    print(f"grade {letter}: rows {rows[0]:,}/{rows[1]:,} | differing {only[0]}/{only[1]} | "
          f"parts {p3.get('parts')} pairs {p3.get('pairs')} | one {one['seconds']:.1f}s "
          f"split {split['seconds']:.1f}s (pass 3 {one['stages'].get('blockingMs')} vs "
          f"{split['stages'].get('blockingMs')} ms, count {split['stages'].get('pairCountMs')} ms) "
          f"| {statuses}", flush=True)
