"""
Jalur B untuk `.mdf` — melampirkan berkas basis data SQL Server sekali pakai.

    .mdf (+ .ldf)  ->  database job_<id>  ->  parquet kolom kontrak  ->  dilepas

BEDANYA DENGAN `.sql`

Dump `.sql` adalah teks berisi perintah; ia DIJALANKAN. Berkas `.mdf` bukan
perintah melainkan basis data itu sendiri; ia DILAMPIRKAN. Karena tidak ada
pernyataan yang dieksekusi, permukaan serangannya jauh lebih kecil — tapi tidak
nol: berkas yang dilampirkan bisa memuat trigger, prosedur, dan CLR assembly
yang aktif begitu datanya dibaca. Karena itu ia tetap berjalan di container yang
sama sekali tidak punya akses keluar, dan database-nya tetap dibuang.

`.ldf` — KADANG WAJIB, DAN YANG MENENTUKAN TIDAK KELIHATAN

`.ldf` adalah log transaksi. Diuji dengan SQL Server 2022, izin berkas dibuat
identik supaya yang membedakan hanya keadaan database-nya:

    database di-detach baik-baik, tanpa .ldf   -> BERHASIL (log dibangun ulang)
    proses SQL Server dibunuh,   tanpa .ldf   -> GAGAL
    proses dibunuh, berkas sama, DENGAN .ldf  -> BERHASIL

Pesan SQL Server pada kasus kedua menerangkan sendiri sebabnya: *the log cannot
be rebuilt because there were open transactions/users when the database was
shutdown*. Log itu menyimpan perubahan yang belum tercermin di `.mdf`.

Yang penting untuk sisi portal: **pengirim tidak punya cara tahu ia di keadaan
yang mana.** Orang yang menyalin `.mdf` dari server yang sedang berjalan akan
yakin berkasnya baik-baik saja. Karena itu `.ldf` diterima sebagai pendamping
OPSIONAL, dan kalau ia tidak ada kita mencoba membangun ulang lognya lebih dulu
sebelum menyerah.

`.ndf` (berkas data sekunder) tidak bisa dibangun ulang sama sekali — kalau
database-nya punya, berkas itu wajib ikut.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import duckdb

from _umum import KONTRAK, pilih_dari_kandidat, sql_kontrak

PG_SA = os.getenv("MSSQL_SA_USER", "sa")
SANDI = os.getenv("MSSQL_SA_PASSWORD", "")
SQLCMD = "/opt/mssql-tools18/bin/sqlcmd"
RUMAH_DUCKDB = os.getenv("KONV_DUCKDB_HOME", "/var/opt/mssql")

# Tempat berkas .mdf disalin sebelum dilampirkan. HARUS di dalam folder yang
# bisa ditulis proses SQL Server — ia yang membuka berkasnya, bukan kita.
KERJA = os.getenv("KONV_MSSQL_KERJA", "/var/opt/mssql/data/uji")


def _sqlcmd(sql: str, db: str = "master", batas: int = 300) -> str:
    hasil = subprocess.run(
        [SQLCMD, "-S", "localhost", "-U", PG_SA, "-P", SANDI, "-C", "-b",
         "-d", db, "-h", "-1", "-W", "-Q", sql],
        capture_output=True, text=True, timeout=batas, check=False)
    if hasil.returncode:
        keluaran = (hasil.stderr or hasil.stdout).strip()
        raise RuntimeError(keluaran.splitlines()[0][:400] if keluaran
                           else f"sqlcmd keluar dengan kode {hasil.returncode}")
    return hasil.stdout.strip()


def _lampirkan(db: str, mdf: str, ldf: str | None) -> str:
    """
    Lampirkan berkas. Kembalikan keterangan cara yang berhasil.

    Urutannya disengaja: kalau `.ldf` ada, `FOR ATTACH` biasa dicoba lebih dulu
    karena ia memulihkan database ke keadaan yang konsisten. `ATTACH_REBUILD_LOG`
    MEMBUANG isi log, jadi ia hanya benar kalau memang tidak ada yang tersisa di
    sana — dan SQL Server sendiri yang menolak kalau ternyata ada.
    """
    if ldf:
        try:
            _sqlcmd(f"CREATE DATABASE [{db}] ON (FILENAME = '{mdf}'), "
                    f"(FILENAME = '{ldf}') FOR ATTACH;")
            return "FOR ATTACH dengan .ldf"
        except RuntimeError as e:
            print(f"[K] attach dengan .ldf gagal ({e}); mencoba rebuild log",
                  flush=True)

    try:
        _sqlcmd(f"CREATE DATABASE [{db}] ON (FILENAME = '{mdf}') "
                f"FOR ATTACH_REBUILD_LOG;")
        return "FOR ATTACH_REBUILD_LOG (tanpa .ldf)"
    except RuntimeError as e:
        pesan = str(e)
        if "log cannot be rebuilt" in pesan.lower() or "1813" in pesan:
            raise RuntimeError(
                "Berkas .mdf ini diambil saat basis datanya masih berjalan, "
                "sehingga log transaksinya memuat perubahan yang belum tersimpan "
                "di .mdf. Tanpa berkas .ldf-nya, database tidak bisa dipulihkan "
                "ke keadaan yang konsisten — dan itu tidak bisa diperbaiki dari "
                "sisi kami. Mintakan berkas .ldf-nya juga, atau minta pengirim "
                "men-detach database-nya baik-baik lebih dulu lalu mengirim "
                "ulang. Pesan asli SQL Server: " + pesan[:200]) from e
        raise


def _koneksi(db: str) -> tuple[duckdb.DuckDBPyConnection, int]:
    """
    DuckDB + pegangan ODBC ke database yang baru dilampirkan.

    DuckDB tidak punya extension SQL Server seperti `postgres` — sudah
    diperiksa, `sqlserver`/`mssql`/`tds` tidak ada. ODBC-lah jalannya, dan
    driver `ODBC Driver 18 for SQL Server` memang sudah ada di image ini.

    Alternatifnya mengekspor lewat `bcp` ke TSV, dan itu memaksa membuang
    karakter kendali dari data supaya pemisahnya tidak rusak — artinya mengubah
    data demi format perantara. Lewat ODBC nilainya berpindah apa adanya.
    """
    con = duckdb.connect()
    con.execute(f"SET home_directory='{RUMAH_DUCKDB}'")
    con.execute("LOAD odbc")
    dsn = (f"Driver={{ODBC Driver 18 for SQL Server}};Server=localhost;"
           f"UID={PG_SA};PWD={SANDI};TrustServerCertificate=yes;Database={db}")
    pegangan = con.execute(
        f"SELECT odbc_connect('{dsn}')").fetchone()[0]
    return con, pegangan


def _tanya(con, pegangan: int, sql: str):
    return con.execute(
        f"SELECT * FROM odbc_query({pegangan}, '{sql}')").fetchall()


def konversi(jalur_mdf: str, job_id: str, tujuan: str,
             jalur_ldf: str | None = None, tabel: str | None = None,
             lapor=lambda t: None) -> dict:
    """Satu job utuh. Database dilepas dan berkasnya dihapus apa pun yang terjadi."""
    db = "job_" + re.sub(r"[^a-z0-9_]", "_", job_id.lower())[:48]
    mulai = time.perf_counter()

    os.makedirs(KERJA, exist_ok=True)
    mdf = os.path.join(KERJA, f"{db}.mdf")
    ldf = os.path.join(KERJA, f"{db}.ldf") if jalur_ldf else None
    os.replace(jalur_mdf, mdf)
    if jalur_ldf:
        os.replace(jalur_ldf, ldf)

    lapor("K2 lampirkan berkas basis data")
    cara = _lampirkan(db, mdf, ldf)
    print(f"[K] {job_id} dilampirkan: {cara}", flush=True)

    con = pegangan = None
    try:
        lapor("K3 pilih tabel & ekspor parquet")
        con, pegangan = _koneksi(db)

        kandidat = {}
        for skema, nama in _tanya(con, pegangan,
                                  "SELECT TABLE_SCHEMA, TABLE_NAME FROM "
                                  "INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = "
                                  "''BASE TABLE''"):
            kolom = [r[0] for r in _tanya(
                con, pegangan,
                f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA = ''{skema}'' AND TABLE_NAME = ''{nama}''")]
            baris = _tanya(con, pegangan,
                           f"SELECT COUNT(*) FROM [{skema}].[{nama}]")[0][0]
            kandidat[f"{skema}.{nama}"] = {"kolom": kolom, "baris": int(baris)}

        nama_tabel, alasan = pilih_dari_kandidat(kandidat, tabel)
        skema, polos = nama_tabel.split(".", 1)

        pilih = sql_kontrak(alasan["peta"], kutip='[]', cast="NVARCHAR(4000)")
        q = f"SELECT {pilih} FROM [{skema}].[{polos}]".replace("'", "''")
        con.execute(f"""COPY (SELECT * FROM odbc_query({pegangan}, '{q}'))
                        TO '{tujuan}' (FORMAT parquet)""")
        baris = con.execute(
            f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        # Dilepas, bukan DROP: DROP DATABASE pada database yang dilampirkan akan
        # ikut MENGHAPUS berkas .mdf-nya, dan berkas itu milik pengirim.
        # Berkas kerjanya memang salinan, tapi kebiasaan menghapus berkas basis
        # data lewat perintah SQL adalah kebiasaan yang salah untuk dipelihara.
        try:
            _sqlcmd(f"ALTER DATABASE [{db}] SET OFFLINE WITH ROLLBACK IMMEDIATE; "
                    f"EXEC sp_detach_db '{db}';")
        except Exception as e:  # noqa: BLE001
            print(f"[K] PERINGATAN: database {db} gagal dilepas: {e}", flush=True)
        for p in (mdf, ldf):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    return {
        "tabel": nama_tabel,
        "alasan_tabel": {k: v for k, v in alasan.items() if k != "peta"},
        "dialek": "sqlserver",
        "cara_lampir": cara,
        "row_count": baris,
        "durasi_detik": round(time.perf_counter() - mulai, 1),
        "kolom_kontrak": KONTRAK,
    }
