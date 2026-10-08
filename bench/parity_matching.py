"""Matching parity: DuckDB service (srb-old) vs engine.matching, row by row."""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/srv")

from engine import duck, matching, sr  # noqa: E402
from engine import settings as cfg  # noqa: E402

GRADES = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
LETTERS = sys.argv[1:] or list(GRADES)
HARNESS = "http://srb-cb:8000"
OLD = "http://srb-old:8000/api/v1/run/matching-dispatch"


def post(url, body, headers=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.loads(response.read())


def wait_callback(job_id, limit=1800):
    started = time.time()
    while time.time() - started < limit:
        try:
            with urllib.request.urlopen(f"{HARNESS}/cb/{job_id}", timeout=10) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            time.sleep(2)
    raise TimeoutError(job_id)


def payload(job_id, letter, callback):
    return {"jobId": job_id, "fileId": f"p-um-{letter}", "masterFileId": "um-master",
            "actor": "bench", "callbackUrl": callback, "s3Bucket": "bench",
            "incomingFile": {"s3Key": f"p-um-{letter}/old/enriched.parquet"},
            "masterDataFile": {"s3Key": "master/um-master.parquet"}, "grade": GRADES[letter]}


con = duck.connect(s3=False)
con.execute("INSTALL postgres")
con.execute("LOAD postgres")
con.execute("ATTACH 'host=srb-pg port=5432 dbname=portal_sim user=postgres password=bench' "
            "AS portal (TYPE postgres, READ_ONLY)")
sr.attach(con)
stamp = int(time.time())
for letter in LETTERS:
    old_id, new_id = f"par-old-{letter}-{stamp}", f"par-new-{letter}-{stamp}"
    post(f"{HARNESS}/prepare", {"jobId": old_id, "fileId": f"p-um-{letter}",
                                "masterFileId": "um-master"})
    t = time.perf_counter()
    post(OLD, {"tweaks": {"MatchingDispatch-b4819": {"payload": json.dumps(
        payload(old_id, letter, f"{HARNESS}/cb/old"))}}}, {"x-api-key": "bench-key"})
    old_cb = wait_callback(old_id)
    old_s = time.perf_counter() - t
    t = time.perf_counter()
    job = matching.build_job(payload(new_id, letter, f"{HARNESS}/cb/new"))
    matching.register(job)
    matching.run(job)
    new_s = time.perf_counter() - t
    con.execute(f"""CREATE OR REPLACE TABLE a AS SELECT id_incoming, master_nik, score, status,
        method, rank_conflict, pattern_group, reasoning,
        CAST(incoming_snapshot AS VARCHAR) AS i_snap, CAST(master_snapshot AS VARCHAR) AS m_snap
        FROM portal.syncrono_matching_result WHERE job_id = '{old_id}'""")
    sr.pull(con, "b", f"SELECT id_incoming, master_nik, score, status, method, rank_conflict, "
                      f"pattern_group, reasoning, CAST(incoming_snapshot AS VARCHAR) AS i_snap, "
                      f"CAST(master_snapshot AS VARCHAR) AS m_snap "
                      f"FROM {cfg.T_PORTAL}matching_results WHERE job_id = '{new_id}'")
    counts = con.execute("SELECT (SELECT count(*) FROM a), (SELECT count(*) FROM b)").fetchone()
    diff = {}
    for column in ("status", "master_nik", "score", "method", "rank_conflict", "pattern_group",
                   "reasoning", "i_snap", "m_snap"):
        if column in ("i_snap", "m_snap"):
            keys = (["nama", "nik", "tanggal_lahir", "jenis_kelamin", "nama_ibu", "tempat_lahir",
                     "provinsi"] if column == "i_snap" else
                    ["nama_lengkap", "nik", "tanggal_lahir", "jenis_kelamin", "nama_ibu",
                     "tempat_lahir", "provinsi"])
            expr_a = "concat_ws('|', " + ", ".join(
                f"COALESCE(json_extract_string(a.{column}, '$.{k}'), '<null>')" for k in keys) + ")"
            expr_b = "concat_ws('|', " + ", ".join(
                f"COALESCE(json_extract_string(b.{column}, '$.{k}'), '<null>')" for k in keys) + ")"
        elif column == "rank_conflict":
            expr_a, expr_b = f"CAST(a.{column} AS BOOLEAN)", f"CAST(b.{column} AS BOOLEAN)"
        else:
            expr_a, expr_b = f"a.{column}", f"b.{column}"
        diff[column] = con.execute(
            f"SELECT count(*) FROM a JOIN b USING (id_incoming) "
            f"WHERE {expr_a} IS DISTINCT FROM {expr_b}").fetchone()[0]
    statuses = con.execute("SELECT status, count(*) FROM b GROUP BY 1 ORDER BY 1").fetchall()
    print(f"grade {letter}: rows old {counts[0]:,} new {counts[1]:,} | differing {diff} | "
          f"old {old_s:.1f}s new {new_s:.1f}s | {statuses}", flush=True)
    for column in ("status", "master_nik", "pattern_group", "reasoning", "m_snap"):
        if diff[column]:
            for row in con.execute(
                    f"SELECT a.id_incoming, a.{column}, b.{column} FROM a JOIN b USING "
                    f"(id_incoming) WHERE a.{column} IS DISTINCT FROM b.{column} LIMIT 2").fetchall():
                print(f"    {column}: {row}")
