"""
Sisi pemanggil jalur B — engine grading menyuruh layanan konversi bekerja.

Satu fungsi, `konversi_dulu()`, dipanggil pekerja SEBELUM grading dimulai. Kalau
berkasnya bukan format jalur B, ia tidak melakukan apa-apa.

KENAPA KONVERSI JADI TAHAP PERTAMA JOB YANG SAMA, BUKAN JOB TERSENDIRI

Karena pembelokannya ada di dispatch, bukan di portal, portal tidak pernah tahu
ada pembagian jalur — ia memanggil `grading-dispatch` dengan muatan yang sama
untuk semua format. Kalau konversi dicatat sebagai job kedua, akan ada dua
`jobId` untuk satu unggahan, dan sisi portal harus menjahit keduanya di UI.

Sebagai tahap pertama job yang sama: satu `jobId` dari awal sampai akhir,
`grading-status` dan callback berlaku apa adanya, dan kolom `stage` yang sudah
ada tinggal diisi K1..K3 lewat `detak()` yang sama dengan G1..G6. Pengguna
melihat satu proses.

YANG DIKEMBALIKAN

Kunci S3 parquet hasil konversi. Pemanggil menaruhnya di `parquet_key`, dan
sejak titik itu grading tidak bisa membedakannya dari job parquet biasa —
`_pilih_sumber()` memilihnya sendiri karena ia berbeda dari berkas enriched
yang jadi tujuan.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from _grading import POLA_EKSEKUTABEL

# Satu alamat per mesin basis data.
ALAMAT = {
    "postgresql": os.getenv("KONVERTER_URL", "http://konverter:8390"),
    "sqlserver": os.getenv("KONVERTER_MSSQL_URL", "http://konverter-mssql:8391"),
    "oracle": os.getenv("KONVERTER_ORACLE_URL", "http://konverter-oracle:8392"),
    "mysql": os.getenv("KONVERTER_MYSQL_URL", "http://konverter-mysql:8393"),
}

# Ekstensi berkas menentukan mesinnya — kecuali `.sql`, yang dipakai bersama
# oleh PostgreSQL dan MySQL. Untuk `.sql` yang menentukan adalah `sqlDialect`
# kiriman portal; tanpa itu, konverter PostgreSQL yang mengenali dialeknya dari
# kepala berkas dan mengembalikannya kalau bukan miliknya (`konversi_dulu()`).
MESIN_EKSTENSI = {".sql": "postgresql", ".mdf": "sqlserver", ".dmp": "oracle"}
MESIN_DIALEK = {"mysql": "mysql", "mariadb": "mysql"}

# Profil compose yang menyalakan tiap layanan. Mesin selain PostgreSQL ada di
# balik profil karena memegang memori sepanjang container hidup, dipakai atau
# tidak — jadi SENGAJA tidak ikut naik dengan `up -d` biasa.
#
# Namanya disebut di pesan galat. Sebab yang paling sering dari "tidak bisa
# dihubungi" bukan kerusakan melainkan layanannya memang belum dinyalakan, dan
# pesan yang menyebut cara menyalakannya menghemat satu putaran bertanya.
PROFIL = {"sqlserver": "mssql", "oracle": "oracle", "mysql": "mysql"}

# Batas waktu menunggu konverter. Harus LEBIH BESAR dari KONV_BATAS_DETIK di
# sisi sana, supaya yang memutus adalah konverter yang tahu apa yang sedang
# dikerjakannya — bukan pemanggil yang hanya tahu ia lama.
BATAS_DETIK = int(os.getenv("KONVERTER_BATAS_DETIK", "2100"))


def perlu_konversi(job: dict) -> str | None:
    """Kunci sumber yang perlu dikonversi, atau None."""
    kunci = (job.get("raw_source_key") or job.get("csv_key") or "").strip()
    return kunci if kunci and POLA_EKSEKUTABEL.search(kunci) else None


def _kirim(mesin: str, ext: str, isi: dict) -> dict:
    """
    POST ke satu konverter. Balasan 422 dikembalikan sebagai `{ok: False, ...}`.

    Konverter membalas 422 dengan sebab yang sudah bisa dibaca manusia — dan,
    kalau dump-nya milik konverter lain, nama dialeknya. Keduanya diteruskan apa
    adanya supaya tidak berubah jadi "HTTP 422" yang tidak menerangkan apa pun.
    """
    alamat = ALAMAT[mesin]
    permintaan = urllib.request.Request(
        f"{alamat.rstrip('/')}/konversi", data=json.dumps(isi).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(permintaan, timeout=BATAS_DETIK) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {**json.loads(e.read()), "ok": False}
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    except urllib.error.URLError as e:
        profil = PROFIL.get(mesin)
        saran = (
            f" Layanan ini ada di balik profil compose `{profil}` dan TIDAK "
            f"ikut naik dengan `up -d` biasa. Nyalakan dengan:\n"
            f"    docker compose -f docker-compose.server.yml --env-file .env "
            f"--profile {profil} up -d --build konverter-{profil}"
        ) if profil else ""
        raise RuntimeError(
            f"Layanan konversi {mesin} untuk berkas {ext} tidak bisa dihubungi "
            f"di {alamat} ({e.reason}). Format lain (parquet, CSV, xlsx) tidak "
            f"membutuhkannya dan tetap bisa digrading.{saran}"
        ) from e


def konversi_dulu(job: dict, lapor=lambda t: None) -> dict | None:
    """
    Jalankan konversi kalau berkasnya format jalur B. Ubah `job` di tempat.

    Mengembalikan ringkasan konversi untuk dicatat, atau None kalau tidak ada
    yang perlu dikonversi.
    """
    sumber = perlu_konversi(job)
    if not sumber:
        return None

    file_id = job["file_id"]
    tujuan = f"uploads/{file_id}/source.parquet"

    ext = os.path.splitext(sumber)[1].lower()
    mesin = MESIN_EKSTENSI.get(ext)
    if not mesin:
        raise RuntimeError(
            f"Belum ada layanan konversi untuk berkas {ext}. Yang sudah: "
            f"{', '.join(sorted(MESIN_EKSTENSI))}.")

    dialek = str(job.get("sql_dialect") or "").strip().lower() or None
    if ext == ".sql" and dialek in MESIN_DIALEK:
        mesin = MESIN_DIALEK[dialek]

    isi = {
        "jobId": job["job_id"],
        "fileId": file_id,
        "s3Bucket": job["s3_bucket"],
        "sourceKey": sumber,
        "targetKey": tujuan,
        # Ketiganya opsional. Kalau portal mengirimkannya, keterangan selalu
        # menang atas tebakan konverter.
        "dialect": dialek,
        "table": job.get("source_table"),
        # `.ldf` pendamping untuk `.mdf`. Kadang wajib, kadang tidak, dan yang
        # menentukan tidak terlihat dari berkasnya — lihat `_pulih_mssql.py`.
        "logKey": job.get("log_key"),
    }

    lapor(f"K0 kirim ke layanan konversi ({ext}, {mesin})")
    hasil = _kirim(mesin, ext, isi)

    # Dump MySQL tanpa `sqlDialect`: konverter PostgreSQL mengenalinya dari
    # kepala berkas dan mengembalikannya. Dibelokkan SEKALI — konverter tujuan
    # diberi tahu dialeknya, jadi ia tidak akan mengembalikannya lagi.
    belok = MESIN_DIALEK.get(str(hasil.get("dialek") or "").lower())
    if not hasil.get("ok") and mesin == "postgresql" and belok:
        lapor(f"K0 dump berdialek {hasil['dialek']} — dibelokkan ke konverter {belok}")
        mesin, isi["dialect"] = belok, hasil["dialek"]
        hasil = _kirim(mesin, ext, isi)

    if not hasil.get("ok"):
        raise RuntimeError(f"Konversi gagal: {hasil.get('error') or 'tanpa sebab'}")

    # SEJAK TITIK INI grading tidak tahu lagi berkasnya pernah berupa dump.
    # `_pilih_sumber()` akan memilih parquet ini karena ia berbeda dari berkas
    # enriched yang jadi tujuan — jalur yang sama persis dengan parquet kiriman
    # portal.
    job["parquet_key"] = tujuan
    job["raw_source_key"] = None
    job["csv_key"] = None

    print(f"[K] {job['job_id']} konversi selesai ({mesin}): {hasil['row_count']:,} "
          f"baris dari tabel '{hasil['tabel']}' -> {tujuan}")
    return hasil
