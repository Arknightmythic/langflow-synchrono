"""Pass 3 experiments in StarRocks: python bench/p3_lab.py MODE [C D E ...]

Every mode first prepares Pass 1 and 2 like a real matching job on the files graded by
parity_parts.py (pp-um-<grade>), then:
  pairs    counts, for the first Pass 3 part, the Jaro-Winkler calls per scored column and the
           distinct (incoming, master) value pairs behind them
  udf      runs the whole Pass 3 with the Jaro-Winkler UDF and with a cheap built-in function in
           its place (meaningless results) to show what the UDF costs
  profile  runs the first part once with the StarRocks profile on; saved to /work/profile-<grade>.txt
Writes only its own work tables, dropped at the end.
"""
import sys
import time

sys.path.insert(0, "/srv")

from engine import duck, grading, matching, names, rules, sr, udf  # noqa: E402

GRADES = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
MODES = ("pairs", "udf", "profile")
if len(sys.argv) < 2 or sys.argv[1] not in MODES:
    raise SystemExit(__doc__)
mode, letters = sys.argv[1], sys.argv[2:] or ["C", "D", "E"]
stamp = int(time.time())


def scored_columns(rules_: dict) -> dict[str, tuple[str, str]]:
    out = {}
    for field, share in rules_["weights"]:
        if not share:
            continue
        if field == "wilayah":
            for region in matching.REGIONS:
                out[region] = (f"{region}_clean", f"{region}_master_clean")
        elif field == "tanggal_lahir":
            if rules_["date_match"] != "exact":
                out[field] = ("CAST(tanggal_lahir_clean AS VARCHAR)",
                              "CAST(tanggal_lahir_master_clean AS VARCHAR)")
        else:
            right = "nama_master_clean" if field == "nama" else f"{field}_master_clean"
            out[field] = (f"{field}_clean", right)
    return out


def part_sql(ctx: dict, index: int, parts: int) -> str:
    return matching.blocking_sr_sql(ctx["w"], ctx["rules"]["blocking"], ctx["file_id"],
                                    ctx["master_id"], ctx["nv"], ctx["mv"],
                                    (index, parts) if parts > 1 else None)


def count_pairs(ctx: dict) -> None:
    parts, pairs = ctx["parts"]
    cols = scored_columns(ctx["rules"])
    values = ", ".join(f"{a} AS a_{n}, {b} AS b_{n}" for n, (a, b) in cols.items())
    stats = []
    for n in cols:
        both = f"nullif(a_{n}, '') IS NOT NULL AND nullif(b_{n}, '') IS NOT NULL"
        stats.append(f"sum(CASE WHEN {both} THEN 1 ELSE 0 END) AS calls_{n}")
        stats.append(f"count(DISTINCT CASE WHEN {both} THEN concat_ws('|#|', a_{n}, b_{n}) END)"
                     f" AS uniq_{n}")
    t = time.perf_counter()
    row = sr.query(f"WITH joined AS ({part_sql(ctx, 0, parts)}), v AS (SELECT {values} FROM joined) "
                   f"SELECT count(*) AS part_pairs, {', '.join(stats)} FROM v")[0]
    calls = sum(int(row[f"calls_{n}"] or 0) for n in cols)
    uniq = sum(int(row[f"uniq_{n}"] or 0) for n in cols)
    print(f"grade {ctx['letter']}: {parts} part(s) of {pairs} pairs; part 1 has "
          f"{int(row['part_pairs']):,} pairs ({time.perf_counter() - t:.0f}s to measure)", flush=True)
    for n in cols:
        c, u = int(row[f"calls_{n}"] or 0), int(row[f"uniq_{n}"] or 0)
        print(f"    {n:14s} JW calls {c:>13,}   unique pairs {u:>11,}   x{c / u if u else 0:,.0f}")
    print(f"    {'total':14s} JW calls {calls:>13,}   unique pairs {uniq:>11,}   "
          f"x{calls / uniq if uniq else 0:,.0f}", flush=True)


def time_udf(ctx: dict) -> None:
    runs = {}
    for label, functions in (("udf", ctx["fn"]), ("built-in", {"jw": "instr", "round": "round"})):
        timings = {}
        detail = matching.pass3_starrocks(ctx["w"], functions, ctx["rules"], ctx["file_id"],
                                          ctx["master_id"], ctx["nv"], ctx["mv"], timings)
        runs[label] = (timings["blockingMs"], detail.get("parts"))
    saved = runs["udf"][0] - runs["built-in"][0]
    print(f"grade {ctx['letter']}: Pass 3 with UDF {runs['udf'][0] / 1000:.1f}s, with built-in "
          f"{runs['built-in'][0] / 1000:.1f}s ({runs['udf'][1]} part(s)); the UDF costs about "
          f"{saved / 1000:.1f}s = {saved / runs['udf'][0]:.0%} of Pass 3", flush=True)


def profile(ctx: dict) -> None:
    parts, pairs = ctx["parts"]
    for statement in ("SET enable_profile = true", "SET enable_async_profile = false"):
        try:
            sr.execute(statement)
        except Exception as e:  # noqa: BLE001
            print(f"    {statement}: {e}")
    t = time.perf_counter()
    rows = sr.query(matching.pass3_sr_query(ctx["fn"], ctx["rules"], part_sql(ctx, 0, parts)))
    seconds = time.perf_counter() - t
    query_id = sr.scalar("SELECT last_query_id()")
    sr.execute("SET enable_profile = false")
    text = None
    for statement in (f"ANALYZE PROFILE FROM '{query_id}'", f"SELECT get_query_profile('{query_id}')"):
        try:
            text = "\n".join(str(next(iter(r.values()))) for r in sr.query(statement))
            break
        except Exception as e:  # noqa: BLE001
            text = f"{statement}: {e}"
    with open(f"/work/profile-{ctx['letter']}.txt", "w", encoding="utf-8") as fh:
        fh.write(text or "")
    print(f"grade {ctx['letter']}: part 1 of {parts} ({pairs} pairs): {len(rows):,} rows in "
          f"{seconds:.1f}s, query {query_id}; profile in bench/.data/work-new/profile-"
          f"{ctx['letter']}.txt", flush=True)


for letter in letters:
    file_id = f"pp-um-{letter}"
    job = matching.build_job({
        "jobId": f"lab-{letter}-{stamp}", "fileId": file_id, "masterFileId": "um-master",
        "actor": "bench", "callbackUrl": "http://srb-cb:8000/cb/none", "s3Bucket": "bench",
        "incomingFile": {"s3Key": f"{file_id}/new/enriched.parquet"},
        "masterDataFile": {"s3Key": "master/um-master.parquet"}, "grade": GRADES[letter]})
    w = matching.Work(job["job_id"])
    con = duck.connect(s3=True)
    grading.apply_s3_endpoint(con, job)
    sr.attach(con)
    try:
        rules_ = rules.matching_rules(GRADES[letter])
        variant = names.variant_for(rules_["name_cleaning"])
        nv, mv = f"name_{variant}", f"mother_{variant}"
        master_id = matching.ensure_master(job, print, variant)
        matching.ensure_incoming(job, con, variant)
        w.drop()
        matching._create(w.t("dec"), matching.DECISION_DDL)
        matching._create(w.t("cand"), matching.CANDIDATE_DDL)
        matching.pass1(w, con, file_id, master_id, nv, mv, rules_["contradiction_jw"])
        matching.pass2(w, con, file_id, master_id, nv, mv)
        matching._load_decisions(w, con, "p2_decisions", "p2_candidates")
        fn = udf.ensure()
        if not fn and mode != "pairs":
            raise RuntimeError("Jaro-Winkler UDF is not available in StarRocks")
        ctx = {"letter": letter, "w": w, "rules": rules_, "file_id": file_id,
               "master_id": master_id, "nv": nv, "mv": mv, "fn": fn,
               "parts": matching._pass3_parts(w, rules_["blocking"], file_id, master_id, nv, mv, {})}
        {"pairs": count_pairs, "udf": time_udf, "profile": profile}[mode](ctx)
    finally:
        w.drop()
        con.close()
