"""
Jalur B untuk `.dmp` — impor Data Pump ke skema sekali pakai.

    .dmp  ->  skema JOB_<id>  ->  parquet kolom kontrak  ->  skema dibuang

UNIT ISOLASINYA SKEMA, BUKAN DATABASE

Di PostgreSQL tiap job mendapat DATABASE baru; di SQL Server, database yang
dilampirkan. Di Oracle padanannya adalah **USER/skema**: membuat pluggable
database per job berarti menit-menit tambahan tiap kali, sementara
`CREATE USER` / `DROP USER CASCADE` ongkosnya sepersekian detik dan membuang
segalanya — tabel, indeks, apa pun yang sempat dibuat.

HANYA TABEL YANG DIIMPOR, DAN ITU LAPISAN KEAMANAN UTAMANYA

`INCLUDE=TABLE` pada impdp berarti prosedur PL/SQL, paket, trigger, kelas Java,
dan job scheduler **tidak pernah masuk ke basis data sama sekali**. Ini
pertahanan yang jauh lebih kuat daripada memeriksa isi dumpnya: yang tidak
diimpor tidak bisa berjalan, apa pun isinya.

Ditambah skema yang haknya cuma `CREATE SESSION` dan `CREATE TABLE` — tanpa
`CREATE ANY DIRECTORY`, tanpa EXECUTE pada `UTL_FILE` atau `DBMS_SCHEDULER` —
tidak ada jalan menyentuh berkas server maupun menjalankan perintah.

`.dmp` LAMA (exp) TIDAK BISA, DAN ITU BUKAN KEKURANGAN DI SINI

Ada DUA format berbeda dengan ekstensi yang sama:

  * **Data Pump** (`expdp`/`impdp`) — yang dipakai sejak Oracle 10g.
  * **exp lama** (`exp`/`imp`) — utilitasnya **dihapus Oracle sejak versi 21**.

Berkas exp lama karena itu tidak bisa diimpor ke Oracle 23 dengan cara apa pun,
dan itu keputusan Oracle, bukan batasan yang bisa kita akali. Ia dikenali dari
penanda `EXPORT:V` di kepala berkas, lalu ditolak dengan pesan yang menyebut
jalan keluarnya: ekspor ulang dengan `expdp`.

Menerimanya diam-diam berarti `impdp` gagal dengan `ORA-39001: invalid argument
value`, yang tidak menerangkan apa pun kepada siapa pun.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import duckdb

from _umum import KONTRAK, pilih_dari_kandidat, sql_kontrak

ORA_DSN = os.getenv("KONV_ORA_DSN", "localhost:1521/FREEPDB1")
ORA_ADMIN = os.getenv("KONV_ORA_ADMIN", "system")
ORA_SANDI = os.getenv("ORACLE_PASSWORD", "")

# Folder yang dikenal Oracle sebagai DIRECTORY object. Berkas .dmp harus ada di
# sini supaya Data Pump bisa membukanya — Data Pump berjalan DI DALAM server,
# bukan di sisi klien.
KERJA = os.getenv("KONV_ORA_KERJA", "/opt/oracle/dmp")
NAMA_DIR = os.getenv("KONV_ORA_DIRNAME", "KONVERSI_DIR")

IMPDP = os.getenv("KONV_IMPDP", "impdp")
BATAS_DETIK = int(os.getenv("KONV_BATAS_DETIK", "1800"))

# Penanda format exp lama. Ada di blok pertama berkas, mis. "EXPORT:V11.02.00".
POLA_EXP_LAMA = re.compile(rb"EXPORT:V\d")


def format_dmp(jalur: str) -> str:
    """`datapump` atau `exp_lama`, dari beberapa ratus bita pertamanya."""
    with open(jalur, "rb") as f:
        kepala = f.read(4096)
    return "exp_lama" if POLA_EXP_LAMA.search(kepala) else "datapump"


def _sambung(pengguna: str, sandi: str):
    """
    Koneksi Oracle mode THIN — tanpa Instant Client.

    `oracledb` mode thin adalah Python murni: tidak ada pustaka native yang
    harus dipasang, tidak ada `LD_LIBRARY_PATH` yang harus benar. Itu menjaga
    image ini sesederhana dua konverter lainnya.
    """
    import oracledb
    return oracledb.connect(user=pengguna, password=sandi, dsn=ORA_DSN)


def _admin():
    return _sambung(ORA_ADMIN, ORA_SANDI)


def _siapkan_skema(skema: str, sandi: str) -> None:
    """
    Buat skema sekali pakai dengan hak seminimal mungkin.

    Yang TIDAK diberikan sama pentingnya dengan yang diberikan:
    tanpa `CREATE ANY DIRECTORY` (membaca berkas server), tanpa
    `CREATE PROCEDURE` (menyimpan PL/SQL), tanpa peran `DBA` atau
    `IMP_FULL_DATABASE`.
    """
    with _admin() as con, con.cursor() as cur:
        cur.execute(f"""
            BEGIN
                EXECUTE IMMEDIATE 'DROP USER {skema} CASCADE';
            EXCEPTION WHEN OTHERS THEN NULL;
            END;""")
        cur.execute(f'CREATE USER {skema} IDENTIFIED BY "{sandi}" '
                    f'DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS')
        cur.execute(f"GRANT CREATE SESSION, CREATE TABLE TO {skema}")
        cur.execute(f"GRANT READ, WRITE ON DIRECTORY {NAMA_DIR} TO {skema}")


def _buang_skema(skema: str) -> None:
    with _admin() as con, con.cursor() as cur:
        cur.execute(f"""
            BEGIN
                EXECUTE IMMEDIATE 'DROP USER {skema} CASCADE';
            EXCEPTION WHEN OTHERS THEN NULL;
            END;""")


# `item_code` 8 pada KU$_DUMPFILE_INFO = nama master table dump-nya, berbentuk
# `"SKEMA"."SYS_EXPORT_SCHEMA_01"`. Dari situ skema asalnya terbaca.
POLA_SKEMA_ASAL = re.compile(r'^"([^"]+)"')


def skema_asal(berkas: str) -> str | None:
    """
    Skema yang datanya ada di dalam dump ini.

    Dibutuhkan untuk `REMAP_SCHEMA`, dan ini ditemukan dengan menjalankannya:
    impor pertama "berhasil" tanpa satu pun tabel, dengan dua baris yang mudah
    terlewat di tengah keluaran impdp:

        ORA-39154: Objects from foreign schemas have been removed from import
        ORA-31655: no data or metadata objects selected for job

    Pengguna non-privilese yang mengimpor dump milik skema LAIN tidak mendapat
    galat — Oracle membuang objeknya diam-diam lalu melaporkan sukses. Tanpa
    `REMAP_SCHEMA`, jalur ini akan selalu menghasilkan parquet kosong dan tidak
    ada yang tahu kenapa.

    Menaikkan hak penggunanya memang membuat impornya jalan, tapi itu
    membatalkan lapisan pertahanan yang justru paling penting di sini. Jadi yang
    dinaikkan bukan haknya, melainkan pengetahuannya: `DBMS_DATAPUMP.
    GET_DUMPFILE_INFO` membaca kepala dump-nya — API resmi Oracle, tanpa
    mengimpor apa pun.
    """
    with _admin() as con, con.cursor() as cur:
        keluar = cur.var(str)
        cur.execute("""
            DECLARE
                t  ku$_dumpfile_info;
                ft NUMBER;
            BEGIN
                DBMS_DATAPUMP.GET_DUMPFILE_INFO(:berkas, :dir, t, ft);
                FOR i IN 1 .. t.COUNT LOOP
                    IF t(i).item_code = 8 THEN :keluar := t(i).value; END IF;
                END LOOP;
            END;""",
            berkas=os.path.basename(berkas), dir=NAMA_DIR, keluar=keluar)
        nilai = keluar.getvalue() or ""
    cocok = POLA_SKEMA_ASAL.match(nilai.strip())
    return cocok.group(1) if cocok else None


def _impor(skema: str, sandi: str, berkas: str, asal: str | None) -> str:
    """
    Jalankan impdp. Hanya TABEL yang diimpor.

    `INCLUDE=TABLE` adalah lapisan pertahanan yang sesungguhnya: prosedur
    PL/SQL, paket, trigger, kelas Java, dan job scheduler tidak pernah masuk ke
    basis data sama sekali. Yang tidak diimpor tidak bisa berjalan, apa pun isi
    dumpnya — itu jauh lebih kuat daripada memeriksa isinya.
    """
    perintah = [
        IMPDP, f"{skema}/{sandi}@{ORA_DSN}",
        f"DIRECTORY={NAMA_DIR}", f"DUMPFILE={os.path.basename(berkas)}",
        "FULL=Y", "INCLUDE=TABLE",
        # Statistik dan indeks tidak dibutuhkan untuk membaca enam kolom, dan
        # keduanya memakan waktu impor yang jauh lebih besar dari datanya.
        "EXCLUDE=STATISTICS,INDEX,CONSTRAINT,REF_CONSTRAINT",
        "TABLE_EXISTS_ACTION=REPLACE", "LOGFILE=impor.log",
    ]
    if asal and asal.upper() != skema.upper():
        perintah.append(f"REMAP_SCHEMA={asal}:{skema}")
    hasil = subprocess.run(perintah, capture_output=True, text=True,
                           timeout=BATAS_DETIK, check=False)
    keluaran = (hasil.stdout or "") + (hasil.stderr or "")
    # impdp mengembalikan kode bukan-nol juga untuk PERINGATAN (mis. objek yang
    # dilewati), jadi kode keluar sendirian bukan penanda gagal. Yang menentukan
    # apakah ada tabelnya sesudah ini — diperiksa pemanggil.
    for b in keluaran.splitlines():
        if b.strip():
            print(f"[impdp] {b.rstrip()}", flush=True)
    if "ORA-39001" in keluaran or "ORA-39000" in keluaran:
        raise RuntimeError(
            "Data Pump menolak berkas ini. Kalau ia hasil `exp` lama (bukan "
            "`expdp`), memang tidak bisa: utilitas `imp` sudah dihapus Oracle "
            "sejak versi 21. Ekspor ulang dengan `expdp`. Pesan asli: "
            + " | ".join(b for b in keluaran.splitlines() if "ORA-" in b)[:400])
    return keluaran


def konversi(jalur_dmp: str, job_id: str, tujuan: str,
             dialek: str | None = None, tabel: str | None = None,
             lapor=lambda t: None) -> dict:
    """Satu job utuh. Skemanya dibuang apa pun yang terjadi."""
    skema = "JOB_" + re.sub(r"[^A-Z0-9_]", "_", job_id.upper())[:24]
    sandi = "K" + re.sub(r"[^A-Za-z0-9]", "", job_id)[:16] + "_9x"
    mulai = time.perf_counter()

    lapor("K1 periksa berkas")
    bentuk = format_dmp(jalur_dmp)
    if bentuk == "exp_lama":
        raise RuntimeError(
            "Berkas ini hasil `exp` lama, bukan `expdp`. Utilitas `imp` yang "
            "bisa membacanya sudah DIHAPUS Oracle sejak versi 21, jadi ia tidak "
            "bisa diimpor ke mesin mana pun yang masih didukung. Mintakan "
            "ekspor ulang dengan `expdp` (Data Pump), atau ekspor ke CSV.")

    os.makedirs(KERJA, exist_ok=True)
    kerja = os.path.join(KERJA, f"{skema}.dmp")
    os.replace(jalur_dmp, kerja)

    con = None
    try:
        lapor("K2 impor ke skema sekali pakai")
        _siapkan_skema(skema, sandi)
        asal = skema_asal(kerja)
        print(f"[K] {job_id} skema asal di dalam dump: {asal or '(tidak terbaca)'}",
              flush=True)
        _impor(skema, sandi, kerja, asal)

        lapor("K3 pilih tabel & ekspor parquet")
        kandidat = {}
        with _sambung(skema, sandi) as ora, ora.cursor() as cur:
            for (nama,) in cur.execute(
                    "SELECT table_name FROM user_tables").fetchall():
                kolom = [r[0] for r in cur.execute(
                    "SELECT column_name FROM user_tab_columns "
                    "WHERE table_name = :t", t=nama).fetchall()]
                jumlah = cur.execute(
                    f'SELECT COUNT(*) FROM "{nama}"').fetchone()[0]
                kandidat[nama] = {"kolom": kolom, "baris": int(jumlah)}

            nama_tabel, alasan = pilih_dari_kandidat(kandidat, tabel)

            con = duckdb.connect()
            con.execute("CREATE TABLE hasil ("
                        + ", ".join(f"{k} VARCHAR" for k in KONTRAK) + ")")
            pilih = sql_kontrak(alasan["peta"], kutip='""', cast="VARCHAR2(4000)")

            # LEWAT ARROW, BUKAN BARIS PER BARIS.
            #
            # Versi pertama memakai `cur.fetchmany()` + `con.executemany()`.
            # Ia benar, tapi memindahkan 200.000 baris lewat objek Python satu
            # per satu: dijalankan sungguhan, ia masih berjalan setelah sepuluh
            # menit sementara impor Data Pump-nya sendiri selesai dalam detik.
            #
            # `fetch_df_batches()` milik oracledb mengembalikan batch yang sudah
            # berbentuk Arrow, dan DuckDB membacanya langsung lewat antarmuka
            # PyCapsule. Datanya tidak pernah menjadi objek Python satu-satu.
            baris = 0
            for kelompok in cur.connection.fetch_df_batches(
                    f'SELECT {pilih} FROM "{nama_tabel}"', size=50_000):
                con.register("_kelompok", kelompok)
                con.execute("INSERT INTO hasil SELECT * FROM _kelompok")
                baris += con.execute(
                    "SELECT count(*) FROM _kelompok").fetchone()[0]
                con.unregister("_kelompok")

        con.execute(f"COPY hasil TO '{tujuan}' (FORMAT parquet)")
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            _buang_skema(skema)
        except Exception as e:  # noqa: BLE001
            print(f"[K] PERINGATAN: skema {skema} gagal dibuang: {e}", flush=True)
        for p in (kerja, os.path.join(KERJA, "impor.log")):
            try:
                os.unlink(p)
            except OSError:
                pass

    return {
        "tabel": nama_tabel,
        "alasan_tabel": {k: v for k, v in alasan.items() if k != "peta"},
        "dialek": "oracle",
        "bentuk_dmp": bentuk,
        "skema_asal": asal,
        "row_count": baris,
        "durasi_detik": round(time.perf_counter() - mulai, 1),
        "kolom_kontrak": KONTRAK,
    }
