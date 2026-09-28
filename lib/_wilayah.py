"""
Rujukan wilayah NIK — dibaca dari S3, BUKAN dari daftar di dalam kode.

    s3://syncrono-master/wilayah/master_wilayah_nik.parquet
    7.265 baris: kode_nik_6digit, kode/nama provinsi, kabupaten, kecamatan

KENAPA TIDAK DITANAM DI KODE

Sebelumnya ada daftar 38 kode provinsi yang ditulis langsung di
`lib/_grading.py` DAN disalin lagi di `lib/_normalisasi.py` — dua salinan yang
bisa menyimpang satu sama lain, dan keduanya hanya bisa diubah dengan menyunting
kode lalu menjalankan ulang container.

Berkas di S3 bisa disunting aplikasi Synchrono, jadi itulah sumber yang
sesungguhnya. Daftar tertanam sudah dihapus.

DUA TINGKAT PEMERIKSAAN

    provinsi   2 digit pertama NIK  -> nikProvinceInvalidCount
    kecamatan  6 digit pertama NIK  -> nikKecamatanInvalidCount

Keduanya dilaporkan terpisah dan itu disengaja. `nikProvinceInvalidCount` ada
di spesifikasi integrasi bagian 4.1 dan sudah dipakai portal, jadi tidak boleh
hilang. Pemeriksaan 6 digit jauh lebih tajam — NIK berkode provinsi benar tapi
kecamatan karangan tetap tertangkap — tapi ia TAMBAHAN, bukan pengganti.
"""

from __future__ import annotations

import os
import re

# Berkas rujukan. Boleh ditimpa lewat environment kalau lokasinya berbeda.
PARQUET = os.getenv(
    "WILAYAH_PARQUET",
    "s3://syncrono-master/wilayah/master_wilayah_nik.parquet",
)

# Kredensial S3 khusus untuk berkas rujukan. Kosongkan kalau berkasnya ada di
# SeaweedFS yang sama dengan berkas unggahan — secret utama sudah menanganinya.
ENDPOINT = os.getenv("WILAYAH_S3_ENDPOINT", "").strip().strip('"')
KEY = os.getenv("WILAYAH_S3_KEY", "").strip()
SECRET = os.getenv("WILAYAH_S3_SECRET", "").strip()

TABEL = "ref_wilayah"

# Apakah kode 6 digit yang tidak dikenal membuat NIK TIDAK TEPERCAYA, atau
# sekadar dilaporkan?
#
# Bawaannya sekadar dilaporkan, dan itu bukan sikap malu-malu. Diukur pada
# berkas produksi d88150c5: 97,4% NIK-nya berkode provinsi sah, tapi hanya
# 11,6% yang kode 6 digitnya ada di rujukan — digit kabupaten dan kecamatannya
# memang dikarang saat data dummy dibuat. Menegakkannya sekarang menjatuhkan
# berkas itu dari grade A ke E dengan 176.810 anomali.
#
# Pada data Dukcapil yang sesungguhnya kode itu semestinya sah, jadi nyalakan
# `WILAYAH_KECAMATAN_TEGAS=1` begitu data aslinya masuk.
KECAMATAN_TEGAS = os.getenv("WILAYAH_KECAMATAN_TEGAS", "0") == "1"


def _bucket(jalur: str) -> str:
    m = re.match(r"^s3://([^/]+)/", jalur)
    return m.group(1) if m else ""


def muat(con) -> dict:
    """
    Muat rujukan ke tabel sementara `ref_wilayah`.

    Dipanggil sekali per sesi grading. 7 ribu baris — biayanya tidak berarti
    dibanding membaca parquet unggahan yang ratusan ribu baris.

    Kalau berkasnya tidak terjangkau, pemeriksaan wilayah DIMATIKAN dan grading
    tetap berjalan. Menggagalkan seluruh grading karena satu berkas rujukan
    tidak terbaca jauh lebih merugikan daripada kehilangan satu jenis
    pemeriksaan — dan keadaannya dilaporkan, bukan disembunyikan.
    """
    if ENDPOINT:
        # Secret ber-SCOPE: rujukan boleh berada di SeaweedFS yang berbeda dari
        # tempat berkas unggahan. Tanpa SCOPE, secret kedua menimpa yang pertama.
        con.execute(f"""
            CREATE OR REPLACE SECRET wilayah (
                TYPE s3, KEY_ID '{KEY}', SECRET '{SECRET}',
                ENDPOINT '{re.sub(r"^https?://", "", ENDPOINT).rstrip("/")}',
                URL_STYLE 'path',
                USE_SSL {str(ENDPOINT.lower().startswith("https://")).lower()},
                SCOPE 's3://{_bucket(PARQUET)}'
            )
        """)

    try:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE {TABEL} AS
            SELECT DISTINCT
                   trim(CAST(kode_nik_6digit AS VARCHAR)) AS kode6,
                   trim(CAST(kode_provinsi   AS VARCHAR)) AS kode_prov,
                   trim(CAST(nama_provinsi   AS VARCHAR)) AS nama_prov,
                   trim(CAST(kode_kecamatan  AS VARCHAR)) AS kode_kec,
                   trim(CAST(nama_kecamatan  AS VARCHAR)) AS nama_kec
              FROM read_parquet('{PARQUET}')
             WHERE kode_nik_6digit IS NOT NULL
        """)
        n = con.execute(f"SELECT count(*) FROM {TABEL}").fetchone()[0]
        prov = con.execute(
            f"SELECT count(DISTINCT kode_prov) FROM {TABEL}").fetchone()[0]
        print(f"[WIL] rujukan wilayah: {n:,} kecamatan, {prov} provinsi "
              f"({PARQUET})")
        return {"tersedia": True, "kecamatan": n, "provinsi": prov,
                "sumber": PARQUET}

    except Exception as e:  # noqa: BLE001 — VPN putus, berkas dipindah, dsb.
        pesan = " ".join(str(e).split())[:150]
        print(f"[WIL] rujukan wilayah TIDAK terbaca — pemeriksaan wilayah "
              f"dimatikan untuk berkas ini.\n      {pesan}")
        con.execute(f"CREATE OR REPLACE TEMP TABLE {TABEL} "
                    f"(kode6 VARCHAR, kode_prov VARCHAR, nama_prov VARCHAR, "
                    f"kode_kec VARCHAR, nama_kec VARCHAR)")
        return {"tersedia": False, "kecamatan": 0, "provinsi": 0,
                "sumber": PARQUET, "galat": pesan}


def tersedia(con) -> bool:
    """Apakah tabel rujukan berisi? Kosong = pemeriksaan wilayah dilewati."""
    try:
        return con.execute(f"SELECT count(*) > 0 FROM {TABEL}").fetchone()[0]
    except Exception:  # noqa: BLE001
        return False


def kode_provinsi(con) -> set[str]:
    """Himpunan kode provinsi 2 digit — dipakai pengenalan kolom NIK."""
    try:
        return {r[0] for r in con.execute(
            f"SELECT DISTINCT kode_prov FROM {TABEL}").fetchall() if r[0]}
    except Exception:  # noqa: BLE001
        return set()


# ── Ekspresi SQL ───────────────────────────────────────────────────────────

def sql_prov_sah(kolom_nik_bersih: str) -> str:
    """Benar kalau 2 digit pertama termasuk kode provinsi yang dikenal."""
    return (f"substr({kolom_nik_bersih}, 1, 2) IN "
            f"(SELECT kode_prov FROM {TABEL})")


def sql_kec_sah(kolom_nik_bersih: str) -> str:
    """Benar kalau 6 digit pertama termasuk kode kecamatan yang dikenal."""
    return (f"substr({kolom_nik_bersih}, 1, 6) IN "
            f"(SELECT kode6 FROM {TABEL})")
