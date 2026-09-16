"""
Bangkitkan enam CSV dummy bergrade A-F dengan header DAN isi yang disamarkan.

Berbeda dari `buat_data_uji_grading.py` yang menyusun data dari tabel master,
skrip ini mengambil potongan CSV NYATA di test-data-csv lalu menyamarkannya.
Tujuannya bukan menguji penilaian grade, melainkan menguji NORMALISASI: apakah
berkas yang isinya bagus tetap dikenali meski nama kolomnya tidak baku dan
tanggalnya bercampur.

Tiap berkas sengaja menyasar lapis pengenalan yang berbeda:

    samar-a  lapis 1  alias tidak lazim  (no_kependudukan, nama_ibunda)
    samar-b  lapis 2  nama mirip         ("N.I.K.", "Tanggal Lahir (dd/mm/yyyy)")
    samar-c  lapis 3  bentuk nilai       (f1..f5, tanpa petunjuk nama)
    samar-d  lapis 4  kamus master       (kol_1..kol_5, teks bebas)
    samar-e  campuran + wilayah
    samar-f  lapis 5  AI                 (nilai DI LUAR kamus master)

Jalankan DI DALAM container, sesudah potongan sumber disalin ke /tmp/sumber:

    docker exec synchrono-langflow python /synchrono/infra/buat_csv_samar.py
"""

import csv
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/synchrono/lib")

from _shared import buka_koneksi  # noqa: E402

SUMBER = Path("/tmp/sumber")
KELUAR = Path("/tmp/samar")
BUCKET = "bucket-test"
N = 1500

random.seed(7)

# Tempat lahir & nama yang SENGAJA tidak ada di tabel master, supaya lapis 4
# (kamus) tidak bisa menolong dan berkas benar-benar sampai ke AI.
KOTA_LUAR = ["Singapore", "Kuala Lumpur", "Jeddah", "Riyadh", "Hong Kong",
             "Tokyo", "Seoul", "Amsterdam", "Cairo", "Doha"]
NAMA_LUAR = ["Siti Aminah Al-Faruq", "Nurul Hidayati Rahman", "Fatimah Az-Zahra",
             "Khadijah Binti Umar", "Maryam Salsabila", "Aisyah Nur Halimah"]


def baca(nama: str, n: int = N) -> list[dict]:
    """utf-8-sig, BUKAN utf-8: CSV dari Excel diawali BOM, dan tanpa ini
    kolom pertama bernama '﻿nik' sehingga tidak cocok alias apa pun."""
    with (SUMBER / nama).open(encoding="utf-8-sig", errors="replace") as f:
        return [b for _, b in zip(range(n), csv.DictReader(f))]


def tanggal(nilai: str, gaya: int) -> str:
    """Satu tanggal, ditulis ulang dalam salah satu dari tujuh bentuk."""
    for f in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(nilai.strip(), f)
            break
        except (ValueError, AttributeError):
            continue
    else:
        return nilai

    return [
        d.strftime("%d-%m-%Y"),
        d.strftime("%d/%m/%Y"),
        d.strftime("%d %b %Y"),          # abbr Inggris + spasi
        d.strftime("%Y-%m-%d"),
        d.strftime("%m-%d-%Y"),          # urutan Amerika, nyasar
        d.strftime("%d-%m-%y"),          # tahun dua digit
        _bulan_indonesia(d),             # nama bulan Indonesia
    ][gaya % 7]


BULAN_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
            "Jul", "Agt", "Sep", "Okt", "Nov", "Des"]


def _bulan_indonesia(d) -> str:
    return f"{d.day} {BULAN_ID[d.month - 1]} {d.year}"


def tulis(nama: str, baris: list[dict], harapan: dict) -> None:
    KELUAR.mkdir(exist_ok=True)
    berkas = KELUAR / f"{nama}.csv"
    with berkas.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(baris[0]))
        w.writeheader()
        w.writerows(baris)

    con = buka_koneksi()
    tujuan = f"s3://{BUCKET}/uploads/{nama}/data.parquet"
    con.execute(f"""COPY (SELECT * FROM read_csv('{berkas}', header=true,
                          all_varchar=true)) TO '{tujuan}' (FORMAT PARQUET)""")
    con.close()

    print(f"  {nama:10s} {len(baris):>5,} baris  {len(baris[0])} kolom")
    print(f"             header : {list(baris[0])}")
    print(f"             harapan: grade {harapan['grade']}, "
          f"{len(harapan['peta'])} elemen")


def main() -> int:
    print(f"menulis CSV ke {KELUAR} dan parquet ke s3://{BUCKET}/uploads/\n")

    # ── A: alias tidak lazim, tapi masih ada di daftar alias ───────────────
    a = baca("gradeA.csv")
    tulis("samar-a", [{
        "no_kependudukan": r["nik"],
        "nama_warga": r["nama"],
        "kota_lahir": r["tempat_lahir"],
        "tanggal_lhr": tanggal(r["tanggal_lahir"], 2),   # semua "04 Sep 1958"
        "jk": r["jenis_kelamin"],
        "nama_ibunda": r["nama_ibu"],
    } for r in a], {"grade": "A/B", "peta": list("123456")})

    # ── B: nama berantakan, butuh pencocokan mirip ────────────────────────
    b = baca("gradeB.csv")
    tulis("samar-b", [{
        "N.I.K.": r["nik"],
        "Nama Lengkap": r["nama"],
        "Tempat Lahir": r["tempat_lahir"],
        "Tanggal Lahir": tanggal(r["tanggal_lahir"], i),  # tujuh bentuk campur
        "Jenis Kelamin": r["jenis_kelamin"],
        "Nama Ibu": r["nama_ibu"],
    } for i, r in enumerate(b)], {"grade": "B", "peta": list("123456")})

    # ── C: nama kolom tanpa petunjuk sama sekali ──────────────────────────
    c = baca("gradeC.csv")
    tulis("samar-c", [{
        "f1": r["nama"],
        "f2": r["tempat_lahir"],
        "f3": tanggal(r["tanggal_lahir"], i),
        "f4": r["jenis_kelamin"],
        "f5": r["nama_ibu"],
    } for i, r in enumerate(c)], {"grade": "C", "peta": list("12345")})

    # ── D: sama, tapi sebagian kosong ─────────────────────────────────────
    d = baca("gradeD.csv")
    tulis("samar-d", [{
        "kol_1": r["nama"],
        "kol_2": r["tempat_lahir"],
        "kol_3": tanggal(r["tanggal_lahir"], i),
        "kol_4": r["jenis_kelamin"],
        "kol_5": r["nama_ibu"],
    } for i, r in enumerate(d)], {"grade": "C/D", "peta": list("12345")})

    # ── E: wilayah, nama kolom disingkat ──────────────────────────────────
    e = baca("gradeE.csv")
    tulis("samar-e", [{
        "nm": r["nama"],
        "tmp": r["tempat_lahir"],
        "tgl": tanggal(r["tanggal_lahir"], i),
        "prop": r["provinsi"],
        "kab_kot": r["kabupaten"],
        "kecmtn": r["kecamatan"],
        "desa_kel": r["kelurahan"],
        "ibu": r["nama_ibu"],
        "stat": r["status_hidup"],
    } for i, r in enumerate(e)], {"grade": "E", "peta": list("12345678")})

    # ── F: nilai DI LUAR kamus master — satu-satunya yang butuh AI ────────
    # Nama kolomnya berarti bagi manusia, tapi isinya tidak ada di master,
    # sehingga lapis 3 dan 4 sama-sama buntu.
    f = baca("gradeC.csv", 800)
    tulis("samar-f", [{
        "kode_wp": f"WP{i:06d}",
        "nama_pemohon_luar_negeri": random.choice(NAMA_LUAR),
        "kota_domisili_asing": random.choice(KOTA_LUAR),
        "tgl_registrasi": tanggal(r["tanggal_lahir"], i),
        "keterangan_petugas": "verifikasi lapangan",
    } for i, r in enumerate(f)], {"grade": "F/E", "peta": []})

    # ── G: nama kolom NOL PETUNJUK dan nilai di luar kamus master ─────────
    #
    # Inilah satu-satunya keadaan di mana contoh nilai benar-benar menentukan.
    # `c1` dan `c5` sama-sama nama orang, `c2` nama kota — dan tidak satu pun
    # ada di tabel master, jadi lapis 3 dan 4 buntu. Nama kolomnya sendiri
    # tidak memberi tahu apa-apa, sehingga AI dalam mode `nama_saja` hanya bisa
    # menebak. Berkas ini dipakai untuk memutuskan apakah mode `dengan_sampel`
    # layak dinyalakan.
    g = baca("gradeC.csv", 800)
    tulis("samar-g", [{
        "c1": random.choice(NAMA_LUAR),
        "c2": random.choice(KOTA_LUAR),
        "c3": tanggal(r["tanggal_lahir"], i),
        "c4": r["jenis_kelamin"],
        "c5": random.choice(NAMA_LUAR),
    } for i, r in enumerate(g)], {"grade": "C/E", "peta": list("12345")})

    print("\nselesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
