"""
Uji skala: grading + matching pada SATU berkas besar, dengan waktu dan memori.

    docker exec synchrono-service python /synchrono/beban/uji_skala.py besar-1000k
    docker exec synchrono-service python /synchrono/beban/uji_skala.py besar-5000k --tanpa-matching

SATU UKURAN PER PROSES, DAN ITU DISENGAJA.

Memori puncak diukur lewat `ru_maxrss`, yang merupakan tanda air tertinggi
proses — ia hanya naik, tidak pernah turun. Menguji 1 juta lalu 5 juta dalam
satu proses akan membuat angka 1 juta terbawa ke pengukuran 5 juta, dan yang
terbaca bukan lagi memori masing-masing. `skala.ps1` yang memanggil berkas ini
sekali per ukuran.

DUA ANGKA MEMORI YANG BERBEDA ARTINYA

    RSS puncak      memori yang benar-benar dipegang proses. Inilah yang harus
                    disediakan server.
    tumpahan disk   berapa yang DuckDB tulis ke disk karena tidak cukup memori.
                    Nol berarti seluruhnya masih tertampung; di atas nol berarti
                    kita sudah melewati titik itu, dan waktunya melambat.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time

sys.path.insert(0, "/synchrono/lib")
sys.path.insert(0, "/components/matching")

from _grading import jalankan_penuh  # noqa: E402
from _shared import buka_koneksi  # noqa: E402


def mb(byte: float) -> float:
    return byte / 1024 / 1024


def rss_puncak_mb() -> float:
    """ru_maxrss di Linux satuannya kilobyte."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def duckdb_memori(con) -> tuple[float, float]:
    """(memori DuckDB, tumpahan ke disk) dalam MB."""
    try:
        m, t = con.execute("""
            SELECT sum(memory_usage_bytes), sum(temporary_storage_bytes)
              FROM duckdb_memory()
        """).fetchone()
        return mb(m or 0), mb(t or 0)
    except Exception:
        return 0.0, 0.0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("file_id")
    p.add_argument("--bucket", default="bucket-test")
    p.add_argument("--tanpa-matching", action="store_true")
    p.add_argument("--grade-matching", type=int, default=1)
    p.add_argument("--json", action="store_true",
                   help="Keluarkan satu baris JSON, untuk dikumpulkan skala.ps1.")
    args = p.parse_args()

    kunci = f"uploads/{args.file_id}/data.parquet"
    enriched = f"uploads/{args.file_id}/enriched.parquet"
    hasil: dict = {"file_id": args.file_id}

    # ── Grading ──────────────────────────────────────────────────────────
    tahap: dict[str, float] = {}
    terakhir = [time.perf_counter()]

    def lapor(nama: str) -> None:
        kini = time.perf_counter()
        # Tahap sebelumnya baru selesai saat tahap berikutnya dilaporkan.
        if tahap:
            tahap[list(tahap)[-1]] = kini - terakhir[0]
        tahap[nama.split()[0]] = 0.0
        terakhir[0] = kini

    mulai = time.perf_counter()
    keluaran = jalankan_penuh(
        {"file_id": args.file_id, "s3_bucket": args.bucket,
         "parquet_key": kunci, "enriched_key": enriched},
        lapor=lapor,
    )
    detik = time.perf_counter() - mulai
    if tahap:
        tahap[list(tahap)[-1]] = time.perf_counter() - terakhir[0]

    s = keluaran["hasil"]["summary"]
    sesi = keluaran["sesi"]
    hasil.update({
        "baris": s["recordCount"],
        "grade": s["gradeLetter"],
        "skor": s["qualityScore"],
        "anomali": s["anomalyCount"],
        "grading_detik": round(detik, 2),
        "grading_baris_per_detik": round(s["recordCount"] / detik),
        "enriched_mb": round(mb(sesi.get("parquet_size_bytes") or 0), 1),
        "rss_puncak_mb": round(rss_puncak_mb(), 1),
        "tahap": {k: round(v, 2) for k, v in tahap.items()},
    })

    con = buck = None
    try:
        con = buka_koneksi()
        dmem, dtmp = duckdb_memori(con)
        hasil["duckdb_mb"] = round(dmem, 1)
        hasil["tumpahan_disk_mb"] = round(dtmp, 1)
        kol = len(con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('s3://{args.bucket}/{kunci}')"
        ).fetchall())
        hasil["kolom"] = kol
    finally:
        if con:
            con.close()

    # ── Matching ─────────────────────────────────────────────────────────
    if not args.tanpa_matching:
        import n1_open_session as n1
        import n2_prepare_incoming as n2
        import n3_prepare_master as n3
        import n4_load_config as n4
        import n5_run_join as n5
        import n6_score_classify as n6
        import n7_persist as n7

        mulai = time.perf_counter()
        # pakai_enriched=False: berkas enriched ditunjuk langsung, tidak dicari
        # dari tabel grading_jobs — grading di atas dijalankan sebagai fungsi,
        # jadi tidak ada baris job yang tercatat.
        ses = n1.jalankan(args.file_id, f"s3://{args.bucket}/{enriched}",
                          args.grade_matching, pakai_enriched=False)
        for node in (n2, n3, n4, n5, n6):
            ses = node.jalankan(ses)
        ringkas = n7.jalankan(ses, dry_run=True).data
        m_detik = time.perf_counter() - mulai

        hasil.update({
            "matching_detik": round(m_detik, 2),
            "matching_baris_per_detik": round(ringkas["processed_rows"] / m_detik),
            "cocok": ringkas["matched_rows"],
            "tinjau": ringkas["manual_review_rows"],
            "tak_cocok": ringkas["unmatched_rows"],
            "rss_puncak_akhir_mb": round(rss_puncak_mb(), 1),
        })

    if args.json:
        print("HASIL_JSON " + json.dumps(hasil))
        return 0

    print(f"\n{'=' * 62}")
    print(f"  {args.file_id}   {hasil['baris']:,} baris x {hasil.get('kolom')} kolom")
    print("=" * 62)
    print(f"  grade            : {hasil['grade']}  skor {hasil['skor']}  "
          f"anomali {hasil['anomali']:,}")
    print(f"  grading          : {hasil['grading_detik']:,} detik "
          f"({hasil['grading_baris_per_detik']:,} baris/detik)")
    for k, v in hasil["tahap"].items():
        print(f"     {k:<4s} {v:>8.2f} detik")
    print(f"  enriched parquet : {hasil['enriched_mb']:,} MB")
    print(f"  RSS puncak       : {hasil['rss_puncak_mb']:,} MB")
    print(f"  tumpahan disk    : {hasil['tumpahan_disk_mb']:,} MB")
    if "matching_detik" in hasil:
        print(f"  matching         : {hasil['matching_detik']:,} detik "
              f"({hasil['matching_baris_per_detik']:,} baris/detik)")
        print(f"     cocok {hasil['cocok']:,} / tinjau {hasil['tinjau']:,} / "
              f"tak cocok {hasil['tak_cocok']:,}")
        print(f"  RSS puncak akhir : {hasil['rss_puncak_akhir_mb']:,} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
