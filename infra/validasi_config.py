"""
Periksa kesehatan konfigurasi aturan grading.

    python validasi_config.py

Menjalankan validasi yang sama dengan yang dipakai API 4 sebelum menulis, tapi
terhadap konfigurasi yang SEDANG berlaku. Berguna sesudah menyunting tabel
langsung lewat SQL — jalur itu melewati API dan melewati validasinya.

Keluar dengan status 1 kalau ada masalah, sehingga bisa dipakai di skrip.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "lib"))

import duckdb  # noqa: E402

from _config import (ELEMEN, HURUF, baca_kriteria_penuh, baca_semua,  # noqa: E402
                     validasi)

PG = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")


def main() -> int:
    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '{PG}' AS pg (TYPE postgres)")

    kriteria = baca_kriteria_penuh(con)
    semua = baca_semua(con)

    print("KRITERIA GRADING (urutan evaluasi, yang pertama cocok menang)\n")
    print(f"  {'':3s} {'urut':>4s} {'kolom NIK':11s} "
          + " ".join(f"{e[:6]:>6s}" for e in ELEMEN) + f" {'trust':>6s}  aktif")
    print("  " + "-" * (3 + 5 + 12 + 7 * len(ELEMEN) + 9 + 8))
    for k in kriteria:
        nilai = " ".join(
            ("   -  " if k.get(f"min_{e}") is None else f"{k[f'min_{e}']:>6.2f}")
            for e in ELEMEN
        )
        trust = ("   -  " if k["min_nik_trusted"] is None
                 else f"{k['min_nik_trusted']:>6.2f}")
        print(f"  {HURUF.get(k['grade_id'], '?'):3s} {k['urutan']:>4d} "
              f"{k['nik_kolom']:11s} {nilai} {trust}  "
              f"{'ya' if k['aktif'] else 'TIDAK'}")

    print("\nPITA SKOR & KELAYAKAN\n")
    for g in semua["grades"]:
        sk = g["score"] or {}
        tanda = "" if g["criteria"] else "   (kriteria di kode)"
        print(f"  {g['gradeLetter']}  {sk.get('min'):>3}-{sk.get('max'):<3}  "
              f"proceed={str(sk.get('canProceed')):5s}  "
              f"{sk.get('severityLabel','')}{tanda}")

    pita = [{"grade_id": g["gradeId"], "grade_letter": g["gradeLetter"],
             "score_min": (g["score"] or {}).get("min"),
             "score_max": (g["score"] or {}).get("max")}
            for g in semua["grades"] if g["score"]]

    masalah = validasi(kriteria, pita)
    print()
    if masalah:
        print(f"!! {len(masalah)} MASALAH DITEMUKAN\n")
        for m in masalah:
            print(f"   - {m}")
        return 1

    print("Konfigurasi sehat: tidak ada grade yang mustahil tercapai, "
          "pita skor tidak tumpang tindih.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
