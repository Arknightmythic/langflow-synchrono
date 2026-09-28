"""
Membedah G2, tahap yang memakan 77% waktu grading pada 1 juta baris.

    docker exec synchrono-service python /synchrono/beban/profil_g2.py besar-1000k

KENAPA PERLU DIBEDAH SENDIRI

`uji_skala.py` berhenti di tingkat tahap: G2 = 26 detik. Itu cukup untuk tahu
ke mana waktunya pergi, tapi TIDAK cukup untuk memutuskan apa pun — karena G2
mengerjakan dua hal yang sifatnya berlawanan:

    pengenalan kolom   bekerja atas SAMPEL dan atas tabel master. Biayanya
                       mengikuti jumlah KOLOM. Mesin dataframe apa pun tidak
                       akan menyentuhnya.
    pemindaian kolom   `deteksi_konvensi` membaca SELURUH kolom tanggal untuk
                       memutuskan DMY atau MDY. Biayanya mengikuti jumlah
                       BARIS — dan inilah satu-satunya bagian G2 yang bisa
                       dikerjakan mesin lain.

Selisih keduanya menentukan apakah mengganti mesin ada gunanya. Berkas ini
mengukur selisih itu.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/synchrono/lib")

import _normalisasi as N  # noqa: E402
from _grading import _sql_sumber  # noqa: E402
from _shared import buka_koneksi  # noqa: E402

CATATAN: list[tuple[str, float]] = []


def bungkus(modul, nama: str):
    """Membungkus satu fungsi supaya waktunya tercatat, tanpa mengubah hasilnya."""
    asli = getattr(modul, nama)

    def terbungkus(*a, **k):
        t = time.perf_counter()
        try:
            return asli(*a, **k)
        finally:
            CATATAN.append((nama, time.perf_counter() - t))

    setattr(modul, nama, terbungkus)
    return asli


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("berkas")
    p.add_argument("--bucket", default="bucket-test")
    a = p.parse_args()

    jalur = f"s3://{a.bucket}/uploads/{a.berkas}/data.parquet"

    # Dibungkus SEBELUM petakan_kolom dipanggil, karena fungsi-fungsi ini
    # dipanggil dari dalamnya lewat nama modul.
    for nama in ("_lapis_alias", "_lapis_mirip", "_lapis_nilai", "_lapis_kamus",
                 "_lapis_ai", "ambil_sampel", "_prov_sah", "_siapkan_kamus",
                 "deteksi_konvensi", "deteksi_serial_excel", "ada_huruf",
                 "bangun_view"):
        if hasattr(N, nama):
            bungkus(N, nama)

    con = buka_koneksi()

    t0 = time.perf_counter()
    con.execute(f"CREATE OR REPLACE VIEW raw_df AS SELECT * FROM {_sql_sumber(jalur)}")
    kolom = [r[0] for r in con.execute("DESCRIBE raw_df").fetchall()]
    t_describe = time.perf_counter() - t0

    t0 = time.perf_counter()
    jumlah = con.execute("SELECT count(*) FROM raw_df").fetchone()[0]
    t_count = time.perf_counter() - t0

    t0 = time.perf_counter()
    hasil = N.petakan_kolom(con, "raw_df", kolom, izin_ai=False)
    t_petakan = time.perf_counter() - t0

    t0 = time.perf_counter()
    N.bangun_view(con, "raw_df", "norm_df", hasil["peta"], kolom)
    t_bangun = time.perf_counter() - t0

    # `norm_df` adalah VIEW, jadi sampai di sini normalisasi tanggalnya BELUM
    # dikerjakan sama sekali. Ongkosnya baru muncul saat ada yang memindainya —
    # di pipeline sungguhan itu terjadi di G3. Dipaksa di sini supaya terlihat.
    t0 = time.perf_counter()
    con.execute("SELECT count(*) FROM norm_df").fetchone()
    t_materialisasi = time.perf_counter() - t0

    lebar = 34
    print()
    print("=" * 62)
    print(f"  {a.berkas}   {jumlah:,} baris x {len(kolom)} kolom")
    print("=" * 62)
    print(f"  {'buka view + DESCRIBE':<{lebar}} {t_describe:7.2f} detik")
    print(f"  {'count(*) atas raw':<{lebar}} {t_count:7.2f} detik")
    print(f"  {'petakan_kolom (total)':<{lebar}} {t_petakan:7.2f} detik")
    for nama, detik in CATATAN:
        if detik >= 0.005:
            print(f"     {nama:<{lebar - 3}} {detik:7.2f} detik")
    print(f"  {'bangun_view':<{lebar}} {t_bangun:7.2f} detik")
    print(f"  {'materialisasi norm_df (lazy)':<{lebar}} {t_materialisasi:7.2f} detik")
    print("-" * 62)

    # Dua kubu: yang mengikuti jumlah BARIS bisa dipindahkan ke mesin lain,
    # yang mengikuti jumlah KOLOM tidak.
    per_baris = t_count + t_materialisasi + sum(
        d for n, d in CATATAN if n in ("deteksi_konvensi", "deteksi_serial_excel",
                                       "ada_huruf", "_lapis_nilai", "ambil_sampel"))
    per_kolom = t_petakan - sum(
        d for n, d in CATATAN if n in ("deteksi_konvensi", "deteksi_serial_excel",
                                       "ada_huruf", "_lapis_nilai", "ambil_sampel"))
    print(f"  {'~ mengikuti jumlah BARIS':<{lebar}} {per_baris:7.2f} detik")
    print(f"  {'~ mengikuti jumlah KOLOM':<{lebar}} {per_kolom:7.2f} detik")
    print()
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
