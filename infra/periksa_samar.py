"""
Periksa apakah normalisasi keenam berkas samar BENAR, bukan sekadar berjalan.

Dua hal diadu dengan jawaban yang sudah diketahui:

  1. PEMETAAN KOLOM — tiap elemen harus menunjuk kolom asli yang tepat.
     Memetakan `f2` ke `nama` padahal isinya tempat lahir sama buruknya dengan
     tidak memetakannya sama sekali, dan hanya pemeriksaan macam ini yang
     membedakannya.

  2. NILAI TANGGAL — dibandingkan baris per baris dengan tanggal di CSV sumber.
     Berkas samar ditulis ulang dalam tujuh bentuk berbeda dari satu tanggal
     yang sama, jadi hasil normalisasinya harus kembali ke tanggal itu.

    docker exec synchrono-langflow python /synchrono/infra/periksa_samar.py
"""

import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/synchrono/lib")

from _grading import buka, muat_raw  # noqa: E402

SUMBER = Path("/tmp/sumber")
BUCKET = "bucket-test"

# berkas -> (csv sumber, kolom tanggal samaran, {elemen: kolom_asli_yang_benar})
HARAPAN = {
    "samar-a": ("gradeA.csv", "tanggal_lhr", {
        "nik": "no_kependudukan", "nama": "nama_warga",
        "tempat_lahir": "kota_lahir", "tanggal_lahir": "tanggal_lhr",
        "jenis_kelamin": "jk", "nama_ibu": "nama_ibunda"}),
    "samar-b": ("gradeB.csv", "Tanggal Lahir", {
        "nik": "N.I.K.", "nama": "Nama Lengkap",
        "tempat_lahir": "Tempat Lahir", "tanggal_lahir": "Tanggal Lahir",
        "jenis_kelamin": "Jenis Kelamin", "nama_ibu": "Nama Ibu"}),
    "samar-c": ("gradeC.csv", "f3", {
        "nama": "f1", "tempat_lahir": "f2", "tanggal_lahir": "f3",
        "jenis_kelamin": "f4", "nama_ibu": "f5"}),
    "samar-d": ("gradeD.csv", "kol_3", {
        "nama": "kol_1", "tempat_lahir": "kol_2", "tanggal_lahir": "kol_3",
        "jenis_kelamin": "kol_4", "nama_ibu": "kol_5"}),
    "samar-e": ("gradeE.csv", "tgl", {
        "nama": "nm", "tempat_lahir": "tmp", "tanggal_lahir": "tgl",
        "provinsi": "prop", "kabupaten": "kab_kot", "kecamatan": "kecmtn",
        "kelurahan": "desa_kel", "nama_ibu": "ibu", "status_hidup": "stat"}),
    # Berkas ini sengaja berisi nama dan kota yang TIDAK ada di master, jadi
    # lapis 3 dan 4 sama-sama buntu. Pemetaan di bawah bukan kebetulan: nama
    # kolomnya berarti bagi manusia, dan itulah yang bisa dibaca AI.
    "samar-f": ("gradeC.csv", "tgl_registrasi", {
        "id": "kode_wp",
        "nama": "nama_pemohon_luar_negeri",
        "wilayah": "kota_domisili_asing",
        "tanggal_lahir": "tgl_registrasi"}),
}


def pisah_ambigu(nilai: str) -> bool:
    """
    Benar kalau kedua bagian pertama <= 12, sehingga urutannya mustahil
    dipastikan dari nilai itu sendiri.

    Bedanya penting. '06-04-1962' bisa 6 April maupun 4 Juni — yang dipilih
    mengikuti mayoritas berkas, dan sebagian memang akan keliru. Itu batas yang
    disepakati, bukan kegagalan normalisasi. Sedangkan '13-05-1962' yang
    terbaca jadi 5 Desember adalah kesalahan sungguhan.
    """
    import re
    m = re.fullmatch(r"\s*(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})\s*", nilai or "")
    return bool(m) and int(m.group(1)) <= 12 and int(m.group(2)) <= 12


def tanggal_sumber(nama_csv: str, n: int) -> list[str]:
    """Tanggal asli dari CSV sumber, dinormalkan ke dd-mm-yyyy untuk dibandingkan."""
    with (SUMBER / nama_csv).open(encoding="utf-8-sig", errors="replace") as f:
        baris = [b for _, b in zip(range(n), csv.DictReader(f))]
    keluar = []
    for b in baris:
        v = (b.get("tanggal_lahir") or "").strip()
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                keluar.append(datetime.strptime(v, fmt).strftime("%d-%m-%Y"))
                break
            except ValueError:
                continue
        else:
            keluar.append(None)
    return keluar


def main() -> int:
    total_salah = 0

    for berkas, (csv_sumber, kol_tgl, benar) in HARAPAN.items():
        print(f"\n{'=' * 72}\n{berkas}\n{'=' * 72}")

        s = buka({"file_id": berkas, "s3_bucket": BUCKET,
                  "parquet_key": f"uploads/{berkas}/data.parquet"})
        s = muat_raw(s)
        con, peta = s["con"], s["peta_asli"]
        jejak = {j.get("elemen"): j for j in s["normalisasi"]["jejak"]
                 if j.get("elemen")}

        print("\n  PEMETAAN KOLOM")
        salah = 0
        for elemen, kolom_benar in benar.items():
            dapat = peta.get(elemen)
            if dapat == kolom_benar:
                lapis = jejak.get(elemen, {}).get("lapis", "?")
                print(f"    OK    {elemen:15s} <- {dapat:<26} (lapis {lapis})")
            elif dapat is None:
                print(f"    LEWAT {elemen:15s}    seharusnya {kolom_benar}")
                salah += 1
            else:
                print(f"    SALAH {elemen:15s} <- {dapat:<26} "
                      f"seharusnya {kolom_benar}")
                salah += 1

        keliru = {e: k for e, k in peta.items() if e not in benar}
        for e, k in keliru.items():
            print(f"    EKSTRA{e:15s} <- {k}  (tidak diharapkan)")
            salah += 1

        # ── Tanggal ────────────────────────────────────────────────────────
        if "tanggal_lahir" in peta:
            # Nilai MENTAH diambil dari raw_df: di norm_df nama kolom aslinya
            # sudah tidak ada — memang itu yang sedang diuji. Urutan baris
            # sama di kedua view, jadi keduanya bisa dipasangkan langsung.
            kol_samar = benar["tanggal_lahir"]
            mentah_semua = [r[0] for r in con.execute(
                f'SELECT "{kol_samar}" FROM raw_df').fetchall()]
            hasil_semua = [r[0] for r in con.execute(
                "SELECT tanggal_lahir FROM norm_df").fetchall()]
            baris_asli = list(zip(mentah_semua, hasil_semua))
            n = len(baris_asli)
            asli = tanggal_sumber(csv_sumber, n)

            cocok = kosong_sumber = gagal = ambigu = benar_salah = 0
            contoh_salah = []
            for i, ((mentah, hasil), harap) in enumerate(zip(baris_asli, asli)):
                if not harap:
                    kosong_sumber += 1          # sumbernya memang kosong
                elif hasil == harap:
                    cocok += 1
                elif hasil is None:
                    gagal += 1
                elif pisah_ambigu(mentah):
                    ambigu += 1                 # batas yang disepakati
                else:
                    benar_salah += 1
                    if len(contoh_salah) < 5:
                        contoh_salah.append((i, mentah, harap, hasil))

            dapat_dinilai = n - kosong_sumber
            print(f"\n  TANGGAL ({n:,} baris, {kosong_sumber:,} kosong di sumber)")
            print(f"    tepat              : {cocok:,} "
                  f"({cocok / max(dapat_dinilai, 1):.1%} dari yang terisi)")
            print(f"    ambigu (ikut mayoritas): {ambigu:,}  "
                  f"— kedua pembacaan sah, tidak bisa dipastikan")
            print(f"    gagal dibaca       : {gagal:,}")
            print(f"    SALAH              : {benar_salah:,}")
            for i, mentah, harap, hasil in contoh_salah:
                print(f"        baris {i}: '{mentah}' -> {hasil}, seharusnya {harap}")
            if gagal or benar_salah:
                salah += 1
        else:
            print("\n  TANGGAL: kolom tidak dikenali")

        con.close()
        total_salah += salah
        print(f"\n  >> {'LULUS' if salah == 0 else f'{salah} MASALAH'}")

    print(f"\n{'=' * 72}")
    print(f"TOTAL MASALAH: {total_salah}")
    return 1 if total_salah else 0


if __name__ == "__main__":
    raise SystemExit(main())
