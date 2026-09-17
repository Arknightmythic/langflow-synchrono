"""
Bangkitkan berkas CSV uji bergrade A-E YANG SUDAH DIVERIFIKASI.

Berbeda dari test-data-csv/*.csv lama (yang nama berkasnya aspiratif dan tidak
selalu menghasilkan grade sesuai namanya di engine sekarang), skrip ini
MENYINTESIS data dari tabel master dengan cara yang sama seperti
buat_data_uji_grading.py — NIK dibuat konsisten dengan tanggal lahir & jenis
kelamin — sehingga grade-nya benar-benar terkendali.

Bedanya dengan buat_data_uji_grading.py: skrip itu menulis parquet ke S3 untuk
menguji engine; skrip ini menulis CSV ke disk supaya bisa DIUNGGAH LEWAT PORTAL,
persis seperti berkas yang akan diunggah pengguna.

    docker exec synchrono-langflow python /synchrono/infra/buat_csv_uji.py
    # hasil di /tmp/csv_uji/ di dalam container; salin keluar dengan docker cp

Grade yang diharapkan (diverifikasi lewat pipeline, lihat --verifikasi):

    uji_gradeA.csv   A   NIK + 6 elemen, konsisten & tepercaya
    uji_gradeB.csv   B   NIK + 6 elemen, ~20% NIK dirusak per jenis kerusakan
    uji_gradeC.csv   C   tanpa NIK, 5 elemen terisi penuh
    uji_gradeD.csv   D   tanpa NIK, 5 elemen sebagian kosong (di atas ambang D)
    uji_gradeE.csv   E   hanya nama + tanggal lahir + jenis kelamin
"""

import argparse
import os
import sys

sys.path.insert(0, "/synchrono/lib")

from _shared import buka_koneksi  # noqa: E402

N = int(os.getenv("CSV_UJI_N", "3000"))
KELUAR = os.getenv("CSV_UJI_DIR", "/tmp/csv_uji")

# NIK benar: digit 7-8 = tanggal lahir (+40 perempuan), 1-2 = provinsi (32 Jabar).
NIK_BENAR = """
    '32' || '01' || '01'
 || lpad(CAST(day(tanggal_lahir) + CASE WHEN __p THEN 40 ELSE 0 END AS VARCHAR), 2, '0')
 || lpad(CAST(month(tanggal_lahir) AS VARCHAR), 2, '0')
 || right(CAST(year(tanggal_lahir) AS VARCHAR), 2)
 || lpad(CAST(rn AS VARCHAR), 4, '0')
"""

DASAR = f"""
    WITH mentah AS (
        SELECT row_number() OVER () AS rn,
               nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu
          FROM pg.public.master
         WHERE nama_lengkap IS NOT NULL AND tempat_lahir IS NOT NULL
           AND tanggal_lahir IS NOT NULL AND jenis_kelamin IS NOT NULL
           AND nama_ibu IS NOT NULL
         LIMIT {N}
    ), dasar AS (
        SELECT *,
               lower(trim(jenis_kelamin)) IN ('p', 'perempuan', 'wanita') AS __p,
               rn % 20 AS b
          FROM mentah
    )
"""

# Grade B: seperlima baris dirusak NIK-nya, tiap jenis kerusakan sendiri-sendiri.
NIK_RUSAK = f"""
    CASE
        WHEN b = 15 THEN '32' || '0101'
             || lpad(CAST(day(tanggal_lahir) + CASE WHEN __p THEN 0 ELSE 40 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST(rn AS VARCHAR), 4, '0')
        WHEN b = 16 THEN '99' || substr({NIK_BENAR}, 3)
        WHEN b = 17 THEN substr({NIK_BENAR}, 1, 15)
        WHEN b = 18 THEN '3201010101010001'
        WHEN b = 19 THEN '32' || '0101'
             || lpad(CAST(((day(tanggal_lahir) % 28) + 1)
                     + CASE WHEN __p THEN 40 ELSE 0 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST(rn AS VARCHAR), 4, '0')
        ELSE {NIK_BENAR}
    END
"""

ENAM = """
    {nik}                                       AS nik,
    nama_lengkap                                AS nama,
    tempat_lahir                                AS tempat_lahir,
    strftime(tanggal_lahir, '%d-%m-%Y')         AS tanggal_lahir,
    CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
    nama_ibu                                    AS nama_ibu
"""

LIMA = """
    nama_lengkap                                AS nama,
    {tempat}                                    AS tempat_lahir,
    strftime(tanggal_lahir, '%d-%m-%Y')         AS tanggal_lahir,
    CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
    {ibu}                                       AS nama_ibu
"""

BERKAS = {
    "A": ENAM.format(nik=NIK_BENAR),
    "B": ENAM.format(nik=NIK_RUSAK),
    "C": LIMA.format(tempat="tempat_lahir", ibu="nama_ibu"),
    # D: tempat_lahir ~85%, nama_ibu ~75% — masih di atas ambang D, tapi < 100%.
    "D": LIMA.format(
        tempat="CASE WHEN b < 17 THEN tempat_lahir END",
        ibu="CASE WHEN b < 15 THEN nama_ibu END"),
    "E": """
        nama_lengkap                        AS nama,
        strftime(tanggal_lahir, '%d-%m-%Y') AS tanggal_lahir,
        CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin
    """,
}


def tulis(con) -> None:
    os.makedirs(KELUAR, exist_ok=True)
    tersedia = con.execute(
        "SELECT count(*) FROM pg.public.master WHERE nama_lengkap IS NOT NULL"
    ).fetchone()[0]
    print(f"master: {tersedia:,} baris tersedia, memakai {N:,} per berkas\n")
    for g, pilih in BERKAS.items():
        path = f"{KELUAR}/uji_grade{g}.csv"
        con.execute(f"COPY ({DASAR} SELECT {pilih} FROM dasar) "
                    f"TO '{path}' (FORMAT CSV, HEADER)")
        n = con.execute(
            f"SELECT count(*) FROM read_csv_auto('{path}')").fetchone()[0]
        kolom = [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_csv_auto('{path}')").fetchall()]
        print(f"  uji_grade{g}.csv  {n:>6,} baris  [{', '.join(kolom)}]")
    print(f"\nselesai. Berkas di {KELUAR}/")
    print("Salin keluar container dengan:")
    print(f"  docker cp synchrono-langflow:{KELUAR} .")


def verifikasi(con) -> int:
    """Grade tiap CSV lewat pipeline sungguhan, pastikan hasilnya sesuai nama."""
    from _grading import jalankan_penuh
    print("\n== verifikasi lewat pipeline ==")
    salah = 0
    for g in "ABCDE":
        src = f"{KELUAR}/uji_grade{g}.csv"
        key = f"uploads/verif-{g}/data.parquet"
        con.execute(f"COPY (SELECT * FROM read_csv_auto('{src}', all_varchar=true, "
                    f"sample_size=-1)) TO 's3://bucket-test/{key}' (FORMAT PARQUET)")
        out = jalankan_penuh({"file_id": f"verif-{g}", "s3_bucket": "bucket-test",
                              "parquet_key": key,
                              "enriched_key": f"uploads/verif-{g}/enriched.parquet"})
        s = out["hasil"]["summary"]
        ok = s["gradeLetter"] == g
        salah += 0 if ok else 1
        print(f"  uji_grade{g}: -> grade {s['gradeLetter']} skor {s['qualityScore']} "
              f"({'OK' if ok else 'BEDA, harusnya ' + g})")
    print(f"\n  {5 - salah}/5 sesuai")
    return 1 if salah else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verifikasi", action="store_true",
                   help="Grade tiap CSV lewat pipeline untuk memastikan hasilnya.")
    args = p.parse_args()

    con = buka_koneksi()
    tulis(con)
    if args.verifikasi:
        return verifikasi(con)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
