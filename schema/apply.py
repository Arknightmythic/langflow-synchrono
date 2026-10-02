import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time  # noqa: E402

from engine import names  # noqa: E402
from engine import settings as cfg  # noqa: E402
from engine import sr  # noqa: E402

FOLDER = os.path.dirname(os.path.abspath(__file__))
VALUES = {
    "DB_SERVICE": cfg.DB_SERVICE, "DB_KL": cfg.DB_KL, "DB_PORTAL": cfg.DB_PORTAL,
    "DB_MASTER": cfg.DB_MASTER, "REPLICATION": str(cfg.SR_REPLICATION),
    "BUCKETS": str(cfg.SR_BUCKETS),
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
    (cfg.DB_KL, "records"): [(c, "VARCHAR(512)") for c in
                             names.variant_columns("name") + names.variant_columns("mother")],
    (cfg.DB_MASTER, "persons"): [(c, "VARCHAR(512)") for c in
                                 names.variant_columns("name") + names.variant_columns("mother")],
}


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
    if not sr.scalar(f"SELECT count(*) FROM {cfg.DB_SERVICE}.grade_rules WHERE grade_code = 6"):
        sr.execute(f"INSERT INTO {cfg.DB_SERVICE}.grade_rules (grade_code, auto_missing_max, "
                   f"auto_score_min, review_missing_count, review_score_min, review_score_max, "
                   f"updated_by) VALUES (6, 1, 90.0, NULL, 70.0, 90.0, NULL)")
        print("[schema] grade_rules: grade 6 added")
    for table in ("grade_criteria", "grade_rules"):
        sr.execute(f"UPDATE {cfg.DB_SERVICE}.{table} SET updated_by = NULL "
                   f"WHERE updated_by = 'seed'")


def main() -> None:
    for name in sorted(f for f in os.listdir(FOLDER) if f.endswith(".sql")):
        with open(os.path.join(FOLDER, name), encoding="utf-8") as fh:
            text = render(fh.read())
        for seed, sql in statements(text):
            if seed:
                count = sr.scalar(f"SELECT count(*) FROM {cfg.DB_SERVICE}.{seed}")
                if count:
                    print(f"[schema] {name}: {seed} already seeded ({count} rows)")
                    continue
            sr.execute(sql)
        print(f"[schema] {name} applied")
    add_columns()
    ensure_rows()


if __name__ == "__main__":
    main()
