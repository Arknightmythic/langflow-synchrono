"""
Seeder matching_queries — dibaca dari berkas ekspor, bukan ditulis sebagai SQL.

Query matching tiap grade panjangnya ratusan sampai ribuan karakter dan disalin
apa adanya dari sistem yang sudah berjalan. Menempelkannya ke dalam berkas .sql
berarti meng-escape kutip di badan SQL yang isinya juga SQL — mudah rusak dan
sulit dibandingkan saat berubah. Jadi sumbernya tetap `matching_queries.json`
dan berkas ini yang memasukkannya.

Sama seperti seeder lain: ON CONFLICT DO NOTHING, tidak pernah menimpa.
Kalau berkas ekspornya tidak ada, seeder ini DILEWATI — matching memang
opsional, dan grading tidak membutuhkannya.
"""

import json
from pathlib import Path

# infra/db/seeder/005_*.py -> infra/matching_queries.json
EKSPOR = Path(__file__).resolve().parents[2] / "matching_queries.json"


def q(nilai) -> str:
    if nilai is None:
        return "NULL"
    if isinstance(nilai, (int, float)):
        return str(nilai)
    return "'" + str(nilai).replace("'", "''") + "'"


def jalankan(sql) -> None:
    if not EKSPOR.exists():
        print(f"   (lewati: {EKSPOR.name} tidak ada — matching opsional)")
        return

    isi = json.loads(EKSPOR.read_text(encoding="utf-8"))
    baris = isi.get("matching_queries", isi)

    for r in baris:
        sql(
            "INSERT INTO matching_queries (grade_code, matching_query) "
            f"VALUES ({q(r['grade_code'])}, {q(r['matching_query'])}) "
            "ON CONFLICT (grade_code) DO NOTHING"
        )
        print(f"   ok  grade {r['grade_code']}  "
              f"({len(r['matching_query'])} karakter)")
