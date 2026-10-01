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

from _nama import sql_bersih_pilih

load_dotenv()

# ── Konfigurasi koneksi ────────────────────────────────────────────────────

PG_DSN = os.getenv("PG_DSN", "host=127.0.0.1 port=5432 dbname=synchrono user=postgres")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "localhost:8333")
S3_KEY = os.getenv("S3_ACCESS_KEY", "synchrono")
S3_SECRET = os.getenv("S3_SECRET_KEY", "synchrono123")
S3_USE_SSL = os.getenv("S3_USE_SSL", "false").lower() == "true"

# Lihat catatan panjang di buka_koneksi(). Kosong = bawaan DuckDB.
DUCKDB_MEMORY_LIMIT = os.getenv("DUCKDB_MEMORY_LIMIT", "").strip()
DUCKDB_TEMP_DIR = os.getenv("DUCKDB_TEMP_DIR", "").strip()

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

    # BATAS MEMORI DUCKDB — di container, ini menentukan hidup atau mati.
    #
    # Bawaan DuckDB adalah 80% RAM YANG IA LIHAT, dan di dalam container ia
    # melihat RAM MESIN, bukan batas cgroup-nya. Jadi ia dengan tenang
    # mengalokasi melewati batas container, lalu kernel membunuhnya —
    # exit code 137, tanpa satu pun pesan galat dari DuckDB.
    #
    # Diukur pada container berbatas 5,79 GB: 3 juta baris x 35 kolom selesai
    # memakai 3,96 GB, sementara 5 juta baris mati OOM. Dengan batas disetel,
    # DuckDB TIDAK mati — ia menumpahkan ke disk dan tetap menyelesaikan
    # pekerjaannya, hanya lebih lambat. Gagal lambat jauh lebih baik daripada
    # gagal dibunuh.
    #
    # Setel sekitar 60-70% batas memori container. Kosong = pakai bawaan DuckDB
    # (berisiko di container).
    if DUCKDB_MEMORY_LIMIT:
        con.execute(f"SET memory_limit = '{DUCKDB_MEMORY_LIMIT}'")
    # Tempat tumpahan. Harus folder yang bisa ditulis DAN punya ruang lega —
    # tumpahan bisa berkali-kali lipat ukuran berkas masukan.
    if DUCKDB_TEMP_DIR:
        con.execute(f"SET temp_directory = '{DUCKDB_TEMP_DIR}'")

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


# Nama kolom di enriched parquet -> nama baku yang dipakai SQL matching.
#
# Berkas enriched ditulis dengan nama sesuai spesifikasi integrasi bagian 4.1
# (`nama_lengkap`, `nama_ibu_kandung`), sementara seluruh query matching di
# tabel `matching_queries` merujuk `nama` dan `nama_ibu`. Pemetaan ini yang
# menyambungkan keduanya, jadi berkas enriched tetap bisa dipakai matching
# tanpa satu pun query perlu diubah.
ALIAS_ENRICHED = {
    "nama": ("nama", "nama_lengkap"),
    "nama_ibu": ("nama_ibu", "nama_ibu_kandung"),
}


def _sumber_kolom(baku: str, kolom_ada: set[str]) -> str | None:
    """Nama kolom yang sebenarnya ada di berkas untuk satu elemen baku."""
    for kandidat in ALIAS_ENRICHED.get(baku, (baku,)):
        if kandidat in kolom_ada:
            return kandidat
    return None


def _bersih_nama(kolom: str, bersih: dict | None) -> str:
    """
    Nama (dan nama ibu) untuk dibandingkan.

    Tanpa pembersihan yang dinyalakan, ekspresinya PERSIS `_bersih` — lower dan
    trim saja, seperti sistem lama. Sakelarnya diatur per grade lewat
    konfigurasi (`nameCleaning`), dan berlaku di KEDUA sisi: pembersihan yang
    hanya diterapkan pada incoming justru menjauhkannya dari master yang juga
    memuat gelar.
    """
    b = bersih or {}
    if not (b.get("titles") or b.get("patronym") or b.get("abbreviations")):
        return _bersih(kolom)
    return sql_bersih_pilih(kolom, gelar=bool(b.get("titles")),
                            patronimik=bool(b.get("patronym")),
                            singkatan=bool(b.get("abbreviations")))


def sql_view_incoming(kolom_ada: set[str], bersih: dict | None = None) -> str:
    """
    Bangun SELECT normalisasi untuk parquet incoming.

    Kolom yang tidak ada di file dilewati — sama seperti versi Polars yang
    memakai `if "nama" in df.columns`. File grade 1/2 tidak punya tanggal_lahir,
    dan grade 6 bisa saja tidak memasangkan kolom apa pun.

    `bersih` = sakelar pembersihan nama milik grade berkas (lihat `_bersih_nama`).
    """
    pilih = []

    if "id" in kolom_ada:
        pilih.append("trim(CAST(id AS VARCHAR)) AS id")
    if "nik" in kolom_ada:
        pilih.append("trim(CAST(nik AS VARCHAR)) AS nik")
    else:
        # Grade C, D, dan E memang TIDAK BOLEH punya kolom NIK — kriteria
        # grade-nya menyebutnya `terlarang`. Kolomnya tetap dipancarkan sebagai
        # NULL supaya query yang merujuknya tidak gagal mengikat.
        pilih.append("CAST(NULL AS VARCHAR) AS nik")

    # SELURUH kolom baku selalu dipancarkan — NULL kalau berkasnya tidak
    # memuatnya.
    #
    # Sebelumnya kolom yang absen dilewati begitu saja, dan akibatnya query
    # matching yang merujuknya gagal MENGIKAT, bukan menghasilkan nilai kosong:
    # BinderException, seluruh grade mati. Terukur pada berkas uji grade E yang
    # tidak memuat status_hidup maupun kolom wilayah — grade 5 tidak bisa
    # dijalankan sama sekali.
    #
    # Memancarkannya sebagai NULL aman dan justru lebih tepat: `sql_missing`
    # menghitung NULL sebagai atribut kosong, dan kolom yang memang tidak ada
    # di berkas memang atribut yang kosong. Bagi berkas yang MEMUAT kolomnya,
    # tidak ada satu pun yang berubah.
    for kol in ("nama", "tempat_lahir", "provinsi", "kabupaten",
                "kecamatan", "kelurahan", "nama_ibu"):
        sumber = _sumber_kolom(kol, kolom_ada)
        if sumber:
            # Di-alias ke nama baku, supaya query matching tidak perlu tahu
            # berkasnya memakai nama spesifikasi atau nama baku.
            pilih.append(f"{sumber} AS {kol}")
            bersihkan = (_bersih_nama(sumber, bersih) if kol in ("nama", "nama_ibu")
                         else _bersih(sumber))
            pilih.append(f"{bersihkan} AS {kol}_clean")
        else:
            pilih.append(f"CAST(NULL AS VARCHAR) AS {kol}")
            pilih.append(f"CAST(NULL AS VARCHAR) AS {kol}_clean")

    if "tanggal_lahir" in kolom_ada:
        pilih.append("tanggal_lahir")
        pilih.append(f"{_sql_tanggal('tanggal_lahir')} AS tanggal_lahir_clean")
    else:
        pilih.append("CAST(NULL AS VARCHAR) AS tanggal_lahir")
        pilih.append("CAST(NULL AS TIMESTAMP) AS tanggal_lahir_clean")

    if "jenis_kelamin" in kolom_ada:
        pilih.append("jenis_kelamin")
        pilih.append(f"""CASE
            {_sql_daftar('jenis_kelamin', GENDER_L, 'l')}
            {_sql_daftar('jenis_kelamin', GENDER_P, 'p')}
            ELSE NULL END AS jenis_kelamin_clean""")
    else:
        pilih.append("CAST(NULL AS VARCHAR) AS jenis_kelamin")
        pilih.append("CAST(NULL AS VARCHAR) AS jenis_kelamin_clean")

    # status_hidup_clean SELALU ada, NULL kalau berkasnya tidak punya kolomnya.
    #
    # Blocking grade 5 merujuk kolom ini, sedangkan berkas grade E tidak
    # memuatnya — kriteria grading grade E memang tidak menjanjikannya. Dulu
    # akibatnya BinderException dan grade 5 tidak bisa dijalankan sama sekali.
    # Dengan kolomnya selalu ada, query-nya bisa memilih melewati syarat itu
    # saat nilainya NULL, alih-alih gagal mengikat.
    if "status_hidup" in kolom_ada:
        pilih.append(f"""CASE
            {_sql_daftar('status_hidup', HIDUP, 'h')}
            {_sql_daftar('status_hidup', MATI, 'm')}
            ELSE NULL END AS status_hidup_clean""")
    else:
        pilih.append("CAST(NULL AS VARCHAR) AS status_hidup_clean")

    # nik_trusted dipakai blocking grade 1 & 2 untuk menolak NIK yang sudah
    # dinyatakan tidak tepercaya oleh grading.
    #
    # Kalau berkasnya tidak memuat kolom ini, nilainya TRUE — bukan FALSE.
    # Alasannya: penjaga itu hanya boleh bekerja saat grading BENAR-BENAR
    # sudah memberi vonis. Menganggap "tidak ada kabar" sebagai "tidak
    # tepercaya" akan membuat seluruh berkas lama gagal mencocokkan apa pun,
    # diam-diam dan tanpa satu pun galat.
    if "nik_trusted" in kolom_ada:
        pilih.append("CAST(nik_trusted AS BOOLEAN) AS nik_trusted")
    else:
        pilih.append("TRUE AS nik_trusted")

    return ",\n        ".join(pilih)


def sql_view_master(bersih: dict | None = None) -> str:
    """Kolom view master; `bersih` = sakelar pembersihan nama, sama dengan incoming."""
    return f"""
    nik,
    nama_lengkap,
    tempat_lahir,
    tanggal_lahir,
    jenis_kelamin,
    nama_ibu,
    {_bersih_nama('nama_lengkap', bersih)} AS nama_master_clean,
    {_bersih('tempat_lahir')} AS tempat_lahir_master_clean,
    {_bersih('provinsi')}     AS provinsi_master_clean,
    {_bersih('kabupaten')}    AS kabupaten_master_clean,
    {_bersih('kecamatan')}    AS kecamatan_master_clean,
    {_bersih('kelurahan')}    AS kelurahan_master_clean,
    CAST(tanggal_lahir AS DATE) AS tanggal_lahir_master_clean,
    -- DINORMALISASI PERSIS SEPERTI INCOMING ('l'/'p'), bukan sekadar lower():
    -- master berisi 'LAKI-LAKI'/'PEREMPUAN' sementara incoming 'L'/'P' menjadi
    -- 'l' <> 'laki-laki' — Pass 1/2 menganggap SEMUA pasangan bertentangan, dan
    -- blocking grade C/D yang menyambung lewat jenis kelamin tidak menemukan
    -- satu kandidat pun (terukur 28 Sep 2026 pada master server 2 juta baris).
    CASE
        {_sql_daftar('jenis_kelamin', GENDER_L, 'l')}
        {_sql_daftar('jenis_kelamin', GENDER_P, 'p')}
        ELSE NULL END AS jenis_kelamin_master_clean,
    {_bersih_nama('nama_ibu', bersih)}      AS nama_ibu_master_clean,
    CASE
        {_sql_daftar('status_kematian', HIDUP, 'h')}
        {_sql_daftar('status_kematian', MATI, 'm')}
        ELSE NULL END AS status_hidup_master_clean
"""


# Tanpa pembersihan nama — dipakai matching lama (n1..n7) dan sebagai bawaan.
SQL_VIEW_MASTER = sql_view_master()


# ── Skor & klasifikasi ─────────────────────────────────────────────────────
#
# Bobot, elemen yang dihitung "kosong", dan pembersihan nama adalah
# KONFIGURASI per grade — kolom `bobot`, `elemen_kosong`, dan `bersih_nama` di
# tabel `grade_rules`, diubah lewat API config (lib/_config.py). Nilai di
# bawah ini hanya BAWAAN: disalin dari ScoringService.compute_similarity_score()
# sistem lama, dipakai kalau kolomnya kosong dan oleh matching lama (n1..n7).
#
# Bobot ditulis dalam PERSEN dan BERURUTAN. Urutannya bukan kosmetik: skor
# adalah jumlah pecahan biner, dan 0.6 + 0.3 tidak persis 0.9. Urutan suku yang
# berbeda bisa menggeser skor di digit terakhir, tepat di ambang — grade D
# "nama & tanggal persis, tempat & ibu kosong" bernilai 89,999…, jadi REVIEW,
# bukan 90 dan AUTO. Urutan bawaan = urutan rumus lama, jadi hasilnya identik.

# Elemen yang boleh diberi bobot / dihitung kosong. NIK sengaja tidak ada: NIK
# dipakai untuk blocking dan Pass 1 (cocok persis), bukan untuk skor kemiripan.
ELEMEN_SKOR = ["nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin",
               "nama_ibu", "wilayah"]

BOBOT_BAWAAN = {
    1: [("nama", 100)],
    2: [("nama", 80), ("tempat_lahir", 10), ("nama_ibu", 10)],
    3: [("nama", 60), ("tempat_lahir", 20), ("tanggal_lahir", 20)],
    4: [("nama", 60), ("tanggal_lahir", 30), ("tempat_lahir", 5), ("nama_ibu", 5)],
    5: [("nama", 50), ("wilayah", 30), ("nama_ibu", 10), ("tanggal_lahir", 10)],
}

# Bentuk lama (pecahan, grade 1-4) — untuk alat yang menghitung ulang skor di
# Python (beban/uji_hibrida.py).
BOBOT = {g: [(f, b / 100) for f, b in isi]
         for g, isi in BOBOT_BAWAAN.items() if g != 5}

WILAYAH = ["provinsi", "kabupaten", "kecamatan", "kelurahan"]

# count_missing_attributes(): grade 1 & 3 selalu 0. `wilayah` dihitung kosong
# hanya kalau KEEMPAT tingkatnya kosong.
MISSING = {
    1: [],
    2: ["nama", "tempat_lahir", "nama_ibu"],
    3: [],
    4: ["tanggal_lahir", "tempat_lahir", "nama_ibu"],
    5: ["nama", "tanggal_lahir", "wilayah", "nama_ibu"],
}

# Pembersihan nama sebelum dibandingkan — mati semua, seperti sistem lama.
BERSIH_NAMA_BAWAAN = {"titles": False, "patronym": False, "abbreviations": False}


def _pasangan(field: str) -> tuple[str, str]:
    """Nama kolom incoming & master untuk satu field, sesuai keluaran matching_query."""
    kiri = f"{field}_clean"
    kanan = "nama_master_clean" if field == "nama" else f"{field}_master_clean"
    return kiri, kanan


# Cara menilai tanggal lahir di skor Pass 3 (`matching.dateMatch` per grade).
#
#   similarity  Jaro-Winkler atas teks yyyy-mm-dd — engine lama, bawaan. Beda
#               1 hari, hari/bulan tertukar, maupun beda TAHUN tetap bernilai
#               ±0,9. Karena blocking C/D/E sudah mensyaratkan hari & bulan
#               sama, orang LAIN bernama sama yang lahir di tahun berbeda nyaris
#               tidak dihukum dan lolos AUTO (terukur dengan data uji ber-kunci
#               jawaban, test-data-csv/uji-master-ae).
#   exact       1 kalau tanggalnya sama persis, selain itu 0 — seperti V1 engine
#               lama untuk grade D/E. Tanggal bukan teks: 12 dan 13 Maret bukan
#               "hampir sama", melainkan dua hari yang berbeda.
COCOK_TANGGAL = ("similarity", "exact")

# Kandidat CONFLICT yang disimpan dan disebut di reasoning, per baris. Jumlah
# seluruhnya tetap dihitung dan disebut; hanya rinciannya yang dibatasi.
MAKS_KANDIDAT = 5


def _suku_skor(field: str, cocok_tanggal: str = "similarity") -> str:
    """Kemiripan satu elemen, 0-1. Sisi yang kosong bernilai 0 (macro `j`)."""
    if field == "wilayah":
        # Rata-rata tingkat yang terisi DI KEDUA SISI; tidak ada satu pun -> 0.
        cabang = ", ".join(
            f"CASE WHEN nullif({w}_clean, '') IS NOT NULL "
            f"AND nullif({w}_master_clean, '') IS NOT NULL "
            f"THEN j({w}_clean, {w}_master_clean) END"
            for w in WILAYAH
        )
        return (f"COALESCE(list_avg(list_filter([{cabang}], "
                f"x -> x IS NOT NULL)), 0.0)")
    kiri, kanan = _pasangan(field)
    if field == "tanggal_lahir":
        if cocok_tanggal == "exact":
            # Kosong di salah satu sisi -> perbandingan NULL -> 0, sama seperti `j`.
            return (f"CAST(CASE WHEN CAST({kiri} AS DATE) = CAST({kanan} AS DATE) "
                    f"THEN 1.0 ELSE 0.0 END AS DOUBLE)")
        kiri = f"CAST({kiri} AS VARCHAR)"
        kanan = f"CAST({kanan} AS VARCHAR)"
    return f"j({kiri}, {kanan})"


def kolom_elemen(elemen: str, sisi: str) -> list[str]:
    """Kolom `joined_df` yang dibaca rumus skor untuk satu elemen. sisi: 'i' / 'm'."""
    if elemen == "wilayah":
        return [f"{w}_clean" if sisi == "i" else f"{w}_master_clean" for w in WILAYAH]
    kiri, kanan = _pasangan(elemen)
    return [kiri if sisi == "i" else kanan]


def kolom_kurang(kolom_ada, bobot, elemen_kosong) -> dict[str, list[str]]:
    """
    Elemen yang kolomnya TIDAK dikeluarkan kueri blocking -> kolom yang kurang.

    Skor Pass 3 dihitung dari keluaran kueri blocking, bukan dari tabel asal —
    elemen berbobot butuh kolom kedua sisi, elemen "kosong" hanya sisi incoming.
    `bobot` = [(elemen, persen), ...]; elemen berbobot 0 tidak dibutuhkan.
    """
    kurang: dict[str, list[str]] = {}
    for elemen, persen in bobot or []:
        if persen:
            k = [c for c in kolom_elemen(elemen, "i") + kolom_elemen(elemen, "m")
                 if c not in kolom_ada]
            if k:
                kurang[elemen] = k
    for elemen in elemen_kosong or []:
        k = [c for c in kolom_elemen(elemen, "i") if c not in kolom_ada]
        if k:
            kurang.setdefault(elemen, k)
    return kurang


def _pecahan(persen) -> str:
    """60 -> '0.6': literal yang sama persis dengan yang tertulis di rumus lama."""
    return repr(float(persen) / 100)


def sql_skor(grade: int, bobot: list | None = None,
             cocok_tanggal: str = "similarity") -> str:
    """
    Ekspresi SQL skor 0-100: jumlah kemiripan elemen x bobotnya.

    `bobot` = [(elemen, persen), ...] dari konfigurasi grade; None = bawaan.
    Elemen berbobot 0 dilewati. `cocok_tanggal` lihat COCOK_TANGGAL.
    """
    isi = BOBOT_BAWAAN[grade] if bobot is None else bobot
    suku = [f"{_suku_skor(f, cocok_tanggal)} * {_pecahan(b)}" for f, b in isi if b]
    if not suku:
        return "0.0"
    return "(" + " + ".join(suku) + ") * 100"


def sql_missing(grade: int, elemen: list | None = None) -> str:
    """
    Jumlah elemen yang kosong di sisi incoming.

    `elemen` = daftar dari konfigurasi grade; None = bawaan.
    """
    field = MISSING[grade] if elemen is None else elemen
    if not field:
        return "0"

    suku = []
    for f in field:
        if f == "wilayah":
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
