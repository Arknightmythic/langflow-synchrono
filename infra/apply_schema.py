"""
Terapkan skema + seed konfigurasi ke PostgreSQL.

Hanya butuh `duckdb` — DDL dijalankan lewat postgres_execute() dari extension
postgres_scanner, jadi tidak perlu psycopg maupun sqlalchemy.

    python apply_schema.py            # terapkan DDL + seed
    python apply_schema.py --verify   # hanya laporkan kondisi sekarang
"""

import argparse
import json
import os
import re
from pathlib import Path

import duckdb

PG_DSN = os.getenv(
    "PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres"
)
DISINI = Path(__file__).parent

# ── Seed tabel referensi & aturan (nilainya diambil dari sistem berjalan) ────

SEED = {
    "ref_grades": (
        ["grade_id", "grade_code"],
        [(1, "A"), (2, "B"), (3, "C"), (4, "D"), (5, "E"), (6, "F")],
    ),
    "ref_process": (
        ["process_id", "process_name"],
        [(1, "UPLOADED"), (2, "GRADED")],
    ),
    "ref_sync_statuses": (
        ["sync_status_id", "status_code"],
        [(1, "In Progress"), (2, "Awaiting Action"), (3, "Completed")],
    ),
    "ref_match_results": (
        ["match_result_id", "match_result_name"],
        [(1, "AUTO_MATCH"), (2, "MANUAL_REVIEW"), (3, "AUTO_UNMATCH"),
         (4, "MANUAL_MATCH"), (5, "MANUAL_UNMATCH")],
    ),
    "grade_rules": (
        ["grade_code", "auto_missing_max", "auto_score_min",
         "review_missing_count", "review_score_min", "review_score_max"],
        [
            (1, 99, 80.001, 99, 0.0, 80.001),
            (2, 1, 85.0, 2, 80.0, 85.0),
            (3, 99, 87.0, 99, 85.0, 87.0),
            (4, 1, 90.0, 2, 85.0, 90.0),
            (5, 1, 81.0, 2, 80.0, 81.0),
            (6, 1, 90.0, None, 70.0, 90.0),
        ],
    ),
}

PK = {
    "ref_grades": "grade_id",
    "ref_process": "process_id",
    "ref_sync_statuses": "sync_status_id",
    "ref_match_results": "match_result_id",
    "grade_rules": "grade_code",
}


def lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def pecah_statement(sql: str) -> list[str]:
    """
    Pisah file SQL jadi statement, abaikan komentar dan baris kosong.

    Titik koma DI DALAM string literal tidak dianggap pemisah. Memecah dengan
    `sql.split(";")` tampak cukup sampai ada satu deskripsi yang memuat titik
    koma — statement-nya lalu terbelah diam-diam di tengah kalimat, dan yang
    terkirim ke PostgreSQL adalah potongan tak bermakna. Itu sempat terjadi
    pada teks criteria_description grade D.
    """
    tanpa_komentar = "\n".join(
        b for b in sql.splitlines() if not b.strip().startswith("--")
    )

    hasil, sedang, dalam_kutip = [], [], False
    i = 0
    while i < len(tanpa_komentar):
        huruf = tanpa_komentar[i]
        if huruf == "'":
            # '' di dalam string adalah kutip ter-escape, bukan penutup.
            if dalam_kutip and tanpa_komentar[i + 1:i + 2] == "'":
                sedang.append("''")
                i += 2
                continue
            dalam_kutip = not dalam_kutip
            sedang.append(huruf)
        elif huruf == ";" and not dalam_kutip:
            hasil.append("".join(sedang))
            sedang = []
        else:
            sedang.append(huruf)
        i += 1
    hasil.append("".join(sedang))

    return [s.strip() for s in hasil if s.strip()]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true",
                   help="hanya laporkan kondisi, tidak mengubah apa pun")
    p.add_argument("--config", default=None,
                   help="JSON berisi matching_queries hasil ekspor dari StarRocks")
    args = p.parse_args()

    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE postgres)")

    def jalankan(sql: str):
        con.execute("CALL postgres_execute('pg', ?)", [sql])

    def tabel_ada() -> list[str]:
        return [
            r[0] for r in con.execute(
                "SELECT table_name FROM pg.information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            ).fetchall()
        ]

    if args.verify:
        lapor(con, tabel_ada())
        return 0

    # ── DDL ──────────────────────────────────────────────────────────────
    # Urutan penting: schema_grading.sql memuat foreign key ke ref_grades,
    # jadi schema.sql harus lebih dulu.
    for berkas in ("schema.sql", "schema_grading.sql", "schema_config.sql"):
        print(f"== menerapkan DDL — {berkas} ==")
        for stmt in pecah_statement((DISINI / berkas).read_text(encoding="utf-8")):
            jalankan(stmt)
            nama = re.search(r"(TABLE|INDEX)\s+(IF NOT EXISTS\s+)?(\S+)", stmt, re.I)
            print(f"   ok  {nama.group(3) if nama else stmt[:40]}")
        print()

    # ── Seed referensi & aturan ──────────────────────────────────────────
    print("\n== seed tabel referensi & grade_rules ==")
    for tabel, (kolom, baris) in SEED.items():
        for b in baris:
            nilai = ", ".join(lit(v) for v in b)
            update = ", ".join(
                f"{k}=EXCLUDED.{k}" for k in kolom if k != PK[tabel]
            )
            jalankan(
                f"INSERT INTO {tabel} ({', '.join(kolom)}) VALUES ({nilai}) "
                f"ON CONFLICT ({PK[tabel]}) DO UPDATE SET {update}"
            )
        print(f"   ok  {tabel:20s} {len(baris)} baris")

    # ── Seed matching_queries dari file ekspor ───────────────────────────
    cfg_path = Path(args.config) if args.config else (DISINI / "matching_queries.json")
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        rows = cfg.get("matching_queries", cfg)
        print("\n== seed matching_queries ==")
        for r in rows:
            jalankan(
                "INSERT INTO matching_queries (grade_code, matching_query) "
                f"VALUES ({lit(r['grade_code'])}, {lit(r['matching_query'])}) "
                "ON CONFLICT (grade_code) DO UPDATE SET matching_query=EXCLUDED.matching_query"
            )
            print(f"   ok  grade {r['grade_code']}  ({len(r['matching_query'])} chars)")
    else:
        print(f"\n!! {cfg_path.name} tidak ada — matching_queries belum di-seed.")
        print("   Tanpa itu, matching grade 1-5 tidak bisa jalan.")

    print()
    lapor(con, tabel_ada())
    return 0


def lapor(con, tabel: list[str]):
    print("== kondisi database synchrono ==")
    if not tabel:
        print("   (kosong)")
        return
    for t in tabel:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM pg.public.{t}").fetchone()[0]
            print(f"   {t:22s} {n:>10,d} baris")
        except Exception as e:
            print(f"   {t:22s} ? ({str(e).splitlines()[0][:40]})")


if __name__ == "__main__":
    raise SystemExit(main())
