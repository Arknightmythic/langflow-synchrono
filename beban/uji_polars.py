"""
Apakah Polars membuat grading lebih cepat?

    docker exec synchrono-service python /synchrono/beban/uji_polars.py besar-1000k

YANG DIADU, DAN KENAPA HANYA ITU

Grading punya enam tahap. Hanya DUA yang berupa kerja dataframe sungguhan —
perhitungan per baris atas seluruh berkas:

    G3  bersihkan NIK + tandai anomali
    G5  tulis enriched parquet

Empat tahap lainnya bukan urusan mesin dataframe: G1 membuka koneksi, G2
mengenali kolom (bekerja atas sampel dan atas tabel master), G4 menghitung
agregat, G6 menyusun JSON. Mengganti mesin tidak menyentuhnya. Jadi yang
ditimbang di sini G3 dan G5 saja — dan ITULAH batas atas keuntungan Polars,
berapa pun cepatnya ia.

KESETARAAN

Ekspresi Polars di bawah disalin dari SQL-nya satu per satu: pembersihan NIK
(notasi ilmiah, float utuh, buang non-digit), panjang 16, kode provinsi dan
kecamatan diadu ke tabel rujukan yang sama, hari/bulan/tahun dari digit 7-12,
kelamin dari hari > 40, gelar dan patronimik pada nama, duplikasi NIK lewat
window, lalu perakitan `anomaly_type`.

Hasil kedua mesin dibandingkan KOLOM PER KOLOM di akhir. Angka kecepatan yang
tidak disertai bukti kesamaan hasil tidak ada gunanya.
"""

from __future__ import annotations

import argparse
import resource
import sys
import time

sys.path.insert(0, "/synchrono/lib")

import polars as pl  # noqa: E402

import _wilayah  # noqa: E402
from _grading import _sql_sumber  # noqa: E402
from _shared import (S3_ENDPOINT, S3_KEY, S3_SECRET, S3_USE_SSL,  # noqa: E402
                     buka_koneksi)

def rss_mb() -> float:
    """ru_maxrss di Linux satuannya kilobyte."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


GELAR_DEPAN = ["dr", "drs", "ir", "h", "hj", "prof", "kh", "tn", "ny", "sdr"]
GELAR_BELAKANG = ["se", "sh", "si", "st", "skom", "spd", "mm", "mt", "ma", "phd"]


def opsi_s3() -> dict:
    return {
        "aws_access_key_id": S3_KEY,
        "aws_secret_access_key": S3_SECRET,
        "aws_endpoint_url": ("https://" if S3_USE_SSL else "http://") + S3_ENDPOINT,
        "aws_region": "us-east-1",
        "aws_allow_http": "true",
        "aws_virtual_hosted_style_request": "false",
    }


# ── Sisi DuckDB ────────────────────────────────────────────────────────────

def jalan_duckdb(jalur_in: str, jalur_out: str) -> dict:
    con = buka_koneksi()
    con.execute(f"CREATE OR REPLACE VIEW raw_df AS SELECT * FROM {_sql_sumber(jalur_in)}")
    _wilayah.muat(con)

    t = time.perf_counter()
    con.execute("""
        CREATE OR REPLACE TABLE vonis AS
        WITH a AS (
            SELECT *,
                   trim(CAST(nik AS VARCHAR)) AS __mentah
              FROM raw_df
        ), b AS (
            SELECT *,
                   regexp_matches(upper(__mentah), '^[0-9](\\.[0-9]+)?E[+-]?[0-9]+$') AS __excel,
                   CASE
                     WHEN regexp_matches(upper(__mentah), '^[0-9](\\.[0-9]+)?E[+-]?[0-9]+$')
                       THEN CAST(CAST(TRY_CAST(__mentah AS DOUBLE) AS DECIMAL(20,0)) AS VARCHAR)
                     WHEN regexp_matches(__mentah, '^[0-9]+\\.0*$')
                       THEN regexp_replace(__mentah, '\\..*$', '')
                     ELSE regexp_replace(__mentah, '[^0-9]', '', 'g')
                   END AS __nik_clean,
                   lower(trim(CAST(nama_lengkap AS VARCHAR))) AS __nama_l
              FROM a
        ), c AS (
            SELECT *,
                   length(__nik_clean) = 16 AS __len_ok,
                   substr(__nik_clean, 1, 2) AS __prov,
                   substr(__nik_clean, 1, 6) AS __kec,
                   TRY_CAST(substr(__nik_clean, 7, 2) AS INTEGER) AS __hari_mentah,
                   TRY_CAST(substr(__nik_clean, 9, 2) AS INTEGER) AS __bulan,
                   TRY_CAST(substr(__nik_clean, 11, 2) AS INTEGER) AS __tahun
              FROM b
        ), d AS (
            SELECT *,
                   substr(__nik_clean, 1, 2) IN (SELECT kode_prov FROM ref_wilayah) AS __prov_ok,
                   substr(__nik_clean, 1, 6) IN (SELECT kode6 FROM ref_wilayah) AS __kec_ok,
                   CASE WHEN __hari_mentah > 40 THEN __hari_mentah - 40
                        ELSE __hari_mentah END AS __hari,
                   CASE WHEN __hari_mentah > 40 THEN 'p' ELSE 'l' END AS __nik_jk,
                   regexp_matches(__nama_l, '(^| )(bin|binti) ') AS __bin,
                   regexp_matches(__nama_l, '(^|[ .])(dr|drs|ir|h|hj|prof|kh|tn|ny|sdr)[. ]') AS __gelar
              FROM c
        ), e AS (
            SELECT *,
                   CASE WHEN __len_ok THEN count(*) OVER (PARTITION BY __nik_clean)
                        ELSE 1 END AS __kembar
              FROM d
        )
        SELECT __nik_clean, __len_ok, __prov, __kec, __prov_ok, __kec_ok,
               __hari, __bulan, __tahun, __nik_jk, __bin, __gelar,
               (__kembar > 1) AS __dobel,
               trim(BOTH ';' FROM
                 CASE WHEN nullif(__mentah,'') IS NULL OR __mentah='-' THEN 'EMPTY_NIK;' ELSE '' END ||
                 CASE WHEN NOT __len_ok AND nullif(__mentah,'') IS NOT NULL THEN 'INVALID_NIK_LENGTH;' ELSE '' END ||
                 CASE WHEN __len_ok AND NOT __prov_ok THEN 'NIK_PROVINCE_INVALID;' ELSE '' END ||
                 CASE WHEN __len_ok AND NOT __kec_ok THEN 'NIK_KECAMATAN_INVALID;' ELSE '' END ||
                 CASE WHEN __kembar > 1 THEN 'DUPLICATE_NIK;' ELSE '' END ||
                 CASE WHEN __gelar THEN 'NAME_HAS_TITLE;' ELSE '' END ||
                 CASE WHEN __bin THEN 'NAME_HAS_PATRONYM;' ELSE '' END
               ) AS __jenis
          FROM e
    """)
    t_g3 = time.perf_counter() - t
    n = con.execute("SELECT count(*) FROM vonis").fetchone()[0]

    t = time.perf_counter()
    con.execute(f"COPY vonis TO '{jalur_out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    t_g5 = time.perf_counter() - t

    contoh = con.execute("""
        SELECT __nik_clean, __len_ok, __prov_ok, __kec_ok, __hari, __bulan,
               __tahun, __nik_jk, __bin, __gelar, __dobel, __jenis
          FROM vonis ORDER BY __nik_clean, __jenis LIMIT 200000
    """).fetchall()
    con.close()
    return {"g3": t_g3, "g5": t_g5, "baris": n, "contoh": contoh}


# ── Sisi Polars ────────────────────────────────────────────────────────────

def rencana_polars(jalur_in: str, prov: set[str], kec: set[str], opsi: dict):
    """Rencana lazy-nya saja. Dipakai dua mode eksekusi, jadi tidak ada
    kemungkinan keduanya diam-diam menghitung hal yang berbeda."""

    mentah = pl.col("nik").cast(pl.Utf8).str.strip_chars()
    excel = mentah.str.to_uppercase().str.contains(r"^[0-9](\.[0-9]+)?E[+-]?[0-9]+$")
    nama_l = pl.col("nama_lengkap").cast(pl.Utf8).str.strip_chars().str.to_lowercase()

    nik_clean = (
        pl.when(excel)
          .then(mentah.cast(pl.Float64, strict=False).round(0)
                      .cast(pl.Decimal(20, 0), strict=False).cast(pl.Utf8))
          .when(mentah.str.contains(r"^[0-9]+\.0*$"))
          .then(mentah.str.replace(r"\..*$", ""))
          .otherwise(mentah.str.replace_all(r"[^0-9]", ""))
    )

    return (
        pl.scan_parquet(jalur_in, storage_options=opsi)
          .with_columns(__mentah=mentah, __nama_l=nama_l)
          .with_columns(__nik_clean=nik_clean, __excel=excel)
          .with_columns(
              __len_ok=pl.col("__nik_clean").str.len_chars() == 16,
              __prov=pl.col("__nik_clean").str.slice(0, 2),
              __kec=pl.col("__nik_clean").str.slice(0, 6),
              __hari_mentah=pl.col("__nik_clean").str.slice(6, 2).cast(pl.Int32, strict=False),
              __bulan=pl.col("__nik_clean").str.slice(8, 2).cast(pl.Int32, strict=False),
              __tahun=pl.col("__nik_clean").str.slice(10, 2).cast(pl.Int32, strict=False),
          )
          .with_columns(
              __prov_ok=pl.col("__prov").is_in(list(prov)),
              __kec_ok=pl.col("__kec").is_in(list(kec)),
              __hari=pl.when(pl.col("__hari_mentah") > 40)
                       .then(pl.col("__hari_mentah") - 40)
                       .otherwise(pl.col("__hari_mentah")),
              __nik_jk=pl.when(pl.col("__hari_mentah") > 40)
                         .then(pl.lit("p")).otherwise(pl.lit("l")),
              __bin=pl.col("__nama_l").str.contains(r"(^| )(bin|binti) "),
              __gelar=pl.col("__nama_l").str.contains(
                  r"(^|[ .])(dr|drs|ir|h|hj|prof|kh|tn|ny|sdr)[. ]"),
          )
          .with_columns(
              __kembar=pl.when(pl.col("__len_ok"))
                         .then(pl.len().over("__nik_clean"))
                         .otherwise(pl.lit(1, dtype=pl.UInt32))
          )
          .with_columns(__dobel=pl.col("__kembar") > 1)
          .with_columns(
              __jenis=pl.concat_str([
                  pl.when(pl.col("__mentah").is_null() | (pl.col("__mentah") == "")
                          | (pl.col("__mentah") == "-"))
                    .then(pl.lit("EMPTY_NIK;")).otherwise(pl.lit("")),
                  pl.when(~pl.col("__len_ok") & pl.col("__mentah").is_not_null()
                          & (pl.col("__mentah") != ""))
                    .then(pl.lit("INVALID_NIK_LENGTH;")).otherwise(pl.lit("")),
                  pl.when(pl.col("__len_ok") & ~pl.col("__prov_ok"))
                    .then(pl.lit("NIK_PROVINCE_INVALID;")).otherwise(pl.lit("")),
                  pl.when(pl.col("__len_ok") & ~pl.col("__kec_ok"))
                    .then(pl.lit("NIK_KECAMATAN_INVALID;")).otherwise(pl.lit("")),
                  pl.when(pl.col("__kembar") > 1)
                    .then(pl.lit("DUPLICATE_NIK;")).otherwise(pl.lit("")),
                  pl.when(pl.col("__gelar"))
                    .then(pl.lit("NAME_HAS_TITLE;")).otherwise(pl.lit("")),
                  pl.when(pl.col("__bin"))
                    .then(pl.lit("NAME_HAS_PATRONYM;")).otherwise(pl.lit("")),
              ]).str.strip_chars(";")
          )
    )


KOLOM_BANDING = ["__nik_clean", "__len_ok", "__prov_ok", "__kec_ok", "__hari",
                 "__bulan", "__tahun", "__nik_jk", "__bin", "__gelar",
                 "__dobel", "__jenis"]


def jalan_polars(jalur_in: str, jalur_out: str, prov: set[str], kec: set[str]) -> dict:
    """Mode biasa: seluruh hasil ditahan di memori, lalu ditulis."""
    opsi = opsi_s3()
    lf = rencana_polars(jalur_in, prov, kec, opsi)

    t = time.perf_counter()
    df = lf.collect()
    t_g3 = time.perf_counter() - t

    t = time.perf_counter()
    df.write_parquet(jalur_out, storage_options=opsi, compression="zstd")
    t_g5 = time.perf_counter() - t

    contoh = (df.select(KOLOM_BANDING).sort(["__nik_clean", "__jenis"])
                .head(200000).rows())
    return {"g3": t_g3, "g5": t_g5, "baris": df.height, "contoh": contoh}


def jalan_polars_stream(jalur_in: str, jalur_out: str, prov: set[str],
                        kec: set[str]) -> dict:
    """
    Mode streaming: `sink_parquet` mengalirkan hasilnya langsung ke berkas,
    tanpa pernah menahan seluruh tabel di memori.

    Inilah jawaban Polars untuk data yang lebih besar dari RAM, jadi menyimpulkan
    apa pun tentang OOM tanpa mencobanya tidak adil. Perhatikan bahwa G3 dan G5
    TIDAK bisa dipisah di sini — justru itu gunanya: menghitung dan menulis jadi
    satu aliran. Angkanya diadu ke G3+G5 DuckDB, bukan ke salah satunya.
    """
    opsi = opsi_s3()
    lf = rencana_polars(jalur_in, prov, kec, opsi)

    t = time.perf_counter()
    lf.sink_parquet(jalur_out, storage_options=opsi, compression="zstd",
                    engine="streaming")
    t_total = time.perf_counter() - t

    n = pl.scan_parquet(jalur_out, storage_options=opsi).select(
        pl.len()).collect().item()
    contoh = (pl.scan_parquet(jalur_out, storage_options=opsi)
                .select(KOLOM_BANDING).collect()
                .sort(["__nik_clean", "__jenis"]).head(200000).rows())
    return {"g3": t_total, "g5": 0.0, "baris": n, "contoh": contoh}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("berkas")
    p.add_argument("--bucket", default="bucket-test")
    # Satu mesin per proses. RSS puncak adalah tanda air tertinggi proses —
    # menjalankan DuckDB lebih dulu membuat angka Polars ikut terbawa, dan
    # sebaliknya. Dipisah supaya masing-masing diukur dari nol.
    p.add_argument("--hanya", choices=["duckdb", "polars", "stream"])
    a = p.parse_args()

    masuk = f"s3://{a.bucket}/uploads/{a.berkas}/data.parquet"

    # Tabel rujukan ditarik SEKALI lewat DuckDB dan dipakai kedua sisi, supaya
    # yang terukur perhitungannya, bukan cara memuat rujukan.
    con = buka_koneksi()
    _wilayah.muat(con)
    prov = {r[0] for r in con.execute("SELECT DISTINCT kode_prov FROM ref_wilayah").fetchall()}
    kec = {r[0] for r in con.execute("SELECT DISTINCT kode6 FROM ref_wilayah").fetchall()}
    con.close()
    print(f"\n  rujukan wilayah: {len(prov)} provinsi, {len(kec):,} kecamatan")

    d = q = None
    if a.hanya in (None, "duckdb"):
        print("\n  --- DuckDB ---")
        d = jalan_duckdb(masuk, f"s3://{a.bucket}/uji/{a.berkas}-duckdb.parquet")
        print(f"  G3 {d['g3']:6.2f} detik   G5 {d['g5']:6.2f} detik   "
              f"{d['baris']:,} baris   RSS puncak {rss_mb():,.0f} MB")

    if a.hanya == "stream":
        print("\n  --- Polars streaming (sink_parquet) ---")
        q = jalan_polars_stream(
            masuk, f"s3://{a.bucket}/uji/{a.berkas}-stream.parquet", prov, kec)
        print(f"  G3+G5 {q['g3']:6.2f} detik   {q['baris']:,} baris   "
              f"RSS puncak {rss_mb():,.0f} MB")
        print()
        return 0

    if a.hanya in (None, "polars"):
        print("\n  --- Polars ---")
        q = jalan_polars(masuk, f"s3://{a.bucket}/uji/{a.berkas}-polars.parquet", prov, kec)
        print(f"  G3 {q['g3']:6.2f} detik   G5 {q['g5']:6.2f} detik   "
              f"{q['baris']:,} baris   RSS puncak {rss_mb():,.0f} MB")

    if not (d and q):
        print()
        return 0

    print()
    print("=" * 64)
    print(f"  {'':<14}{'DuckDB':>10}{'Polars':>10}   selisih")
    for nama, kd, kq in (("G3 hitung", d["g3"], q["g3"]), ("G5 tulis", d["g5"], q["g5"]),
                         ("G3+G5", d["g3"] + d["g5"], q["g3"] + q["g5"])):
        arah = f"{kd / kq:.2f}x" if kq > 0 else "-"
        print(f"  {nama:<14}{kd:9.2f}s{kq:9.2f}s   Polars {arah}")
    print("=" * 64)

    # Cepat tanpa hasil yang sama bukan hasil.
    sama = d["contoh"] == q["contoh"]
    print(f"\n  Kesamaan hasil atas {len(d['contoh']):,} baris contoh: "
          + ("SAMA PERSIS" if sama else "BERBEDA"))
    if not sama:
        beda = 0
        for i, (x, y) in enumerate(zip(d["contoh"], q["contoh"])):
            if x != y:
                if beda < 5:
                    print(f"    baris {i}:")
                    print(f"      duckdb {x}")
                    print(f"      polars {y}")
                beda += 1
        print(f"    total {beda:,} baris berbeda dari {len(d['contoh']):,}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
