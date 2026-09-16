"""
Fondasi bersama untuk node matching — DuckDB-native.

ARSITEKTUR

    Backend Synchrono  --HTTP-->  Langflow flow  -->  node 1..7
                                                        |
                                    +-------------------+-------------------+
                                    |                   |                   |
                              SeaweedFS (S3)     PostgreSQL          DuckDB (mesin)
                              parquet incoming   master + config     join, skor, klasifikasi

Service ini TIDAK bergantung pada backend data-matching lama. Satu-satunya
dependency Python-nya adalah `duckdb`.

KENAPA DUCKDB SAJA CUKUP

  * `read_parquet('s3://...')`      — baca langsung dari SeaweedFS lewat httpfs
  * `ATTACH ... (TYPE postgres)`    — master & konfigurasi tanpa ditarik ke memori
  * `jaro_winkler_similarity()`     — bawaan DuckDB, sudah diverifikasi IDENTIK
                                      bit-per-bit dengan rapidfuzz
  * `postgres_execute()`            — DDL/DML ke PostgreSQL

Jadi tidak ada Polars, rapidfuzz, pymysql, maupun client S3.

DATA TIDAK BERPINDAH ANTAR NODE

Node mengoper OBJEK KONEKSI, bukan frame. Tiap tahap membuat view/tabel di
dalam DuckDB, dan node berikutnya merujuknya lewat nama. Ini yang membuat
pemecahan jadi node tidak menimbulkan biaya serialisasi.
"""

from __future__ import annotations

import os

import duckdb
from dotenv import load_dotenv

load_dotenv()

# ── Konfigurasi koneksi ────────────────────────────────────────────────────

PG_DSN = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "localhost:8333")
S3_KEY = os.getenv("S3_ACCESS_KEY", "synchrono")
S3_SECRET = os.getenv("S3_SECRET_KEY", "synchrono123")
S3_USE_SSL = os.getenv("S3_USE_SSL", "false").lower() == "true"

# Nama view yang dirujuk oleh SQL di tabel `matching_queries`. JANGAN diubah —
# kelima query itu disalin apa adanya dari sistem yang sudah berjalan.
VIEW_INCOMING = "incoming_df"
VIEW_MASTER = "master_df"
TABEL_JOIN = "joined_df"
TABEL_HASIL = "match_results"

# ── Macro: padanan safe_jaro() milik ScoringService ────────────────────────
# Mengembalikan 0 kalau salah satu sisi NULL atau string kosong, persis seperti
# implementasi Python-nya. Tanpa ini, jaro_winkler_similarity('', 'x') != 0.
SQL_MACRO = """
CREATE OR REPLACE MACRO j(a, b) AS
    CASE WHEN a IS NULL OR b IS NULL OR a = '' OR b = ''
         THEN 0.0
         ELSE jaro_winkler_similarity(a, b) END
"""


def buka_koneksi() -> duckdb.DuckDBPyConnection:
    """Koneksi DuckDB in-memory dengan SeaweedFS + PostgreSQL sudah tersambung."""
    con = duckdb.connect()

    for ext in ("httpfs", "postgres"):
        con.execute(f"INSTALL {ext}")
        con.execute(f"LOAD {ext}")

    # URL_STYLE 'path' wajib untuk SeaweedFS — ia tidak memakai virtual-host style.
    con.execute(f"""
        CREATE OR REPLACE SECRET seaweed (
            TYPE s3,
            KEY_ID '{S3_KEY}',
            SECRET '{S3_SECRET}',
            ENDPOINT '{S3_ENDPOINT}',
            URL_STYLE 'path',
            USE_SSL {str(S3_USE_SSL).lower()}
        )
    """)
    con.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE postgres)")
    con.execute(SQL_MACRO)
    return con


# ── Normalisasi kolom ──────────────────────────────────────────────────────
# Menyalin persis perilaku ObjectStorageService.load_parquet_from_minio().
# Urutan format tanggal PENTING: dicoba berurutan, yang pertama cocok dipakai.

FORMAT_TANGGAL = ["%d/%m/%Y", "%d-%m-%Y", "%d %m %Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d"]

GENDER_L = ["laki-laki", "laki laki", "pria", "male", "l"]
GENDER_P = ["perempuan", "wanita", "female", "p"]
HIDUP = ["hidup", "h"]
MATI = ["meninggal", "mati", "wafat", "m"]


def _bersih(kolom: str) -> str:
    return f"lower(trim(CAST({kolom} AS VARCHAR)))"


def _sql_tanggal(kolom: str) -> str:
    """6 format dicoba berurutan; tahun < 1900 dibuang jadi NULL."""
    mentah = _bersih(kolom)
    coba = ",\n            ".join(
        f"try_strptime({mentah}, '{f}')" for f in FORMAT_TANGGAL
    )
    return f"""
        CASE WHEN year(COALESCE(
            {coba}
        )) < 1900 THEN NULL
        ELSE CAST(COALESCE(
            {coba}
        ) AS DATE) END"""


def _sql_daftar(kolom: str, nilai: list[str], hasil: str) -> str:
    isi = ", ".join(f"'{v}'" for v in nilai)
    return f"WHEN {_bersih(kolom)} IN ({isi}) THEN '{hasil}'"


def sql_view_incoming(kolom_ada: set[str]) -> str:
    """
    Bangun SELECT normalisasi untuk parquet incoming.

    Kolom yang tidak ada di file dilewati — sama seperti versi Polars yang
    memakai `if "nama" in df.columns`. File grade 1/2 tidak punya tanggal_lahir,
    dan grade 6 bisa saja tidak memasangkan kolom apa pun.
    """
    pilih = []

    if "id" in kolom_ada:
        pilih.append("trim(CAST(id AS VARCHAR)) AS id")
    if "nik" in kolom_ada:
        pilih.append("trim(CAST(nik AS VARCHAR)) AS nik")

    for kol in ("nama", "tempat_lahir", "provinsi", "kabupaten",
                "kecamatan", "kelurahan", "nama_ibu"):
        if kol in kolom_ada:
            pilih.append(f"{kol}")
            pilih.append(f"{_bersih(kol)} AS {kol}_clean")

    if "tanggal_lahir" in kolom_ada:
        pilih.append("tanggal_lahir")
        pilih.append(f"{_sql_tanggal('tanggal_lahir')} AS tanggal_lahir_clean")

    if "jenis_kelamin" in kolom_ada:
        pilih.append("jenis_kelamin")
        pilih.append(f"""CASE
            {_sql_daftar('jenis_kelamin', GENDER_L, 'l')}
            {_sql_daftar('jenis_kelamin', GENDER_P, 'p')}
            ELSE NULL END AS jenis_kelamin_clean""")

    if "status_hidup" in kolom_ada:
        pilih.append(f"""CASE
            {_sql_daftar('status_hidup', HIDUP, 'h')}
            {_sql_daftar('status_hidup', MATI, 'm')}
            ELSE NULL END AS status_hidup_clean""")

    return ",\n        ".join(pilih)


SQL_VIEW_MASTER = f"""
    nik,
    nama_lengkap,
    tempat_lahir,
    tanggal_lahir,
    jenis_kelamin,
    nama_ibu,
    {_bersih('nama_lengkap')} AS nama_master_clean,
    {_bersih('tempat_lahir')} AS tempat_lahir_master_clean,
    {_bersih('provinsi')}     AS provinsi_master_clean,
    {_bersih('kabupaten')}    AS kabupaten_master_clean,
    {_bersih('kecamatan')}    AS kecamatan_master_clean,
    {_bersih('kelurahan')}    AS kelurahan_master_clean,
    CAST(tanggal_lahir AS DATE) AS tanggal_lahir_master_clean,
    {_bersih('jenis_kelamin')} AS jenis_kelamin_master_clean,
    {_bersih('nama_ibu')}      AS nama_ibu_master_clean,
    CASE
        {_sql_daftar('status_kematian', HIDUP, 'h')}
        {_sql_daftar('status_kematian', MATI, 'm')}
        ELSE NULL END AS status_hidup_master_clean
"""


# ── Skor & klasifikasi ─────────────────────────────────────────────────────
# Bobot disalin dari ScoringService.compute_similarity_score().

BOBOT = {
    1: [("nama", 1.0)],
    2: [("nama", 0.8), ("tempat_lahir", 0.1), ("nama_ibu", 0.1)],
    3: [("nama", 0.6), ("tempat_lahir", 0.2), ("tanggal_lahir", 0.2)],
    4: [("nama", 0.6), ("tanggal_lahir", 0.3), ("tempat_lahir", 0.05), ("nama_ibu", 0.05)],
    # grade 5 ditangani khusus: ada komponen rata-rata wilayah
}

WILAYAH = ["provinsi", "kabupaten", "kecamatan", "kelurahan"]


def _pasangan(field: str) -> tuple[str, str]:
    """Nama kolom incoming & master untuk satu field, sesuai keluaran matching_query."""
    kiri = f"{field}_clean"
    kanan = "nama_master_clean" if field == "nama" else f"{field}_master_clean"
    return kiri, kanan


def sql_skor(grade: int) -> str:
    """Ekspresi SQL yang menghasilkan skor 0-100."""
    if grade == 5:
        cabang = "\n                ".join(
            f"CASE WHEN nullif({w}_clean, '') IS NOT NULL "
            f"AND nullif({w}_master_clean, '') IS NOT NULL "
            f"THEN j({w}_clean, {w}_master_clean) END,"
            for w in WILAYAH
        ).rstrip(",")
        return f"""(
              j(nama_clean, nama_master_clean) * 0.5
            + COALESCE(list_avg(list_filter([
                {cabang}
              ], x -> x IS NOT NULL)), 0.0) * 0.3
            + j(nama_ibu_clean, nama_ibu_master_clean) * 0.1
            + j(CAST(tanggal_lahir_clean AS VARCHAR),
                CAST(tanggal_lahir_master_clean AS VARCHAR)) * 0.1
        ) * 100"""

    suku = []
    for field, bobot in BOBOT[grade]:
        kiri, kanan = _pasangan(field)
        if field == "tanggal_lahir":
            kiri = f"CAST({kiri} AS VARCHAR)"
            kanan = f"CAST({kanan} AS VARCHAR)"
        suku.append(f"j({kiri}, {kanan}) * {bobot}")
    return "(" + " + ".join(suku) + ") * 100"


# count_missing_attributes(): grade 1 & 3 selalu 0.
MISSING = {
    1: [],
    2: ["nama", "tempat_lahir", "nama_ibu"],
    3: [],
    4: ["tanggal_lahir", "tempat_lahir", "nama_ibu"],
    5: ["nama", "tanggal_lahir", "__wilayah__", "nama_ibu"],
}


def sql_missing(grade: int) -> str:
    """Jumlah atribut yang kosong di sisi incoming."""
    field = MISSING[grade]
    if not field:
        return "0"

    suku = []
    for f in field:
        if f == "__wilayah__":
            # Dihitung kosong hanya kalau KEEMPAT kolom wilayah kosong.
            isi = " OR ".join(f"nullif({w}_clean, '') IS NOT NULL" for w in WILAYAH)
            suku.append(f"CASE WHEN {isi} THEN 0 ELSE 1 END")
        else:
            kol = f"{f}_clean"
            suku.append(
                f"CASE WHEN nullif(CAST({kol} AS VARCHAR), '') IS NOT NULL THEN 0 ELSE 1 END"
            )
    return " + ".join(suku)


SQL_KLASIFIKASI = """
    CASE
        WHEN (r.auto_missing_max IS NULL OR s.missing_count <= r.auto_missing_max)
             AND s.skor >= r.auto_score_min
            THEN 1
        WHEN (r.review_missing_count IS NULL OR s.missing_count = r.review_missing_count)
             AND s.skor >= r.review_score_min AND s.skor < r.review_score_max
            THEN 2
        ELSE 3
    END
"""


def ambil(data, *kunci, wajib: bool = True):
    """Baca field dari objek Data Langflow maupun dict biasa."""
    isi = getattr(data, "data", data)
    if not isinstance(isi, dict):
        raise TypeError(f"Input node tidak dikenali: {type(data)!r}")
    for k in kunci:
        if k in isi:
            return isi[k]
    if wajib:
        raise KeyError(f"Field {kunci!r} tidak ada. Tersedia: {sorted(isi)}")
    return None


# ── Import Langflow, dengan stub supaya run_local.py jalan tanpa Langflow ───

try:
    from langflow.custom import Component  # type: ignore
    from langflow.io import (  # type: ignore
        BoolInput,
        HandleInput,
        IntInput,
        MessageTextInput,
        Output,
    )
    from langflow.schema import Data  # type: ignore
    from langflow.schema.message import Message  # type: ignore

    LANGFLOW_TERSEDIA = True

except ImportError:  # pragma: no cover
    LANGFLOW_TERSEDIA = False

    class Component:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Data:  # type: ignore
        def __init__(self, data=None, **kwargs):
            self.data = data or kwargs

        def __repr__(self):
            return "Data(" + ", ".join(
                f"{k}=<{type(v).__name__}>" if not isinstance(v, (str, int, float, bool, type(None)))
                else f"{k}={v!r}"
                for k, v in self.data.items()
            ) + ")"

    class Message:  # type: ignore
        def __init__(self, text=""):
            self.text = text

        def __repr__(self):
            return f"Message({self.text!r})"

    def _stub(**kwargs):
        return kwargs

    MessageTextInput = HandleInput = IntInput = BoolInput = Output = _stub  # type: ignore
