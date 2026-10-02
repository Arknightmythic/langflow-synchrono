"""Collect k6 RESULT lines, k6 summaries and sampler CSVs into bench/results/summary.json."""
import csv
import glob
import json
import os
import re
import statistics
import sys

RESULTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "results")
FILES = ["A", "B", "C", "D", "E"]


def latest(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(RESULTS, pattern)))
    return files[-1] if files else None


def records(target: str) -> list[dict]:
    rows = []
    path = latest(f"k6-{target}-*.log")
    if not path:
        return rows
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            match = re.search(r'RESULT (\{.*\})"? source=', line) or re.search(
                r"RESULT (\{.*\})\s*$", line)
            if not match:
                continue
            text = match.group(1).replace('\\"', '"') if '\\"' in match.group(1) else match.group(1)
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError:
                pass
    return rows


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def aggregate(rows: list[dict]) -> dict:
    out = {}
    for f in FILES:
        grading = [r for r in rows if r.get("mode") == "seq" and r.get("file") == f
                   and r.get("step") == "grading"]
        matching = [r for r in rows if r.get("mode") == "seq" and r.get("file") == f
                    and r.get("step") == "matching"]
        stage_keys = sorted({k for r in matching for k in (r.get("stages") or {})})
        out[f] = {
            "rounds": len(matching),
            "gradingE2eMs": median([r.get("e2eMs") for r in grading]),
            "gradingEngineMs": median([r.get("engineMs") for r in grading]),
            "klLoadMs": median([r.get("klLoadMs") for r in grading]),
            "matchingE2eMs": median([r.get("e2eMs") for r in matching]),
            "matchingAll": [r.get("e2eMs") for r in matching],
            "gradingAll": [r.get("e2eMs") for r in grading],
            "stages": {k: median([(r.get("stages") or {}).get(k) for r in matching])
                       for k in stage_keys},
            "statuses": {k: (matching[-1] if matching else {}).get(k)
                         for k in ("auto", "review", "unmatch", "conflict")},
            "failures": [r.get("error") for r in grading + matching
                         if r.get("status") not in ("COMPLETED",)],
        }
    concurrent = [r for r in rows if r.get("mode") == "concurrent"]
    return {"files": out, "concurrent": concurrent}


def noise(target: str) -> dict | None:
    path = latest(f"k6-{target}-*.json")
    if not path:
        return None
    data = json.load(open(path, encoding="utf-8"))
    metric = data.get("metrics", {}).get("http_req_duration{scenario:noise}")
    if not metric:
        return None
    values = metric.get("values", {})
    return {k: round(v, 1) for k, v in values.items() if isinstance(v, (int, float))}


def resources(target: str) -> dict:
    path = latest(f"sampler-{target}-*.csv")
    series: dict[str, list] = {}
    if path:
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                series.setdefault(row["source"], []).append(
                    (float(row["ts"]), float(row["cpu_pct"]), float(row["mem_mb_or_pct"])))
    summary = {}
    for source, points in series.items():
        cpu = [p[1] for p in points]
        mem = [p[2] for p in points]
        summary[source] = {"samples": len(points), "cpuAvg": round(statistics.mean(cpu), 1),
                           "cpuMax": round(max(cpu), 1), "memAvg": round(statistics.mean(mem), 1),
                           "memMax": round(max(mem), 1)}
    return summary


def main() -> None:
    out = {}
    for target in ("old", "new"):
        rows = records(target)
        out[target] = {**aggregate(rows), "noise": noise(target), "resources": resources(target),
                       "raw": rows}
    path = os.path.join(RESULTS, "summary.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    for target in ("old", "new"):
        for f, a in out[target]["files"].items():
            print(target, f, "grading", a["gradingE2eMs"], "kl", a["klLoadMs"], "matching",
                  a["matchingE2eMs"], a["matchingAll"], a["statuses"], a["failures"] or "")
            print("     stages", a["stages"])
        for c in out[target]["concurrent"]:
            if c.get("step") == "makespan":
                print(target, "concurrent", c)
        print(target, "noise", out[target]["noise"])
        print(target, "resources", {k: {x: v[x] for x in ("cpuAvg", "cpuMax", "memAvg", "memMax")}
                                     for k, v in out[target]["resources"].items()})
    print("written", path)


if __name__ == "__main__":
    main()
