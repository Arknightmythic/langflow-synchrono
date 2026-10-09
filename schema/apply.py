import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402

from engine import names  # noqa: E402
from engine import settings as cfg  # noqa: E402
from engine import sr  # noqa: E402
from engine.sql import now_text, sq  # noqa: E402

FOLDER = os.path.dirname(os.path.abspath(__file__))
VALUES = {
    "DB": cfg.DB, "DB_MASTER": cfg.DB_MASTER, "SERVICE": cfg.T_SERVICE, "KL": cfg.T_KL,
    "PORTAL": cfg.T_PORTAL, "REPLICATION": str(cfg.SR_REPLICATION),
    "BUCKETS": str(cfg.SR_BUCKETS),
    "CREATED_AT": sq(now_text()), "CREATED_BY": sq(cfg.ENGINE_ACTOR),
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
# Audit columns on every permanent table: created_at (WIB, like every DATETIME column) and
# created_by, the requester or the engine. add_columns() only adds what a table lacks.
AUDITED = [(cfg.DB, cfg.P_SERVICE + t) for t in (
    "grading_jobs", "grade_criteria", "grade_bands", "grade_rules", "matching_queries",
    "engine_config", "config_versions", "masters", "config_history", "reasoning_patterns",
    "service_api_keys")] + [(cfg.DB, cfg.P_KL + "records"), (cfg.DB, cfg.P_KL + "enriched"),
                            (cfg.DB, cfg.P_PORTAL + "matching_jobs"),
                            (cfg.DB, cfg.P_PORTAL + "matching_results"),
                            (cfg.DB_MASTER, "persons"), (cfg.DB_MASTER, "dictionary")]
for key in AUDITED:
    ADDED_COLUMNS.setdefault(key, []).extend([("created_at", "DATETIME"),
                                              ("created_by", "VARCHAR(255)")])
# Rows without created_at take it from the table's own time column, and created_by from its own
# actor column. Duplicate-key tables (K/L, enriched, master) cannot be updated in StarRocks.
BACKFILL = [
    (cfg.T_SERVICE + "grade_criteria", "updated_at", "updated_by"),
    (cfg.T_SERVICE + "grade_rules", "updated_at", "updated_by"),
    (cfg.T_SERVICE + "engine_config", "updated_at", "updated_by"),
    (cfg.T_SERVICE + "masters", "updated_at", None),
    (cfg.T_SERVICE + "config_versions", "first_used", None),
    (cfg.T_SERVICE + "config_history", "changed_at", "changed_by"),
]
# Until 2026-10-09 stored times were UTC and the audit column was called created_date (WIB).
# Since then every DATETIME column holds WIB and the audit column is created_at. A table that still
# has created_date is converted once by to_wib(): its time columns move from UTC to WIB, then
# created_date is renamed to created_at, or dropped where created_at already existed.
# "utc": written by the service or the portal in UTC; "cluster": written with StarRocks now().
WIB_SHIFT = {
    (cfg.DB, cfg.P_SERVICE + "grading_jobs"): {
        "created_at": "utc", "started_at": "utc", "finished_at": "utc", "heartbeat_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "grade_criteria"): {"updated_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "grade_bands"): {},
    (cfg.DB, cfg.P_SERVICE + "grade_rules"): {"updated_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "matching_queries"): {},
    (cfg.DB, cfg.P_SERVICE + "engine_config"): {"updated_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "config_versions"): {"first_used": "utc"},
    (cfg.DB, cfg.P_SERVICE + "masters"): {"updated_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "config_history"): {"changed_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "reasoning_patterns"): {"created_at": "utc", "updated_at": "utc"},
    (cfg.DB, cfg.P_SERVICE + "service_api_keys"): {
        "created_at": "utc", "last_used_at": "utc", "expires_at": "utc"},
    (cfg.DB, cfg.P_PORTAL + "matching_jobs"): {
        "created_at": "utc", "updated_at": "utc", "started_at": "utc", "completed_at": "utc",
        "failed_at": "utc"},
    (cfg.DB, cfg.P_PORTAL + "matching_results"): {
        "created_at": "cluster", "updated_at": "cluster", "reviewed_at": "review"},
    (cfg.DB, cfg.P_KL + "records"): {},
    (cfg.DB, cfg.P_KL + "enriched"): {},
    (cfg.DB_MASTER, "persons"): {},
    (cfg.DB_MASTER, "dictionary"): {},
}
MARK = "tz_wib"  # set on the rows already moved, so an interrupted run never moves a row twice


def columns_of(db: str, table: str) -> set[str]:
    return {r["COLUMN_NAME"] for r in sr.query(
        f"SELECT COLUMN_NAME FROM information_schema.columns "
        f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_NAME = '{table}'")}


def wait_for_alter(db: str, table: str) -> None:
    for _ in range(600):
        jobs = sr.query(f"SHOW ALTER TABLE COLUMN FROM {db} WHERE TableName = '{table}' "
                        f"ORDER BY CreateTime DESC LIMIT 1")
        if not jobs or jobs[0].get("State") in ("FINISHED", "CANCELLED"):
            return
        time.sleep(1)


def alter(db: str, table: str, change: str) -> None:
    sr.execute(f"ALTER TABLE {db}.{table} {change}")
    wait_for_alter(db, table)


def to_wib() -> None:
    zone = None
    for (db, table), shift in WIB_SHIFT.items():
        present = columns_of(db, table)
        if not present or ("created_date" not in present and MARK not in present):
            continue
        if shift:
            if zone is None:
                zone = sr.scalar("SELECT @@time_zone")
            if MARK not in present:
                alter(db, table, f"ADD COLUMN {MARK} BOOLEAN")

            def moved(column: str, source: str) -> str:
                origin = sq(zone) if source == "cluster" else "'+00:00'"
                if source == "review":
                    # The portal wrote reviewed_at in UTC at first, later in WIB (7 hours after
                    # updated_at): move only the values that agree with updated_at.
                    return (f"CASE WHEN {column} IS NOT NULL AND abs(timestampdiff(SECOND, "
                            f"updated_at, {column})) < 3600 THEN convert_tz({column}, "
                            f"'+00:00', '+07:00') ELSE {column} END")
                return f"convert_tz({column}, {origin}, '+07:00')"

            sets = [f"{c} = {moved(c, s)}" for c, s in shift.items() if c in present]
            sr.execute(f"UPDATE {db}.{table} SET {', '.join(sets + [f'{MARK} = TRUE'])} "
                       f"WHERE {MARK} IS NULL")
        if "created_date" in present:
            if "created_at" in present:
                alter(db, table, "DROP COLUMN created_date")
            else:
                alter(db, table, "RENAME COLUMN created_date TO created_at")
        if MARK in columns_of(db, table):
            alter(db, table, f"DROP COLUMN {MARK}")
        print(f"[schema] {db}.{table}: times now WIB, audit column created_at")


def add_columns() -> None:
    for (db, table), wanted in ADDED_COLUMNS.items():
        present = columns_of(db, table)
        missing = [(c, kind) for c, kind in wanted if c not in present]
        if not missing:
            continue
        alter(db, table, f"ADD COLUMN ({', '.join(f'{c} {kind}' for c, kind in missing)})")
        print(f"[schema] {db}.{table}: added {', '.join(c for c, _ in missing)}")


def ensure_rows() -> None:
    if not sr.scalar(f"SELECT count(*) FROM {cfg.T_SERVICE}grade_rules WHERE grade_code = 6"):
        sr.execute(f"INSERT INTO {cfg.T_SERVICE}grade_rules (grade_code, auto_missing_max, "
                   f"auto_score_min, review_missing_count, review_score_min, review_score_max, "
                   f"updated_by, created_at, created_by) VALUES (6, 1, 90.0, NULL, 70.0, 90.0, "
                   f"NULL, {VALUES['CREATED_AT']}, {VALUES['CREATED_BY']})")
        print("[schema] grade_rules: grade 6 added")
    for table in ("grade_criteria", "grade_rules"):
        sr.execute(f"UPDATE {cfg.T_SERVICE}{table} SET updated_by = NULL "
                   f"WHERE updated_by = 'seed'")


def backfill_audit() -> None:
    for table, column, actor in BACKFILL:
        sr.execute(f"UPDATE {table} SET created_at = {column} "
                   f"WHERE created_at IS NULL AND {column} IS NOT NULL")
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
    to_wib()
    add_columns()
    if seed_rows:
        ensure_rows()
        backfill_audit()


if __name__ == "__main__":
    main()
