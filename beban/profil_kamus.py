"""
Kenapa `_lapis_kamus` memakan 20 detik, dan obatnya.

    docker exec synchrono-service python /synchrono/beban/profil_kamus.py besar-1000k

LATAR

`profil_g2.py` menunjukkan G2 memakan 26 dari 34 detik grading pada 1 juta
baris, dan 20 detik di antaranya ada di `_lapis_kamus` sendirian — lapis
pengenalan kolom yang mencocokkan contoh nilai ke kamus tabel master.

Yang aneh: pencocokannya cuma 8 milidetik. Berkas ini menelusuri ke mana 20
detik itu sebenarnya pergi, lalu menguji obatnya.

URUTAN PEMERIKSAAN

    1  operasi atomik    berapa ongkos tiap langkah, diukur sendiri-sendiri
    2  ongkos per nilai  membuktikan ongkosnya lurus terhadap JUMLAH NILAI yang
                         diseberangkan dari Python ke DuckDB, bukan terhadap
                         jumlah pernyataan
    3  obatnya           sampel ditahan di dalam DuckDB, seluruh kolom
                         dicocokkan dalam satu UNPIVOT — nol nilai menyeberang

Keputusan pemetaan kolom kedua cara dibandingkan di akhir. Cepat tanpa
keputusan yang sama bukan perbaikan, melainkan bug.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/synchrono/lib")

import duckdb  # noqa: E402

import _normalisasi as N  # noqa: E402
from _grading import _sql_sumber  # noqa: E402
from _shared import buka_koneksi  # noqa: E402

COCOK = """
    SELECT k.elemen, count(DISTINCT u.rowid) * 1.0 / (SELECT count(*) FROM _uji_nilai)
      FROM _uji_nilai u JOIN kamus_master k ON k.nilai = u.v
     GROUP BY k.elemen ORDER BY 2 DESC
"""


def kutip(nama: str) -> str:
    return '"' + nama.replace('"', '""') + '"'


def ukur(nama: str, fn, ulang: int = 1) -> float:
    t = time.perf_counter()
    for _ in range(ulang):
        fn()
    d = (time.perf_counter() - t) / ulang
    print(f"    {nama:<44} {d * 1000:9.1f} ms")
    return d


# ── 1. Cara yang sekarang ──────────────────────────────────────────────────

def cara_sekarang(con, sampel: dict, sisa: list[str]) -> dict:
    """Salinan setia bagian penghitungan `_lapis_kamus`."""
    out = {}
    for kolom in sisa:
        nilai = [str(v).strip().lower() for v in sampel[kolom]
                 if v is not None and str(v).strip()]
        if len(nilai) < 5:
            continue
        con.execute("CREATE OR REPLACE TEMP TABLE _uji_nilai (v VARCHAR)")
        con.executemany("INSERT INTO _uji_nilai VALUES (?)", [(v,) for v in nilai])
        out[kolom] = con.execute(COCOK).fetchall()
    return out


# ── 2. Obatnya ─────────────────────────────────────────────────────────────

def cara_unpivot(con, view: str, sisa: list[str]) -> dict:
    """
    Seluruh kolom sekaligus, tanpa satu nilai pun keluar ke Python.

    Sampelnya diambil dengan reservoir dan benih yang SAMA PERSIS seperti
    `ambil_sampel`, supaya yang dibandingkan cara menghitungnya, bukan sampel
    yang kebetulan berbeda.
    """
    if not sisa:
        return {}

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _sampel AS
        SELECT * FROM {view} USING SAMPLE reservoir({N.N_SAMPEL} ROWS) REPEATABLE (42)
    """)

    # UNPIVOT menuntut tipe yang seragam, jadi semuanya di-CAST ke VARCHAR dulu.
    pasangan = ", ".join(
        f"lower(trim(CAST({kutip(k)} AS VARCHAR))) AS {kutip(k)}" for k in sisa)
    daftar = ", ".join(kutip(k) for k in sisa)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE _panjang AS
        SELECT kolom, nilai FROM (
            SELECT {pasangan} FROM _sampel
        ) UNPIVOT (nilai FOR kolom IN ({daftar}))
        WHERE nilai IS NOT NULL AND nilai <> ''
    """)

    rows = con.execute("""
        WITH n AS (SELECT kolom, count(*) AS total FROM _panjang GROUP BY kolom)
        SELECT p.kolom, k.elemen,
               count(DISTINCT p.rowid) * 1.0 / any_value(n.total)
          FROM _panjang p
          JOIN n ON n.kolom = p.kolom
          JOIN kamus_master k ON k.nilai = p.nilai
         GROUP BY p.kolom, k.elemen
         ORDER BY 1, 3 DESC
    """).fetchall()

    out: dict[str, list] = {}
    for kolom, elemen, skor in rows:
        out.setdefault(kolom, []).append((elemen, skor))
    return out


# ── 3. Obat kedua: jembatan Arrow ──────────────────────────────────────────

def cara_arrow(con, sampel: dict, sisa: list[str]) -> dict:
    """
    Pola dari `data-matching`: menyeberangkan data lewat Arrow, bukan parameter.

    `con.register(nama, tabel_arrow)` memperlihatkan buffer Arrow apa adanya ke
    DuckDB tanpa menyalin nilainya satu per satu — persis yang dihindari di
    `_lapis_kamus` sekarang. Berbeda dengan UNPIVOT, cara ini tetap memulangkan
    sampelnya ke Python dulu, jadi ia menjawab pertanyaan yang lebih sempit:
    kalau nilainya memang HARUS ada di Python, seberapa murah mengembalikannya?
    """
    import pyarrow as pa

    kolom, nilai = [], []
    for k in sisa:
        v = [str(x).strip().lower() for x in sampel[k]
             if x is not None and str(x).strip()]
        if len(v) < 5:
            continue
        kolom.extend([k] * len(v))
        nilai.extend(v)

    con.register("_uji_arrow", pa.table({
        "idx": list(range(len(nilai))), "kolom": kolom, "nilai": nilai}))
    rows = con.execute("""
        WITH n AS (SELECT kolom, count(*) AS total FROM _uji_arrow GROUP BY kolom)
        SELECT u.kolom, k.elemen, count(DISTINCT u.idx) * 1.0 / any_value(n.total)
          FROM _uji_arrow u
          JOIN n ON n.kolom = u.kolom
          JOIN kamus_master k ON k.nilai = u.nilai
         GROUP BY u.kolom, k.elemen
         ORDER BY 1, 3 DESC
    """).fetchall()

    out: dict[str, list] = {}
    for k, elemen, skor in rows:
        out.setdefault(k, []).append((elemen, skor))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("berkas")
    p.add_argument("--bucket", default="bucket-test")
    a = p.parse_args()

    con = buka_koneksi()
    con.execute(f"CREATE OR REPLACE VIEW raw_df AS SELECT * FROM "
                f"{_sql_sumber(f's3://{a.bucket}/uploads/{a.berkas}/data.parquet')}")
    kolom = [r[0] for r in con.execute("DESCRIBE raw_df").fetchall()]

    t = time.perf_counter()
    sampel = N.ambil_sampel(con, "raw_df", kolom)
    t_sampel = time.perf_counter() - t

    peta, _ = N._lapis_alias(kolom)
    sisa = [k for k in sampel if k not in peta.values()]
    N._siapkan_kamus(con)
    n_kamus = con.execute("SELECT count(*) FROM kamus_master").fetchone()[0]

    print(f"\n  {len(kolom)} kolom, {len(sisa)} belum dikenali   |   "
          f"kamus_master {n_kamus:,} baris")
    print(f"  ambil_sampel (fetchall ke Python)  {t_sampel * 1000:.0f} ms")

    # ── 1. Operasi atomik ──────────────────────────────────────────────────
    satu = [str(v).strip().lower() for v in sampel[sisa[0]]
            if v is not None and str(v).strip()]
    print(f"\n  OPERASI ATOMIK, per satu kolom ({len(satu)} nilai)")
    con.execute("CREATE OR REPLACE TEMP TABLE _uji_nilai (v VARCHAR)")
    ukur("CREATE OR REPLACE TEMP TABLE", lambda: con.execute(
        "CREATE OR REPLACE TEMP TABLE _uji_nilai (v VARCHAR)"), 20)
    con.execute("CREATE OR REPLACE TEMP TABLE _uji_nilai (v VARCHAR)")
    ukur("executemany 300 INSERT", lambda: (
        con.execute("DELETE FROM _uji_nilai"),
        con.executemany("INSERT INTO _uji_nilai VALUES (?)", [(v,) for v in satu])), 5)
    ukur("INSERT ... unnest(?) — 1 parameter", lambda: (
        con.execute("DELETE FROM _uji_nilai"),
        con.execute("INSERT INTO _uji_nilai SELECT unnest(?::VARCHAR[])", [satu])), 5)
    ukur("JOIN dari tabel yang sudah terisi",
         lambda: con.execute(COCOK).fetchall(), 5)

    # ── 2. Ongkosnya lurus terhadap jumlah nilai ───────────────────────────
    #
    # Diukur di koneksi POLOS: tanpa S3, tanpa ATTACH postgres, tanpa batas
    # memori. Kalau di sana pun lurus, sebabnya ada di penyeberangan
    # Python->DuckDB, bukan di konfigurasi kita.
    print("\n  ONGKOS MENYEBERANGKAN NILAI (koneksi DuckDB polos)")
    polos = duckdb.connect()
    polos.execute("CREATE TEMP TABLE t (v VARCHAR)")
    for n in (5, 50, 300, 1000):
        v = [f"nilai contoh {i}" for i in range(n)]
        d = ukur(f"unnest list {n:>5} nilai", lambda v=v: (
            polos.execute("DELETE FROM t"),
            polos.execute("INSERT INTO t SELECT unnest(?::VARCHAR[])", [v])), 3)
        print(f"    {'':<44} {d / n * 1000:9.2f} ms per nilai")
    polos.close()

    # ── 3. Sekarang lawan dua obatnya ──────────────────────────────────────
    t = time.perf_counter()
    lama = cara_sekarang(con, sampel, sisa)
    t_lama = time.perf_counter() - t

    t = time.perf_counter()
    baru = cara_unpivot(con, "raw_df", sisa)
    t_baru = time.perf_counter() - t

    t_arrow, arrow = None, None
    try:
        t = time.perf_counter()
        arrow = cara_arrow(con, sampel, sisa)
        t_arrow = time.perf_counter() - t
    except ImportError:
        print("\n  (pyarrow tidak terpasang — varian Arrow dilewati)")

    print("\n" + "=" * 62)
    print(f"  sekarang (per kolom, lewat Python) : {t_lama:6.2f} detik")
    if t_arrow is not None:
        print(f"  Arrow (register, nol salinan)      : {t_arrow:6.2f} detik"
              f"   {t_lama / t_arrow:5.0f}x")
    print(f"  UNPIVOT (semua di dalam DuckDB)    : {t_baru:6.2f} detik"
          f"   {t_lama / t_baru:5.0f}x" if t_baru > 0 else "")
    print("=" * 62)

    # Yang menentukan pemetaan kolom adalah dua teratas dan selisihnya.
    def puncak(h, k):
        v = h.get(k) or []
        tera = v[0] if v else None
        return (tera[0] if tera else None,
                round(tera[1], 4) if tera else None,
                round(v[1][1], 4) if len(v) > 1 else 0.0)

    beda = [(k, puncak(lama, k), puncak(baru, k))
            for k in sisa if puncak(lama, k) != puncak(baru, k)]
    print(f"\n  Kesamaan keputusan atas {len(sisa)} kolom: "
          + ("SAMA SEMUA" if not beda else f"BEDA di {len(beda)}"))
    for k, x, y in beda:
        print(f"    {k:<24} sekarang={x}  unpivot={y}")
    print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
