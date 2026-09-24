"""
Jalur B — memulihkan dump ke basis data sekali pakai, lalu mengekspor parquet.

    dump .sql  ->  database job_<id>  ->  parquet kolom kontrak  ->  database dibuang

KENAPA INI DI LUAR ENGINE GRADING

Berkas di jalur A adalah DATA: paling buruk isinya nilai aneh. Dump SQL adalah
PERINTAH. Ia bisa memasang trigger, memanggil fungsi, membaca berkas server.
Menjalankannya di dalam container grading berarti menaruh penerjemah perintah
tidak tepercaya di sebelah kredensial S3 dan koneksi PostgreSQL produksi.

Layanan ini karena itu berdiri sendiri: PostgreSQL-nya sendiri, jaringannya
tersekat, dan yang dikembalikannya ke grading hanya parquet berisi enam kolom.

KENAPA SATU CONTAINER, BUKAN SATU CONTAINER PER JOB

Container per job memberi isolasi lebih kuat, tapi menuntut orkestratornya
memegang soket Docker — dan pemegang soket Docker praktis punya akses root ke
host. Menaruh itu pada layanan yang tugasnya menjalankan berkas tidak tepercaya
membatalkan seluruh alasan layanan ini ada.

Yang dipakai sebagai gantinya: satu PostgreSQL berumur panjang, dan tiap job
mendapat DATABASE baru yang dibuang setelah selesai. Isolasi antar job memang
lebih lemah — prosesnya sama — tapi dua lapisan yang benar-benar menahan
serangan tetap utuh: peran non-superuser dan container tanpa akses internet.

Keuntungan sampingannya besar: waktu nyala mesin basis data (puluhan detik
untuk SQL Server, bisa menit untuk Oracle) pindah dari tiap job ke sekali saat
container naik. Yang tersisa per job hanya CREATE/DROP DATABASE.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

import duckdb

sys.path.insert(0, os.getenv("LIB_SYNCHRONO", "/synchrono/lib"))

from _umum import KONTRAK, pilih_dari_kandidat, sql_kontrak  # noqa: E402,F401

PG_HOST = os.getenv("KONV_PG_HOST", "127.0.0.1")
PG_PORT = os.getenv("KONV_PG_PORT", "5432")
PG_SUPER = os.getenv("KONV_PG_SUPER", "postgres")

# Peran yang memulihkan dump. BUKAN superuser, dan itu satu-satunya alasan ia
# ada: peran non-superuser TIDAK BISA menjalankan `COPY ... FROM PROGRAM`,
# `pg_read_server_files`, maupun `pg_write_server_files` — bukan "kemungkinan
# besar tertangkap", memang tidak bisa, apa pun isi dumpnya.
PERAN = os.getenv("KONV_PERAN", "pemulih")

# Batas waktu pemulihan. Tanpa ini, satu dump yang menunggu sesuatu selamanya
# akan memegang slot antrean sampai kiamat — persis yang pernah terjadi di
# server grading, di mana dua job macet menyandera seluruh antrean.
BATAS_DETIK = int(os.getenv("KONV_BATAS_DETIK", "1800"))

# `OWNER TO "nama_peran";` — pg_dump hampir selalu memuatnya.
POLA_PEMILIK = re.compile(r'OWNER\s+TO\s+"?([A-Za-z0-9_$-]+)"?\s*;', re.I)

# Penanda dialek di kepala berkas dump.
PENANDA = {
    "postgresql": re.compile(r"PostgreSQL database dump|pg_dump|SET search_path", re.I),
    "mysql":      re.compile(r"MySQL dump|ENGINE=InnoDB|/\*!40101", re.I),
    "sqlserver":  re.compile(r"\bGO\s*$|SET ANSI_NULLS|\[dbo\]\.", re.I | re.M),
    "oracle":     re.compile(r"CREATE OR REPLACE PACKAGE|VARCHAR2\(|/\*\s*Oracle", re.I),
}


def _psql(sql: str, db: str = "postgres", peran: str | None = None) -> str:
    """Satu perintah psql sebagai superuser, atau sebagai peran lain."""
    perintah = ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_SUPER,
                "-d", db, "-v", "ON_ERROR_STOP=1", "-qAt", "-c", sql]
    if peran:
        perintah[1:1] = ["-c", f"SET ROLE {peran}"]
    hasil = subprocess.run(perintah, capture_output=True, text=True,
                           timeout=60, check=False)
    if hasil.returncode:
        raise RuntimeError(f"psql gagal: {hasil.stderr.strip()[:400]}")
    return hasil.stdout.strip()


def deteksi_dialek(jalur: str) -> str:
    """
    Tebak dialek dari kepala berkas.

    Dikerjakan di sini HANYA sebagai cadangan. Kalau portal mengirimkan
    keterangannya, itu yang dipakai — menerima keterangan jauh lebih murah dan
    lebih jujur daripada menebak dari teks, dan dump yang tidak biasa akan
    membuat tebakan ini meleset tanpa memberi tanda.
    """
    with open(jalur, "r", encoding="utf-8", errors="replace") as f:
        kepala = f.read(64 * 1024)
    for nama, pola in PENANDA.items():
        if pola.search(kepala):
            return nama
    return "postgresql"


def siapkan_dump(asal: str, tujuan: str) -> set[str]:
    """
    Salin dump sambil MEMBUANG klausa `OWNER TO`. Kembalikan peran yang disebut.

    Ini ditemukan dengan menjalankannya, bukan dengan membacanya: pemulihan
    pertama berhenti di baris ke-13 dengan

        ERROR: must be able to SET ROLE "dukcapil_admin"

    `ALTER TABLE ... OWNER TO pemilik;` menuntut peran yang menjalankannya
    menjadi ANGGOTA peran pemilik itu. Peran `pemulih` bukan anggota siapa pun,
    jadi tiap dump pg_dump biasa — dan hampir semuanya memuat baris itu — akan
    berhenti di situ.

    PERBAIKAN YANG PALING JELAS JUSTRU MEMBUKA LUBANG

    Godaannya: `GRANT "dukcapil_admin" TO pemulih`. Itu bekerja, dan untuk peran
    kosong yang baru kita buat memang tidak berbahaya. Tapi dump yang dibuat
    superuser memuat `OWNER TO postgres` — dan peran itu SUDAH ADA di sini,
    sebagai superuser. Menjadikan `pemulih` anggotanya berarti menyerahkan
    kembali seluruh hak yang baru saja dicabut, lewat satu baris di dalam berkas
    yang justru tidak dipercaya. Seluruh lapisan 1 batal oleh perbaikan yang
    kelihatannya sepele.

    Karena itu kepemilikan DIBUANG, bukan dipenuhi. Kita cuma membaca enam kolom
    lalu membuang database-nya; siapa pemilik tabelnya tidak ada artinya sama
    sekali. `pg_restore` punya `--no-owner` untuk alasan yang persis sama; untuk
    dump teks biasa, ini padanannya.

    Membuang pernyataan tidak pernah menambah kemampuan, jadi menulis ulang
    masukan yang tidak dipercaya di sini aman — arahnya hanya satu.

    Peran yang disebut tetap dikembalikan dan dibuat sebagai peran KOSONG,
    karena dump juga memuat `GRANT ... TO peran` yang akan gagal kalau namanya
    tidak ada. Peran kosong itu hanya nama: NOLOGIN, NOSUPERUSER, tanpa
    keanggotaan.

    Dibaca per baris, bukan sekaligus: dump bisa berukuran gigabita, dan
    `OWNER TO` pada keluaran pg_dump selalu muat dalam satu baris.
    """
    peran: set[str] = set()
    with open(asal, "r", encoding="utf-8", errors="replace", newline="") as masuk:
        with open(tujuan, "w", encoding="utf-8", newline="") as keluar:
            for baris in masuk:
                cocok = POLA_PEMILIK.search(baris)
                if cocok:
                    peran.add(cocok.group(1))
                    baris = POLA_PEMILIK.sub("OWNER TO CURRENT_USER;", baris)
                keluar.write(baris)
    return peran


def _siapkan_peran(nama: set[str]) -> None:
    """
    Buat peran yang disebut dump sebagai peran KOSONG.

    Yang sudah ada TIDAK disentuh — terutama `postgres` dan peran bawaan `pg_*`.
    Dan tidak ada satu pun `GRANT ... TO pemulih` di sini: peran pemulih tidak
    boleh menjadi anggota apa pun, itu inti dari lapisan 1.
    """
    for p in nama:
        if p == PG_SUPER or p.startswith("pg_"):
            continue
        _psql(
            f"DO $$ BEGIN "
            f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{p}') THEN "
            f"    CREATE ROLE \"{p}\" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; "
            f"  END IF; "
            f"END $$;"
        )
    _psql(
        f"DO $$ BEGIN "
        f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{PERAN}') THEN "
        f"    CREATE ROLE \"{PERAN}\" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE; "
        f"  END IF; "
        f"END $$;"
    )
    # Superuser harus bisa `SET ROLE pemulih`, bukan sebaliknya.
    _psql(f'GRANT "{PERAN}" TO {PG_SUPER}')


def _pulihkan_sql(jalur: str, db: str) -> None:
    """
    Jalankan dump sebagai peran non-superuser, dalam SATU transaksi.

    `--single-transaction` berpasangan dengan `ON_ERROR_STOP=1`: dump yang gagal
    di tengah tidak meninggalkan setengah tabel yang nanti diekspor seolah-olah
    lengkap. Entah seluruhnya masuk, atau tidak sama sekali.
    """
    lingkungan = {**os.environ, "PGOPTIONS": f"-c role={PERAN}"}
    hasil = subprocess.run(
        ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_SUPER, "-d", db,
         "-v", "ON_ERROR_STOP=1", "--single-transaction", "-q", "-f", jalur],
        capture_output=True, text=True, timeout=BATAS_DETIK,
        env=lingkungan, check=False)
    if hasil.returncode:
        galat = (hasil.stderr or hasil.stdout).strip().splitlines()
        # Baris terakhir psql yang berarti, bukan seluruh banjir keluarannya.
        ringkas = " | ".join(b for b in galat[-4:] if b.strip())[:500]
        raise RuntimeError(
            f"Pemulihan dump gagal. Kalau sebabnya hak akses, itu memang "
            f"disengaja: dump dijalankan sebagai peran non-superuser, dan tabel "
            f"data kependudukan tidak membutuhkan hak lebih dari itu. "
            f"Pesan asli: {ringkas}")


def _buka_duckdb(db: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH 'host={PG_HOST} port={PG_PORT} dbname={db} "
                f"user={PG_SUPER}' AS sumber (TYPE postgres, READ_ONLY)")
    return con


def pilih_tabel(con, tabel_paksa: str | None = None) -> tuple[str, dict]:
    """
    Tabel mana yang berisi data kependudukan?

    Dump bisa memuat puluhan tabel, dan menebak salah berarti menggrading tabel
    yang keliru tanpa ada yang sadar. Kalau portal mengirimkan namanya, itu yang
    dipakai — tidak ada tebakan yang lebih baik daripada keterangan.

    Tanpa keterangan, tabelnya DIPILIH DARI ISINYA, memakai `petakan_kolom()`
    yang sama dengan yang dipakai grading. Mesin pengenalan elemen itu sudah ada
    dan sudah teruji; membuat penebak kedua di sini hanya akan melahirkan dua
    mesin yang bisa berbeda pendapat.

    Skornya: berapa dari enam elemen inti yang dikenali. Tabel `pegawai` dengan
    kolom nama dan tanggal lahir akan mendapat 2; tabel penduduk sungguhan
    mendapat 5 atau 6. Seri dimenangkan tabel dengan baris terbanyak.
    """
    kandidat = {}
    for (t,) in con.execute("""
        SELECT table_name FROM sumber.information_schema.tables
         WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
           AND table_type = 'BASE TABLE'
    """).fetchall():
        con.execute(f'CREATE OR REPLACE VIEW _periksa AS SELECT * FROM sumber."{t}"')
        kandidat[t] = {
            "kolom": [r[0] for r in con.execute("DESCRIBE _periksa").fetchall()],
            "baris": con.execute(f'SELECT count(*) FROM sumber."{t}"').fetchone()[0],
        }
    # Pemilihannya BERSAMA dengan jalur .mdf dan .dmp — lihat `_umum.py`. Kalau
    # tiap mesin memilih dengan caranya sendiri, dump yang isinya sama akan
    # menghasilkan berkas berbeda tergantung format kirimannya.
    return pilih_dari_kandidat(kandidat, tabel_paksa)


def ekspor(con, tabel: str, peta: dict, tujuan: str) -> int:
    """
    Tulis parquet berisi HANYA kolom kontrak, seluruhnya teks.

    Kolom yang tidak ada di sumbernya tetap ditulis sebagai NULL bertipe. Itu
    bukan kerapian: parquet yang bentuk kolomnya berubah-ubah memaksa pembaca di
    sisi grading bercabang, dan cabang seperti itu adalah tempat bug bersembunyi.
    """
    con.execute(f'''COPY (SELECT {sql_kontrak(peta)} FROM sumber."{tabel}")
                    TO '{tujuan}' (FORMAT parquet)''')
    return con.execute(f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]


def konversi(jalur_dump: str, job_id: str, tujuan: str,
             dialek: str | None = None, tabel: str | None = None,
             lapor=lambda t: None) -> dict:
    """Satu job utuh. Database-nya dibuang apa pun yang terjadi."""
    db = "job_" + re.sub(r"[^a-z0-9_]", "_", job_id.lower())[:48]
    mulai = time.perf_counter()

    lapor("K1 periksa dump")
    dialek = dialek or deteksi_dialek(jalur_dump)
    if dialek != "postgresql":
        raise RuntimeError(
            f"Dialek '{dialek}' belum didukung. Yang sudah: postgresql. "
            f"Dump MySQL, Oracle, dan SQL Server tidak saling kompatibel, jadi "
            f"masing-masing butuh mesinnya sendiri.")
    bersih = jalur_dump + ".bersih"
    pemilik = siapkan_dump(jalur_dump, bersih)

    _siapkan_peran(pemilik)
    _psql(f'DROP DATABASE IF EXISTS "{db}"')
    _psql(f'CREATE DATABASE "{db}"')
    try:
        _psql(f'GRANT ALL ON SCHEMA public TO "{PERAN}"', db=db)

        lapor("K2 pulihkan ke basis data sekali pakai")
        _pulihkan_sql(bersih, db)

        lapor("K3 pilih tabel & ekspor parquet")
        con = _buka_duckdb(db)
        try:
            nama_tabel, alasan = pilih_tabel(con, tabel)
            baris = ekspor(con, nama_tabel, alasan.get("peta", {}), tujuan)
        finally:
            con.close()
    finally:
        # DROP DATABASE membuang SEMUANYA sekaligus: tabel, trigger, fungsi,
        # extension, apa pun yang sempat dibuat dump itu. Tidak ada daftar yang
        # harus dijaga tetap lengkap, jadi tidak ada yang bisa terlewat.
        try:
            _psql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        except Exception as e:  # noqa: BLE001
            print(f"[K] PERINGATAN: database {db} gagal dibuang: {e}")
        try:
            os.unlink(bersih)
        except OSError:
            pass

    return {
        "tabel": nama_tabel,
        "alasan_tabel": {k: v for k, v in alasan.items() if k != "peta"},
        "dialek": dialek,
        "row_count": baris,
        "pemilik_dibuat": sorted(pemilik),
        "durasi_detik": round(time.perf_counter() - mulai, 1),
    }
