"""
Uji regresi lapis kamus: jalur Arrow harus memetakan kolom SAMA PERSIS.

    docker exec synchrono-service python /synchrono/beban/regresi_kamus.py

`_lapis_kamus` dilewati setiap berkas yang masuk, dan hasilnya menentukan kolom
mana yang dianggap NIK, nama, tempat lahir, dan seterusnya. Salah di situ berarti
salah grade. Jadi kecepatan tidak berarti apa-apa sampai terbukti keputusannya
tidak bergeser.

Semua berkas uji di bucket dipetakan dua kali — sekali lewat jalur Arrow yang
baru, sekali lewat jalur lama yang dipaksa aktif — lalu `peta` dan `jejak`-nya
diadu. Berkas lebar (35 kolom) maupun sempit (6 kolom), yang bersih maupun yang
sengaja disamarkan.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/synchrono/lib")

import _normalisasi as N  # noqa: E402
from _grading import _sql_sumber  # noqa: E402
from _shared import buka_koneksi  # noqa: E402


def paksa_jalur_lama():
    """Menyembunyikan pyarrow dari `_skor_kamus` supaya fallback yang jalan."""
    asli = __builtins__.__import__ if hasattr(__builtins__, "__import__") \
        else __builtins__["__import__"]

    def tanpa_arrow(nama, *a, **k):
        if nama == "pyarrow":
            raise ImportError("disembunyikan oleh uji regresi")
        return asli(nama, *a, **k)

    if hasattr(__builtins__, "__import__"):
        __builtins__.__import__ = tanpa_arrow
    else:
        __builtins__["__import__"] = tanpa_arrow
    return asli


def pulihkan_import(asli):
    if hasattr(__builtins__, "__import__"):
        __builtins__.__import__ = asli
    else:
        __builtins__["__import__"] = asli


def petakan(jalur: str) -> tuple[dict, list, float]:
    con = buka_koneksi()
    try:
        con.execute(f"CREATE OR REPLACE VIEW raw_df AS SELECT * FROM {_sql_sumber(jalur)}")
        kolom = [r[0] for r in con.execute("DESCRIBE raw_df").fetchall()]
        t = time.perf_counter()
        h = N.petakan_kolom(con, "raw_df", kolom, izin_ai=False)
        return h["peta"], h["jejak"], time.perf_counter() - t
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", default="bucket-test")
    p.add_argument("--lewati-besar", action="store_true",
                   help="lewati berkas jutaan baris supaya cepat")
    a = p.parse_args()

    con = buka_koneksi()
    semua = [r[0] for r in con.execute(
        f"SELECT file FROM glob('s3://{a.bucket}/uploads/**/data.parquet') ORDER BY 1"
    ).fetchall()]
    con.close()

    if a.lewati_besar:
        semua = [f for f in semua if "besar-" not in f]

    print(f"\n  {len(semua)} berkas diuji\n")
    print(f"  {'berkas':<26}{'arrow':>8}{'lama':>8}  {'':<4}hasil")
    print("  " + "-" * 60)

    gagal = []
    t_arrow_total = t_lama_total = 0.0

    for jalur in semua:
        nama = jalur.split("/uploads/")[1].rsplit("/", 1)[0]

        peta_a, jejak_a, t_a = petakan(jalur)

        asli = paksa_jalur_lama()
        try:
            peta_l, jejak_l, t_l = petakan(jalur)
        finally:
            pulihkan_import(asli)

        t_arrow_total += t_a
        t_lama_total += t_l

        # `jejak` memuat alasan tiap keputusan, termasuk skor pesaingnya, jadi
        # membandingkannya lebih ketat daripada membandingkan `peta` saja.
        kunci_a = sorted((j.get("elemen"), j.get("kolom"), j.get("lapis"))
                         for j in jejak_a if j.get("elemen"))
        kunci_l = sorted((j.get("elemen"), j.get("kolom"), j.get("lapis"))
                         for j in jejak_l if j.get("elemen"))

        cocok = peta_a == peta_l and kunci_a == kunci_l
        tanda = "SAMA" if cocok else "BEDA"
        print(f"  {nama:<26}{t_a:7.2f}s{t_l:7.2f}s  {tanda}")

        if not cocok:
            gagal.append((nama, peta_a, peta_l, kunci_a, kunci_l))

    print("  " + "-" * 60)
    print(f"  {'total':<26}{t_arrow_total:7.2f}s{t_lama_total:7.2f}s"
          + (f"   {t_lama_total / t_arrow_total:.1f}x" if t_arrow_total > 0 else ""))

    if gagal:
        print(f"\n  {len(gagal)} BERKAS BERBEDA:")
        for nama, pa_, pl_, ka, kl in gagal:
            print(f"\n    {nama}")
            for e in sorted(set(pa_) | set(pl_)):
                if pa_.get(e) != pl_.get(e):
                    print(f"      {e:<16} arrow={pa_.get(e)!r}  lama={pl_.get(e)!r}")
            if ka != kl:
                print(f"      jejak arrow : {ka}")
                print(f"      jejak lama  : {kl}")
        print()
        return 1

    print(f"\n  SEMUA {len(semua)} BERKAS MEMETAKAN SAMA PERSIS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
