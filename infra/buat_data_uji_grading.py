"""
Bangkitkan berkas parquet uji untuk grading, dengan grade yang SUDAH DIKETAHUI.

Gunanya bukan sekadar menyediakan data: tiap berkas dirancang menguji satu
cabang keputusan tertentu, sehingga kalau hasilnya meleset kita tahu persis
bagian mana yang salah.

    docker exec synchrono-langflow python /synchrono/infra/buat_data_uji_grading.py

Harapan per berkas:

    uji-a      A  6 elemen lengkap, NIK konsisten dengan tgl lahir & gender
    uji-b      B  6 elemen lengkap, ~20% NIK bermasalah (beda gender, kecamatan
                  salah, panjang salah, duplikat)
    uji-c      C  5 elemen lengkap, TANPA kolom NIK
    uji-d      D  5 elemen, tanpa NIK, sebagian kosong di bawah 100%
    uji-e      E  hanya nama + tanggal lahir + jenis kelamin
    uji-f      F  nama kolom tidak dikenali sama sekali
    uji-excel  F  NIK rusak jadi notasi ilmiah Excel

NIK di sini DISINTESIS, bukan disalin dari master. NIK master belum tentu
konsisten dengan kolom tanggal lahir dan jenis kelamin di baris yang sama,
padahal justru konsistensi itulah yang hendak diuji.
"""

import sys

sys.path.insert(0, "/synchrono/lib")

from _shared import buka_koneksi  # noqa: E402

BUCKET = sys.argv[1] if len(sys.argv) > 1 else "bucket-test"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 2000

# Digit 7-8 NIK memuat tanggal lahir, +40 untuk perempuan. Digit 1-2 kode
# provinsi (32 = Jawa Barat). Sisanya kecamatan/kelurahan dan nomor urut.
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


def tulis(con, nama: str, pilih: str, dari: str = "dasar") -> None:
    tujuan = f"s3://{BUCKET}/uploads/{nama}/data.parquet"
    con.execute(f"COPY ({DASAR} SELECT {pilih} FROM {dari}) TO '{tujuan}' (FORMAT PARQUET)")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{tujuan}')").fetchone()[0]
    kolom = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{tujuan}')").fetchall()]
    print(f"  {nama:12s} {n:>6,} baris  [{', '.join(kolom)}]")


# Kolom yang sama persis dipakai berulang; ditulis sekali di sini.
ENAM = """
    {nik}                                       AS nik,
    nama_lengkap                                AS nama_lengkap,
    tempat_lahir                                AS tempat_lahir,
    strftime(tanggal_lahir, '%d-%m-%Y')         AS tanggal_lahir,
    CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
    nama_ibu                                    AS nama_ibu_kandung
"""

LIMA = """
    nama_lengkap                                AS nama_lengkap,
    {tempat}                                    AS tempat_lahir,
    strftime(tanggal_lahir, '%d-%m-%Y')         AS tanggal_lahir,
    CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
    {ibu}                                       AS nama_ibu_kandung
"""

# Grade B: seperlima baris dirusak NIK-nya, tiap jenis kerusakan sendiri-sendiri
# supaya keempat penghitung anomaly bisa diperiksa terpisah.
NIK_RUSAK = f"""
    CASE
        -- b=15: gender pada NIK dibalik  -> nikGenderMismatchCount
        WHEN b = 15 THEN '32' || '0101'
             || lpad(CAST(day(tanggal_lahir) + CASE WHEN __p THEN 0 ELSE 40 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST(rn AS VARCHAR), 4, '0')
        -- b=16: kode wilayah 990101 tidak ada -> nikKecamatanInvalidCount
        WHEN b = 16 THEN '99' || substr({NIK_BENAR}, 3)
        -- b=17: hanya 15 digit             -> panjang salah
        WHEN b = 17 THEN substr({NIK_BENAR}, 1, 15)
        -- b=18: semua memakai NIK yang sama -> duplicateNikCount
        WHEN b = 18 THEN '3201010101010001'
        -- b=19: tanggal pada NIK digeser    -> nikDobMismatchCount
        WHEN b = 19 THEN '32' || '0101'
             || lpad(CAST(((day(tanggal_lahir) % 28) + 1)
                     + CASE WHEN __p THEN 40 ELSE 0 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST(rn AS VARCHAR), 4, '0')
        ELSE {NIK_BENAR}
    END
"""


def main() -> int:
    con = buka_koneksi()
    tersedia = con.execute(
        "SELECT count(*) FROM pg.public.master WHERE nama_lengkap IS NOT NULL"
    ).fetchone()[0]
    print(f"master: {tersedia:,} baris tersedia, memakai {N:,}\n")
    print(f"menulis ke s3://{BUCKET}/uploads/<nama>/data.parquet\n")

    # A — semuanya benar.
    tulis(con, "uji-a", ENAM.format(nik=NIK_BENAR))

    # B — 6 elemen lengkap, NIK sebagian rusak.
    tulis(con, "uji-b", ENAM.format(nik=NIK_RUSAK))

    # C — tanpa kolom NIK, lima elemen terisi penuh.
    tulis(con, "uji-c", LIMA.format(tempat="tempat_lahir", ibu="nama_ibu"))

    # D — tanpa NIK, tempat_lahir ~85% dan nama_ibu ~75% terisi. Masih di atas
    # ambang D (0,7 dan 0,6) tapi tidak lagi 100%, jadi tidak naik ke C.
    tulis(con, "uji-d", LIMA.format(
        tempat="CASE WHEN b < 17 THEN tempat_lahir END",
        ibu="CASE WHEN b < 15 THEN nama_ibu END",
    ))

    # E — hanya tiga elemen (nama + tanggal lahir + jenis kelamin).
    tulis(con, "uji-e", """
        nama_lengkap                        AS nama_lengkap,
        strftime(tanggal_lahir, '%d-%m-%Y') AS tanggal_lahir,
        CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin
    """)

    # F — nama kolom tidak dikenali; hanya bisa ditangani lewat pemetaan kustom.
    tulis(con, "uji-f", """
        nama_lengkap  AS kolom_satu,
        tempat_lahir  AS kolom_dua,
        nama_ibu      AS kolom_tiga
    """)

    # Excel — NIK tersimpan sebagai notasi ilmiah, persis seperti tampilan
    # Excel untuk angka 16 digit di kolom sempit. Panjangnya bisa dipulihkan,
    # isinya tidak: seluruh baris harus jadi tidak tepercaya.
    #
    # printf WAJIB di sini. CAST(... AS VARCHAR) di DuckDB justru menghasilkan
    # "3201015107700001.0" — float utuh, bukan notasi ilmiah — sehingga tidak
    # menguji cabang yang dimaksud.
    tulis(con, "uji-excel", ENAM.format(
        nik=f"printf('%.5E', CAST({NIK_BENAR} AS DOUBLE))"))

    # Float utuh — kolom NIK terlanjur dibaca sebagai angka, tapi tidak ada
    # digit yang hilang. Harus pulih menjadi 16 digit dan tetap TEPERCAYA,
    # bukan berubah jadi 17 digit karena titiknya dibuang begitu saja.
    tulis(con, "uji-float", ENAM.format(
        nik=f"CAST({NIK_BENAR} AS VARCHAR) || '.0'"))

    # Samar — ujian sesungguhnya untuk normalisasi. Tidak satu pun nama kolom
    # yang dikenali alias, DAN tanggalnya bercampur enam bentuk sekaligus,
    # termasuk urutan MM-DD dan tahun dua digit.
    tulis(con, "uji-samar", f"""
        {NIK_BENAR}   AS no_identitas,
        nama_lengkap  AS "NAMA LENGKAP WP",
        tempat_lahir  AS "kota kelahiran",
        CASE b % 6
            WHEN 0 THEN strftime(tanggal_lahir, '%d-%m-%Y')
            WHEN 1 THEN strftime(tanggal_lahir, '%d/%m/%Y')
            WHEN 2 THEN strftime(tanggal_lahir, '%d %b %Y')
            WHEN 3 THEN strftime(tanggal_lahir, '%Y-%m-%d')
            -- urutan Amerika, nyasar di berkas yang mayoritasnya DD-MM
            WHEN 4 THEN strftime(tanggal_lahir, '%m-%d-%Y')
            -- tahun dua digit: 58 tidak boleh jadi 2058
            ELSE strftime(tanggal_lahir, '%d-%m-%y')
        END AS "tgl lhr",
        CASE WHEN __p THEN 'P' ELSE 'L' END AS "L/P",
        nama_ibu      AS "ibu kandung",
        'catatan bebas' AS keterangan
    """)

    # Nama bergelar dan bin/binti — menguji deteksi anomali gelar/bin dan pembersihan nama_clean
    tulis(con, "uji-nama", f"""
        {NIK_BENAR}   AS nik,
        CASE b % 5
            WHEN 0 THEN 'Dr. ' || nama_lengkap
            WHEN 1 THEN 'Hj. ' || nama_lengkap || ', S.Pd.'
            WHEN 2 THEN nama_lengkap || ' Bin ' || nama_ibu
            WHEN 3 THEN 'Prof. ' || nama_lengkap || ' Binti ' || nama_ibu || ', M.Kom.'
            ELSE nama_lengkap
        END AS nama_lengkap,
        tempat_lahir,
        strftime(tanggal_lahir, '%d-%m-%Y') AS tanggal_lahir,
        CASE WHEN __p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
        nama_ibu AS nama_ibu_kandung
    """)

    print("\nselesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
