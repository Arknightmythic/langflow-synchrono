import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402

from engine import names  # noqa: E402
from engine import settings as cfg  # noqa: E402
from engine import sr  # noqa: E402
from engine.sql import now_wib_text, sq  # noqa: E402

FOLDER = os.path.dirname(os.path.abspath(__file__))
VALUES = {
    "DB": cfg.DB, "DB_MASTER": cfg.DB_MASTER, "SERVICE": cfg.T_SERVICE, "KL": cfg.T_KL,
    "PORTAL": cfg.T_PORTAL, "REPLICATION": str(cfg.SR_REPLICATION),
    "BUCKETS": str(cfg.SR_BUCKETS),
    "CREATED_DATE": sq(now_wib_text()), "CREATED_BY": sq(cfg.ENGINE_ACTOR),
}


def render(text: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: VALUES[m.group(1)], text)


def statements(text: str) -> list[tuple[str | None, str]]:
    result, seed = [], None
    for block in re.split(r";\s*\n", text):
        lines = [line for line in block.splitlines() if line.strip()]
        marker = next((line for line in lines if line.strip().startswith("-- seed:")), None)
        if marker:
            seed = marker.split(":", 1)[1].strip()
        body = "\n".join(line for line in lines if not line.strip().startswith("--")).strip()
        if body:
            result.append((seed, body))
            seed = None
    return result


ADDED_COLUMNS = {
    (cfg.DB, cfg.P_KL + "records"): [(c, "VARCHAR(512)") for c in
                                     names.variant_columns("name") + names.variant_columns("mother")],
    (cfg.DB_MASTER, "persons"): [(c, "VARCHAR(512)") for c in
                                 names.variant_columns("name") + names.variant_columns("mother")],
    # Filled by the portal when a reviewer decides (as on 107); the service only creates them.
    (cfg.DB, cfg.P_PORTAL + "matching_jobs"): [("reviewed_count", "BIGINT")],
    (cfg.DB, cfg.P_PORTAL + "matching_results"): [("reviewed_at", "DATETIME"),
                                                  ("reviewed_by", "VARCHAR(255)")],
}
# Audit columns on every permanent table: created_date in WIB without a zone, created_by the
# requester or the engine. The portal tables already had created_by.
AUDITED = [(cfg.DB, cfg.P_SERVICE + t) for t in (
    "grading_jobs", "grade_criteria", "grade_bands", "grade_rules", "matching_queries",
    "engine_config", "config_versions", "masters", "config_history", "reasoning_patterns",
    "service_api_keys")] + [(cfg.DB, cfg.P_KL + "records"), (cfg.DB, cfg.P_KL + "enriched"),
                            (cfg.DB_MASTER, "persons"), (cfg.DB_MASTER, "dictionary")]
for key in AUDITED:
    ADDED_COLUMNS.setdefault(key, []).extend([("created_date", "DATETIME"),
                                              ("created_by", "VARCHAR(255)")])
for key in [(cfg.DB, cfg.P_PORTAL + "matching_jobs"), (cfg.DB, cfg.P_PORTAL + "matching_results")]:
    ADDED_COLUMNS[key].append(("created_date", "DATETIME"))
# Rows from before the audit columns: created_date from the table's own time column moved to
# WIB, created_by from its own actor column. Duplicate-key tables (K/L, enriched, master) cannot
# be updated in StarRocks, so their older rows keep NULL. "utc": written by the service as UTC
# text; "cluster": written with StarRocks now(), in the cluster's time zone.
BACKFILL = [
    (cfg.T_SERVICE + "grading_jobs", "created_at", "utc", None),
    (cfg.T_SERVICE + "grade_criteria", "updated_at", "utc", "updated_by"),
    (cfg.T_SERVICE + "grade_rules", "updated_at", "utc", "updated_by"),
    (cfg.T_SERVICE + "engine_config", "updated_at", "utc", "updated_by"),
    (cfg.T_SERVICE + "masters", "updated_at", "utc", None),
    (cfg.T_SERVICE + "config_versions", "first_used", "utc", None),
    (cfg.T_SERVICE + "config_history", "changed_at", "utc", "changed_by"),
    (cfg.T_SERVICE + "reasoning_patterns", "created_at", "utc", None),
    (cfg.T_SERVICE + "service_api_keys", "created_at", "utc", None),
    (cfg.T_PORTAL + "matching_jobs", "created_at", "utc", None),
    (cfg.T_PORTAL + "matching_results", "created_at", "cluster", None),
]


def add_columns() -> None:
    for (db, table), wanted in ADDED_COLUMNS.items():
        present = {r["COLUMN_NAME"] for r in sr.query(
            f"SELECT COLUMN_NAME FROM information_schema.columns "
            f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{table}'")}
        missing = [(c, kind) for c, kind in wanted if c not in present]
        if not missing:
            continue
        sr.execute(f"ALTER TABLE {db}.{table} ADD COLUMN "
                   f"({', '.join(f'{c} {kind}' for c, kind in missing)})")
        for _ in range(600):
            jobs = sr.query(f"SHOW ALTER TABLE COLUMN FROM {db} WHERE TableName = '{table}' "
                            f"ORDER BY CreateTime DESC LIMIT 1")
            if not jobs or jobs[0].get("State") in ("FINISHED", "CANCELLED"):
                break
            time.sleep(1)
        print(f"[schema] {db}.{table}: added {', '.join(c for c, _ in missing)}")


def ensure_rows() -> None:
    if not sr.scalar(f"SELECT count(*) FROM {cfg.T_SERVICE}grade_rules WHERE grade_code = 6"):
        sr.execute(f"INSERT INTO {cfg.T_SERVICE}grade_rules (grade_code, auto_missing_max, "
                   f"auto_score_min, review_missing_count, review_score_min, review_score_max, "
                   f"updated_by, created_date, created_by) VALUES (6, 1, 90.0, NULL, 70.0, 90.0, "
                   f"NULL, {VALUES['CREATED_DATE']}, {VALUES['CREATED_BY']})")
        print("[schema] grade_rules: grade 6 added")
    for table in ("grade_criteria", "grade_rules"):
        sr.execute(f"UPDATE {cfg.T_SERVICE}{table} SET updated_by = NULL "
                   f"WHERE updated_by = 'seed'")


def backfill_audit() -> None:
    zone = sr.scalar("SELECT @@time_zone")
    for table, column, written, actor in BACKFILL:
        source = "'+00:00'" if written == "utc" else sq(zone)
        sr.execute(f"UPDATE {table} SET created_date = convert_tz({column}, {source}, '+07:00') "
                   f"WHERE created_date IS NULL AND {column} IS NOT NULL")
        if actor:
            sr.execute(f"UPDATE {table} SET created_by = {actor} "
                       f"WHERE created_by IS NULL AND {actor} IS NOT NULL")


def main(seed_rows: bool = True) -> None:
    """seed_rows=False creates databases, tables and columns only (tools/rename_databases.py)."""
    for name in sorted(f for f in os.listdir(FOLDER) if f.endswith(".sql")):
        with open(os.path.join(FOLDER, name), encoding="utf-8") as fh:
            text = render(fh.read())
        for seed, sql in statements(text):
            if seed:
                if not seed_rows:
                    continue
                count = sr.scalar(f"SELECT count(*) FROM {cfg.T_SERVICE}{seed}")
                if count:
                    print(f"[schema] {name}: {seed} already seeded ({count} rows)")
                    continue
            sr.execute(sql)
        print(f"[schema] {name} applied")
    add_columns()
    if seed_rows:
        ensure_rows()
        backfill_audit()


if __name__ == "__main__":
    main()
