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

# Satu alamat per mesin basis data. Ekstensi berkas yang menentukan ke mana
# perginya — bukan tebakan isi, bukan konfigurasi portal.
ALAMAT = {
    ".sql": os.getenv("KONVERTER_URL", "http://konverter:8390"),
    ".mdf": os.getenv("KONVERTER_MSSQL_URL", "http://konverter-mssql:8391"),
    ".dmp": os.getenv("KONVERTER_ORACLE_URL", "http://konverter-oracle:8392"),
}

# Batas waktu menunggu konverter. Harus LEBIH BESAR dari KONV_BATAS_DETIK di
# sisi sana, supaya yang memutus adalah konverter yang tahu apa yang sedang
# dikerjakannya — bukan pemanggil yang hanya tahu ia lama.
BATAS_DETIK = int(os.getenv("KONVERTER_BATAS_DETIK", "2100"))


def perlu_konversi(job: dict) -> str | None:
    """Kunci sumber yang perlu dikonversi, atau None."""
    kunci = (job.get("raw_source_key") or job.get("csv_key") or "").strip()
    return kunci if kunci and POLA_EKSEKUTABEL.search(kunci) else None


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
    alamat = ALAMAT.get(ext)
    if not alamat:
        raise RuntimeError(
            f"Belum ada layanan konversi untuk berkas {ext}. Yang sudah: "
            f"{', '.join(sorted(ALAMAT))}.")

    muatan = json.dumps({
        "jobId": job["job_id"],
        "fileId": file_id,
        "s3Bucket": job["s3_bucket"],
        "sourceKey": sumber,
        "targetKey": tujuan,
        # Ketiganya opsional. Kalau portal mengirimkannya, keterangan selalu
        # menang atas tebakan konverter.
        "dialect": job.get("sql_dialect"),
        "table": job.get("source_table"),
        # `.ldf` pendamping untuk `.mdf`. Kadang wajib, kadang tidak, dan yang
        # menentukan tidak terlihat dari berkasnya — lihat `_pulih_mssql.py`.
        "logKey": job.get("log_key"),
    }).encode()

    lapor(f"K0 kirim ke layanan konversi ({ext})")
    permintaan = urllib.request.Request(
        f"{alamat.rstrip('/')}/konversi", data=muatan, method="POST",
        headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(permintaan, timeout=BATAS_DETIK) as r:
            hasil = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Konverter membalas 422 dengan sebab yang sudah bisa dibaca manusia;
        # diteruskan apa adanya supaya tidak berubah jadi "HTTP 422" yang tidak
        # menerangkan apa pun di sisi portal.
        try:
            pesan = json.loads(e.read()).get("error") or str(e)
        except Exception:  # noqa: BLE001
            pesan = str(e)
        raise RuntimeError(f"Konversi gagal: {pesan}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Layanan konversi di {alamat} tidak bisa dihubungi ({e.reason}). "
            f"Berkas {ext} butuh layanan itu; format lain tidak."
        ) from e

    if not hasil.get("ok"):
        raise RuntimeError(f"Konversi gagal: {hasil.get('error') or 'tanpa sebab'}")

    # SEJAK TITIK INI grading tidak tahu lagi berkasnya pernah berupa dump.
    # `_pilih_sumber()` akan memilih parquet ini karena ia berbeda dari berkas
    # enriched yang jadi tujuan — jalur yang sama persis dengan parquet kiriman
    # portal.
    job["parquet_key"] = tujuan
    job["raw_source_key"] = None
    job["csv_key"] = None

    print(f"[K] {job['job_id']} konversi selesai: {hasil['row_count']:,} baris "
          f"dari tabel '{hasil['tabel']}' -> {tujuan}")
    return hasil
