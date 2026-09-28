"""
Pola hibrida DuckDB + Polars: apakah matching jadi lebih cepat?

    docker exec synchrono-service python /synchrono/beban/uji_hibrida.py besar-1000k-padan

POLA YANG DIUJI

Diambil dari `data-matching/processing/matching_service.py`, cara yang sama
persis:

    con.register("incoming_df", incoming_df.to_arrow())   # Polars -> DuckDB
    joined = con.execute(SQL_JOIN).pl()                   # DuckDB -> Polars
    for row in joined.iter_rows(named=True):              # skoring di Python
        score = JaroWinkler.similarity(...)

Jadi pembagiannya: DuckDB mengerjakan JOIN, Polars jadi pembawa data lewat
Arrow, dan skoring fuzzy dikerjakan loop Python dengan rapidfuzz.

Matching kita mengerjakan ketiganya di dalam SQL — `jaro_winkler_similarity`
milik DuckDB, lalu `QUALIFY` memilih kandidat terbaik. Tidak ada baris yang
menyeberang ke Python sama sekali.

YANG DIBANDINGKAN

    kita       n5 (join) + n6 (skor & klasifikasi), keduanya SQL
    hibrida    join SQL yang SAMA, lalu skoring di loop Python + rapidfuzz

Join-nya sengaja dibuat identik supaya yang terukur hanya bagian skoringnya —
di situlah kedua pendekatan berbeda. Skor kedua sisi diadu di akhir; kalau
berbeda, angka kecepatannya tidak berarti apa-apa.
"""

from __future__ import annotations

import argparse
import resource
import sys
import time

sys.path.insert(0, "/synchrono/lib")
sys.path.insert(0, "/components/matching")

from rapidfuzz.distance import JaroWinkler  # noqa: E402

from _shared import TABEL_JOIN, BOBOT, WILAYAH  # noqa: E402


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def aman_jaro(a, b) -> float:
    """Salinan safe_jaro(): nol kalau salah satu sisi kosong."""
    if not a or not b:
        return 0.0
    return JaroWinkler.similarity(str(a), str(b))


# ── Sisi hibrida ───────────────────────────────────────────────────────────

def skor_python(baris: dict, grade: int) -> float:
    """Bobot yang sama dengan sql_skor(), dihitung di Python."""
    if grade == 5:
        wil = [aman_jaro(baris.get(f"{w}_clean"), baris.get(f"{w}_master_clean"))
               for w in WILAYAH
               if baris.get(f"{w}_clean") and baris.get(f"{w}_master_clean")]
        return (
            aman_jaro(baris.get("nama_clean"), baris.get("nama_master_clean")) * 0.5
            + (sum(wil) / len(wil) if wil else 0.0) * 0.3
            + aman_jaro(baris.get("nama_ibu_clean"), baris.get("nama_ibu_master_clean")) * 0.1
            + aman_jaro(str(baris.get("tanggal_lahir_clean") or ""),
                        str(baris.get("tanggal_lahir_master_clean") or "")) * 0.1
        ) * 100

    total = 0.0
    for field, bobot in BOBOT[grade]:
        kiri = baris.get(f"{field}_clean")
        kanan = baris.get("nama_master_clean" if field == "nama"
                          else f"{field}_master_clean")
        if field == "tanggal_lahir":
            kiri, kanan = str(kiri or ""), str(kanan or "")
        total += aman_jaro(kiri, kanan) * bobot
    return total * 100


def jalan_hibrida(con, grade: int) -> dict:
    """
    Pola temannya: hasil join ditarik ke Polars, lalu diloop di Python.

    `.pl()` memakai jembatan Arrow yang sama dengan `con.register(...to_arrow())`
    di kode aslinya — hanya arahnya terbalik. Yang diukur ongkos memindahkan
    barisnya ke Python plus skoringnya, dan itu memang inti polanya.
    """
    t = time.perf_counter()
    joined = con.execute(f"SELECT * FROM {TABEL_JOIN}").pl()
    t_tarik = time.perf_counter() - t

    t = time.perf_counter()
    hasil = {}
    for baris in joined.iter_rows(named=True):
        rid = baris["incoming_row_id"]
        if baris["nik_master"] is None:
            hasil.setdefault(rid, (0.0, None))
            continue
        s = skor_python(baris, grade)
        # Kandidat terbaik per baris incoming, sama seperti QUALIFY di n6.
        if rid not in hasil or s > hasil[rid][0]:
            hasil[rid] = (s, baris["nik_master"])
    t_skor = time.perf_counter() - t

    return {"tarik": t_tarik, "skor": t_skor, "baris": joined.height,
            "hasil": {k: (round(v[0], 2), v[1]) for k, v in hasil.items()}}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("berkas")
    p.add_argument("--bucket", default="bucket-test")
    p.add_argument("--grade", type=int, default=1)
    p.add_argument("--file-id", default="uji-hibrida")
    p.add_argument("--hanya", choices=["kita", "hibrida"])
    a = p.parse_args()

    import n1_open_session as n1
    import n2_prepare_incoming as n2
    import n3_prepare_master as n3
    import n4_load_config as n4
    import n5_run_join as n5
    import n6_score_classify as n6

    jalur = f"s3://{a.bucket}/uploads/{a.berkas}/data.parquet"
    ses = n1.jalankan(a.file_id, jalur, a.grade, pakai_enriched=False)
    for node in (n2, n3, n4):
        ses = node.jalankan(ses)

    # Join dikerjakan SEKALI dan dipakai kedua sisi. Kalau masing-masing
    # menjoin sendiri, yang terukur sebagian join — padahal join-nya sama.
    t = time.perf_counter()
    ses = n5.jalankan(ses)
    t_join = time.perf_counter() - t

    con = ses.data["con"] if hasattr(ses, "data") else ses["con"]
    n_join = con.execute(f"SELECT count(*) FROM {TABEL_JOIN}").fetchone()[0]

    print(f"\n  berkas {a.berkas}, grade {a.grade}")
    print(f"  join (dipakai kedua sisi) : {t_join:6.2f} detik, {n_join:,} baris kandidat\n")

    kita = hib = None

    if a.hanya in (None, "kita"):
        t = time.perf_counter()
        ses2 = n6.jalankan(ses)
        t_kita = time.perf_counter() - t
        kita = dict(con.execute("""
            SELECT id_incoming, match_score FROM match_results
        """).fetchall())
        print(f"  KITA (skor di SQL)        : {t_kita:6.2f} detik   "
              f"RSS {rss_mb():,.0f} MB")

    if a.hanya in (None, "hibrida"):
        h = jalan_hibrida(con, a.grade)
        t_hib = h["tarik"] + h["skor"]
        print(f"  HIBRIDA (skor di Python)  : {t_hib:6.2f} detik   "
              f"RSS {rss_mb():,.0f} MB")
        print(f"      tarik ke Polars       : {h['tarik']:6.2f} detik")
        print(f"      loop skoring          : {h['skor']:6.2f} detik")
        hib = {k: v[0] for k, v in h["hasil"].items()}

    if not (kita and hib):
        print()
        return 0

    print()
    print("=" * 62)
    print(f"  skor di SQL     : {t_kita:6.2f} detik")
    print(f"  skor di Python  : {t_hib:6.2f} detik")
    print(f"  {'':<16}  SQL {t_hib / t_kita:.1f}x lebih cepat"
          if t_kita > 0 else "")
    print("=" * 62)

    # Kesamaan skor. Toleransi 0,01 karena sisi SQL sudah di-ROUND ke 2 desimal.
    sama = beda = 0
    contoh = []
    for k, v in kita.items():
        w = hib.get(k)
        if w is None:
            continue
        if abs(float(v) - float(w)) <= 0.011:
            sama += 1
        else:
            beda += 1
            if len(contoh) < 5:
                contoh.append((k, float(v), float(w)))
    print(f"\n  Kesamaan skor: {sama:,} sama, {beda:,} berbeda "
          f"(dari {len(kita):,} baris)")
    for k, v, w in contoh:
        print(f"    id={k}  sql={v}  python={w}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
