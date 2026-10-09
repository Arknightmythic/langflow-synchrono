"""Move an existing cluster to the new database names (one-off, 2026-10-08).

  synchrono_master                     -> syncrono_master (renamed in place, nothing copied)
  synchrono_service.<table>            -> syncrono_starrocks.syncrono_service_<table>
  synchrono_kl.<table>                 -> syncrono_starrocks.syncrono_kl_<table>
  synchrono_portal.<table>             -> syncrono_starrocks.syncrono_portal_<table>
  (107 already used the syncrono_ spelling for the same four databases: handled the same way)

python tools/rename_databases.py                     show the plan; changes nothing
python tools/rename_databases.py --apply             rename the master, create the new tables,
                                                     copy the rows and check every old row arrived
python tools/rename_databases.py --apply --drop-old  also drop the three old databases, only when
                                                     every check passed (DROP without FORCE, so
                                                     RECOVER DATABASE works for about a day)

Rows are copied only into new tables that are still empty, so running it again never overwrites
what the service has written since. Matching work tables (w_*), tables that are not part of the
schema (shown in the plan) and the old UDFs are not copied; they go with the old database. No
other database is read or changed.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import settings as cfg  # noqa: E402
from engine import sr  # noqa: E402
from schema import apply as schema  # noqa: E402

NEW_DB, NEW_MASTER, OLD_MASTER = "syncrono_starrocks", "syncrono_master", "synchrono_master"
MOVES = {f"{spelling}_{group}": prefix for spelling in ("synchrono", "syncrono")
         for group, prefix in (("service", cfg.P_SERVICE), ("kl", cfg.P_KL), ("portal", cfg.P_PORTAL))}
# Databases of other applications that share these clusters (98: synchrono, 107: syncrono_analytics).
assert not {"synchrono", "syncrono_analytics", NEW_DB, NEW_MASTER} & {OLD_MASTER, *MOVES}


def schema_tables() -> set[str]:
    found = set()
    for name in sorted(f for f in os.listdir(schema.FOLDER) if f.endswith(".sql")):
        with open(os.path.join(schema.FOLDER, name), encoding="utf-8") as fh:
            for _, sql in schema.statements(schema.render(fh.read())):
                match = re.match(r"CREATE TABLE IF NOT EXISTS (\S+)", sql)
                if match:
                    found.add(match.group(1))
    return found


def databases() -> set[str]:
    return {r["Database"] for r in sr.query("SHOW DATABASES")}


def tables(db: str) -> list[str]:
    rows = sr.query(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{db}' "
                    f"AND table_type = 'BASE TABLE' ORDER BY table_name")
    return [r["table_name"] for r in rows if not r["table_name"].startswith("w_")]


def columns(db: str, table: str) -> dict[str, str]:
    rows = sr.query(f"SELECT column_name, data_type FROM information_schema.columns "
                    f"WHERE table_schema = '{db}' AND table_name = '{table}' ORDER BY ordinal_position")
    return {r["column_name"]: r["data_type"].lower() for r in rows}


def count(table: str) -> int:
    # A bare count(*) can be answered from tablet statistics, which lag right after a restart
    # (158, 2026-10-08: 963,034 instead of 2,236,545 rows). The LIMIT forces a real scan.
    return int(sr.scalar(f"SELECT count(*) FROM (SELECT 1 AS x FROM {table} LIMIT 9000000000000000000) t"))


def rows_not_in(a: str, b: str, exprs: str) -> int:
    return int(sr.scalar(f"SELECT count(*) FROM (SELECT {exprs} FROM {a} EXCEPT SELECT {exprs} FROM {b}) d"))


def plan(present: set[str]) -> None:
    if OLD_MASTER in present:
        print(f"rename database {OLD_MASTER} -> {NEW_MASTER}")
    known = schema_tables()
    for db, prefix in MOVES.items():
        if db in present:
            for t in tables(db):
                rows = f"{count(f'`{db}`.`{t}`'):,} rows"
                if f"{NEW_DB}.{prefix}{t}" in known:
                    print(f"copy {db}.{t} ({rows}) -> {NEW_DB}.{prefix}{t}")
                else:
                    print(f"skip {db}.{t} ({rows}): not part of the schema, goes with the old database")


def copy_and_check(db: str, prefix: str) -> bool:
    ok, known = True, schema_tables()
    for t in tables(db):
        old, new = f"`{db}`.`{t}`", f"`{NEW_DB}`.`{prefix}{t}`"
        old_cols, new_cols = columns(db, t), columns(NEW_DB, prefix + t)
        if f"{NEW_DB}.{prefix}{t}" not in known:
            print(f"  skip {db}.{t}: not part of the schema, goes with the old database")
            continue
        if not new_cols:
            print(f"  FAIL {db}.{t}: table {NEW_DB}.{prefix}{t} was not created")
            ok = False
            continue
        lost = [c for c in old_cols if c not in new_cols]
        if lost:
            print(f"  FAIL {db}.{t}: columns missing in the new table: {', '.join(lost)}")
            ok = False
            continue
        common = [c for c in new_cols if c in old_cols]
        names = ", ".join(f"`{c}`" for c in common)
        copied = not count(new)
        if copied:
            sr.execute(f"INSERT INTO {new} ({names}) SELECT {names} FROM {old}")
        else:
            print(f"  {prefix}{t}: already has rows, not copied")
        # JSON cannot take part in EXCEPT; compare its text instead.
        exprs = ", ".join(f"CAST(`{c}` AS STRING)" if old_cols[c] == "json" else f"`{c}`" for c in common)
        missing, extra = rows_not_in(old, new, exprs), rows_not_in(new, old, exprs)
        n_old, n_new = count(old), count(new)
        # A fresh copy must match both ways; a table the service already wrote to may hold more.
        good = missing == 0 and (extra == 0 or not copied) and (n_new == n_old or not copied)
        ok &= good
        print(f"  {'ok  ' if good else 'FAIL'} {db}.{t} {n_old:,} rows -> {prefix}{t} {n_new:,} rows"
              f"{'' if missing == 0 else f', {missing:,} old rows not found'}"
              f"{'' if extra == 0 else f', {extra:,} rows not in the old table'}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument("--drop-old", action="store_true", help="drop the old databases after the checks")
    args = parser.parse_args()
    if (cfg.DB, cfg.DB_MASTER) != (NEW_DB, NEW_MASTER):
        sys.exit(f"DB_STARROCK/DB_MASTER is {cfg.DB}/{cfg.DB_MASTER}, expected {NEW_DB}/{NEW_MASTER}: "
                 f"remove the old DB_SERVICE/DB_KL/DB_PORTAL/DB_MASTER lines from the env file")

    print(f"StarRocks {cfg.SR_HOST}:{cfg.SR_PORT}, {sr.scalar('SELECT current_version()')}")
    present = databases()
    if OLD_MASTER in present and NEW_MASTER in present:
        sys.exit(f"both {OLD_MASTER} and {NEW_MASTER} exist; decide by hand which one to keep")
    plan(present)
    if not args.apply:
        print("\nnothing changed (dry run); add --apply to make these changes")
        return

    if OLD_MASTER in present:
        sr.execute(f"ALTER DATABASE {OLD_MASTER} RENAME {NEW_MASTER}")
        print(f"renamed {OLD_MASTER} -> {NEW_MASTER}")
    schema.main(seed_rows=False)
    ok = True
    for db, prefix in MOVES.items():
        if db in present:
            print(f"{db}:")
            ok &= copy_and_check(db, prefix)
    schema.main()  # default rows only for tables that are still empty
    if not ok:
        sys.exit("some checks failed; the old databases were kept")
    if args.drop_old:
        for db in MOVES:
            if db in present:
                sr.execute(f"DROP DATABASE {db}")
                print(f"dropped {db} (RECOVER DATABASE {db} brings it back for about a day)")
    print("done")


if __name__ == "__main__":
    main()
