"""
Jalur B untuk `.sql` berdialek MySQL/MariaDB — dump dijalankan di MariaDB sekali pakai.

    dump .sql  ->  database job_<id>  ->  parquet kolom kontrak  ->  database dibuang

Polanya sama dengan `_pulih.py` (PostgreSQL): satu server berumur panjang, tiap
job mendapat DATABASE baru yang dibuang setelah selesai, dan yang keluar hanya
parquet berisi enam kolom kontrak.

KENAPA MARIADB, UNTUK DUMP MYSQL SEKALIPUN

Satu mesin untuk dua keluarga dump. MariaDB 11.8 membaca dump mysqldump 5.7/8.x
maupun mariadb-dump; arah sebaliknya lebih rapuh — dump MariaDB 11 memakai
kolasi `utf8mb4_uca1400_ai_ci` yang tidak dikenal MySQL. Dan kliennya punya
`--sandbox`, lapisan yang tidak dimiliki klien MySQL (lihat di bawah).

EMPAT LAPISAN — SEMUANYA MEMBUAT SERANGAN MUSTAHIL, BUKAN MENDETEKSINYA

1. Pengguna basis data per job, hak HANYA pada database job itu. Tanpa FILE
   (`LOAD DATA INFILE`, `SELECT ... INTO OUTFILE`), tanpa SUPER (`SET GLOBAL`,
   binlog), tanpa CREATE USER/GRANT. Padanan peran `pemulih` di PostgreSQL.

2. Perintah klien dimatikan. Klien `mariadb` punya perintah yang dijalankannya
   SENDIRI, di container ini, bukan di server: `\\! perintah` (shell),
   `source berkas`, `tee`, `pager`. Pelajaran dari jalur PostgreSQL: di sana
   `\\!` di dalam dump sempat berjalan sebagai root. Di sini ditutup dua kali —
   `--binary-mode` membuat klien berhenti mengurai perintahnya sendiri (kecuali
   DELIMITER, yang dibutuhkan dump berisi trigger/prosedur), dan `--sandbox`
   menolak yang menyentuh berkas kalaupun lolos.

3. `LOCAL INFILE` mati di KEDUA sisi. `LOAD DATA LOCAL INFILE '/proc/self/environ'`
   membuat KLIEN membaca berkas lokal lalu mengirimkannya ke server sebagai isi
   tabel — dan tabel itulah yang kita ekspor. Tanpa ini, kredensial container
   bisa keluar sebagai "data kependudukan".

4. Klien dijalankan dengan lingkungan KOSONG kecuali PATH dan sandi job. Sandi
   root dan kredensial S3 tidak pernah ada di proses yang membaca dump.

YANG DIBUANG DARI DUMP SEBELUM DIJALANKAN

Pernyataan yang menuntut hak lebih tinggi daripada yang sengaja diberikan, dan
tidak ada gunanya untuk membaca enam kolom — padanan `OWNER TO` di PostgreSQL:

    CREATE/DROP DATABASE, USE      semuanya masuk ke database job
    DEFINER=`x`@`y`                pemiliknya jadi pengguna job
    SET @@SESSION.SQL_LOG_BIN      butuh SUPER; binlog memang mati
    SET @@GLOBAL.GTID_PURGED       butuh SUPER; bisa berbaris-baris
    CHANGE MASTER / REPLICATION    butuh SUPER

Membuang pernyataan tidak pernah menambah kemampuan, jadi menulis ulang masukan
yang tidak dipercaya di sini aman — arahnya hanya satu.

Dump yang memuat beberapa database (`--databases`, `--all-databases`) masuk ke
SATU database job. Tabel dipilih dari isinya seperti biasa; kalau dua database
punya tabel bernama sama, yang belakangan menang (`DROP TABLE IF EXISTS` milik
mysqldump) — sebutkan `sourceTable` kalau itu jadi soal.

Diproses sebagai BITA, bukan teks: dump MySQL lama sering latin1, dan membacanya
sebagai UTF-8 akan merusak nama berhuruf non-ASCII sebelum sampai ke server.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time

import duckdb

from _umum import KONTRAK, pilih_dari_kandidat, sql_kontrak

HOST = os.getenv("KONV_MYSQL_HOST", "127.0.0.1")
PORT = os.getenv("KONV_MYSQL_PORT", "3306")
SANDI_ROOT = os.getenv("MARIADB_ROOT_PASSWORD", "")
KLIEN = "mariadb"
# Loopback di dalam container yang sama — TLS tidak melindungi apa pun di sini,
# dan tanpa `--skip-ssl` setiap pesan galat diawali peringatan sertifikat.
KLIEN_OPSI = ("-h", HOST, "-P", PORT, "--skip-ssl")
BATAS_DETIK = int(os.getenv("KONV_BATAS_DETIK", "1800"))

# Hak pengguna job, HANYA pada `job_<id>`.*. Cukup untuk dump mysqldump biasa —
# tabel, indeks, LOCK TABLES, view, trigger, prosedur — dan tidak lebih.
HAK = ("SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, "
       "CREATE TEMPORARY TABLES, LOCK TABLES, CREATE VIEW, SHOW VIEW, TRIGGER, "
       "CREATE ROUTINE, ALTER ROUTINE, EXECUTE, EVENT")

# Baris yang dibuang utuh. Awalan `/*!40000 ` ikut dikenali: mysqldump menulis
# `/*!40000 DROP DATABASE IF EXISTS `x`*/;`.
POLA_BUANG = re.compile(
    rb"^\s*(?:/\*!\d+\s*)?(?:"
    rb"CREATE\s+(?:DATABASE|SCHEMA)\b|DROP\s+(?:DATABASE|SCHEMA)\b|USE\s"
    rb"|SET\s+@@(?:SESSION\.|GLOBAL\.)?(?:SQL_LOG_BIN|GTID_PURGED|GTID_NEXT)\b"
    rb"|CHANGE\s+(?:MASTER|REPLICATION\s+SOURCE)\s+TO\b)",
    re.I)

# `DEFINER=`root`@`localhost`` — di view, trigger, prosedur, dan event.
_NAMA = rb"(?:`[^`]*`|'[^']*'|\"[^\"]*\"|[\w.%$-]+)"
POLA_DEFINER = re.compile(rb"DEFINER\s*=\s*" + _NAMA + rb"\s*@\s*" + _NAMA, re.I)

# Baris data tidak disentuh: teks "DEFINER=..." di dalam nilai kolom bukan
# klausa, dan data tidak boleh berubah demi membersihkan DDL.
POLA_DATA = re.compile(rb"^\s*(?:INSERT|REPLACE)\s", re.I)


def _admin(sql: str, batas: int = 120) -> str:
    """Satu perintah sebagai root. Hanya untuk membuat/membuang database & pengguna."""
    hasil = subprocess.run(
        [KLIEN, *KLIEN_OPSI, "-u", "root", "--batch", "--skip-column-names",
         "-e", sql],
        capture_output=True, text=True, timeout=batas, check=False,
        env={"PATH": os.environ.get("PATH", ""), "MYSQL_PWD": SANDI_ROOT})
    if hasil.returncode:
        raise RuntimeError(f"mariadb gagal: {hasil.stderr.strip()[:400]}")
    return hasil.stdout.strip()


def siapkan_dump(asal: str, tujuan: str) -> dict:
    """Salin dump sambil membuang pernyataan di docstring modul. Kembalikan hitungannya."""
    hitung = {"baris_dibuang": 0, "definer_dibuang": 0}
    lanjut = False  # sedang di tengah pernyataan berbaris-baris yang dibuang
    with open(asal, "rb") as masuk, open(tujuan, "wb") as keluar:
        for nomor, baris in enumerate(masuk):
            if nomor == 0 and baris.startswith(b"\xef\xbb\xbf"):
                baris = baris[3:]
            if lanjut or POLA_BUANG.match(baris):
                hitung["baris_dibuang"] += 1
                # `SET @@GLOBAL.GTID_PURGED='a:1-5,\n b:1-3';` bisa berbaris-baris;
                # buang sampai titik koma penutupnya.
                lanjut = not baris.rstrip().endswith(b";")
                continue
            if not POLA_DATA.match(baris):
                baris, n = POLA_DEFINER.subn(b"", baris)
                hitung["definer_dibuang"] += n
            keluar.write(baris)
    return hitung


def _pulihkan(jalur: str, db: str, pengguna: str, sandi: str) -> None:
    """
    Jalankan dump sebagai pengguna job. Berhenti di galat pertama.

    Tidak ada padanan `--single-transaction`: DDL MySQL melakukan commit
    sendiri. Tapi tanpa `--force`, klien berhenti di galat pertama, dan
    database yang setengah jadi itu dibuang utuh sebelum sempat diekspor.
    """
    with open(jalur, "rb") as masuk:
        hasil = subprocess.run(
            [KLIEN, "--binary-mode", "--sandbox", "--local-infile=0",
             "--default-character-set=utf8mb4", "--max-allowed-packet=1G",
             *KLIEN_OPSI, "-u", pengguna, db],
            stdin=masuk, capture_output=True, timeout=BATAS_DETIK, check=False,
            env={"PATH": os.environ.get("PATH", ""), "MYSQL_PWD": sandi})
    if hasil.returncode:
        galat = (hasil.stderr or hasil.stdout).decode("utf-8", "replace").strip()
        ringkas = " | ".join(b for b in galat.splitlines()[-4:] if b.strip())[:500]
        raise RuntimeError(
            f"Pemulihan dump MySQL/MariaDB gagal. Kalau sebabnya hak akses, itu "
            f"memang disengaja: dump dijalankan sebagai pengguna tanpa hak di "
            f"luar database-nya sendiri, tanpa akses berkas, dan tanpa perintah "
            f"klien. Pesan asli: {ringkas}")


def _buka_duckdb(db: str, pengguna: str, sandi: str) -> duckdb.DuckDBPyConnection:
    """Dibaca sebagai pengguna job juga — tidak ada alasan memakai root untuk SELECT."""
    con = duckdb.connect()
    con.execute("LOAD mysql")
    con.execute(f"ATTACH 'host={HOST} port={PORT} user={pengguna} "
                f"passwd={sandi} database={db}' AS sumber (TYPE mysql, READ_ONLY)")
    return con


def _tanya(con, sql: str) -> list:
    """Kueri MariaDB asli lewat DuckDB."""
    return con.execute(
        f"SELECT * FROM mysql_query('sumber', '{sql.replace(chr(39), chr(39) * 2)}')"
    ).fetchall()


def _kutip(nama: str) -> str:
    return "`" + nama.replace("`", "``") + "`"


def pilih_tabel(con, tabel_paksa: str | None = None) -> tuple[str, dict]:
    """Sama dengan jalur lain — pemilihannya di `_umum.py`, bersama."""
    kandidat = {}
    for (t,) in _tanya(con, "SELECT TABLE_NAME FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = DATABASE() "
                            "AND TABLE_TYPE = 'BASE TABLE'"):
        literal = t.replace("\\", "\\\\").replace("'", "''")
        kandidat[t] = {
            "kolom": [r[0] for r in _tanya(
                con, f"SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                     f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{literal}' "
                     f"ORDER BY ORDINAL_POSITION")],
            "baris": int(_tanya(con, f"SELECT COUNT(*) FROM {_kutip(t)}")[0][0]),
        }
    return pilih_dari_kandidat(kandidat, tabel_paksa)


def ekspor(con, tabel: str, peta: dict, tujuan: str) -> int:
    """
    Enam kolom kontrak, seluruhnya teks — DUA KALI dipaksa jadi teks.

    Di MariaDB (`CAST ... AS CHAR`) supaya nilainya keluar dalam bentuk
    tulisannya sendiri: tanggal `0000-00-00` warisan MySQL lama tetap teks
    `0000-00-00` untuk dinilai grading, bukan galat konversi tanggal di DuckDB.
    Di DuckDB (`VARCHAR`) supaya kolom yang tidak ada di sumber tetap bertipe
    teks di parquet, bukan tipe NULL.
    """
    dalam = (f"SELECT {sql_kontrak(peta, kutip='``', cast='CHAR')} "
             f"FROM {_kutip(tabel)}").replace("'", "''")
    luar = ", ".join(f"CAST({e} AS VARCHAR) AS {e}" for e in KONTRAK)
    con.execute(f"""COPY (SELECT {luar} FROM mysql_query('sumber', '{dalam}'))
                    TO '{tujuan}' (FORMAT parquet)""")
    return con.execute(f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]


def konversi(jalur_dump: str, job_id: str, tujuan: str,
             dialek: str | None = None, tabel: str | None = None,
             lapor=lambda t: None) -> dict:
    """Satu job utuh. Database dan penggunanya dibuang apa pun yang terjadi."""
    db = "job_" + re.sub(r"[^a-z0-9_]", "_", job_id.lower())[:48]
    pengguna = "pemulih_" + secrets.token_hex(4)
    sandi = secrets.token_hex(24)
    mulai = time.perf_counter()

    lapor("K1 periksa dump")
    bersih = jalur_dump + ".bersih"
    dibuang = siapkan_dump(jalur_dump, bersih)

    _admin(f"DROP DATABASE IF EXISTS {_kutip(db)}; "
           f"CREATE DATABASE {_kutip(db)} CHARACTER SET utf8mb4; "
           f"CREATE USER '{pengguna}'@'%' IDENTIFIED BY '{sandi}'; "
           f"GRANT {HAK} ON {_kutip(db)}.* TO '{pengguna}'@'%';")
    try:
        lapor("K2 pulihkan ke basis data sekali pakai")
        _pulihkan(bersih, db, pengguna, sandi)

        lapor("K3 pilih tabel & ekspor parquet")
        con = _buka_duckdb(db, pengguna, sandi)
        try:
            nama_tabel, alasan = pilih_tabel(con, tabel)
            baris = ekspor(con, nama_tabel, alasan.get("peta", {}), tujuan)
        finally:
            con.close()
    finally:
        # Hak pada database yang sudah dibuang TIDAK ikut hilang di MySQL —
        # barisnya tetap di mysql.db. Karena itu penggunanya dibuang juga, bukan
        # hanya database-nya.
        for sql in (f"DROP DATABASE IF EXISTS {_kutip(db)}",
                    f"DROP USER IF EXISTS '{pengguna}'@'%'"):
            try:
                _admin(sql)
            except Exception as e:  # noqa: BLE001
                print(f"[K] PERINGATAN: {sql} gagal: {e}", flush=True)
        try:
            os.unlink(bersih)
        except OSError:
            pass

    return {
        "tabel": nama_tabel,
        "alasan_tabel": {k: v for k, v in alasan.items() if k != "peta"},
        "dialek": "mysql",
        "row_count": baris,
        "dibuang": dibuang,
        "durasi_detik": round(time.perf_counter() - mulai, 1),
    }
