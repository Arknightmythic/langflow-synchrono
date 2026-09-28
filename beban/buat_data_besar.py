"""
Bangkitkan berkas uji BESAR: jutaan baris, kolom selebar data Dukcapil.

    docker exec synchrono-langflow python /synchrono/beban/buat_data_besar.py 1000000
    docker exec synchrono-langflow python /synchrono/beban/buat_data_besar.py 5000000

KENAPA PERLU BERKAS SENDIRI, BUKAN MENGULANG BERKAS UJI YANG ADA

Berkas uji A-E berisi 3.000 baris dan 6 kolom. Itu cukup untuk membuktikan
grade-nya benar, tapi tidak mengatakan apa pun tentang perilaku pada skala
Dukcapil. Dua hal yang hanya muncul di skala besar:

  * DuckDB berpindah dari bekerja di memori ke menumpahkan ke disk. Titik
    pindahnya menentukan berapa RAM yang harus disediakan server.
  * Biaya per baris TIDAK tetap. Deteksi konvensi tanggal, pencarian duplikat,
    dan penulisan parquet punya sifat penskalaan yang berbeda-beda, dan hanya
    terlihat kalau jumlah barisnya dilipatgandakan.

TIGA HAL YANG DIJAGA AGAR HASILNYA BERARTI

  1. NIK UNIK. Mengulang baris master begitu saja membuat setiap NIK duplikat,
     dan seluruh berkas jatuh jadi anomali — yang terukur lalu bukan grading
     normal melainkan jalur duplikat. Di sini nomor urut dan kode wilayah
     diturunkan dari indeks baris global, jadi NIK unik sampai puluhan juta.

  2. KODE WILAYAH SUNGGUHAN. Diambil dari rujukan 7.265 kecamatan, bukan
     dikarang. Kalau dikarang, NIK_KECAMATAN_INVALID terbit untuk hampir
     semua baris dan waktu yang terukur tercampur penulisan catatan anomali.

  3. NIK KONSISTEN dengan tanggal lahir dan jenis kelamin di baris yang sama,
     kecuali pada baris yang memang sengaja dirusak.

Anomali disuntikkan ~10% dengan komposisi yang menyerupai berkas nyata, supaya
jalur penandaan anomali ikut terukur dan bukan dilewati.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/synchrono/lib")

import _wilayah  # noqa: E402
from _shared import buka_koneksi  # noqa: E402

# Keluaran CSV, bukan parquet: `--csv <jalur>`.
#
# Portal menerima unggahan CSV, jadi berkas uji untuk portal harus CSV pula.
# Parquet tetap bawaannya karena itu yang dipakai pengujian beban: ia dibaca
# langsung dari S3 tanpa tahap unggah.
CSV_TUJUAN = None
_arg = sys.argv[1:]
if "--csv" in _arg:
    i = _arg.index("--csv")
    if i + 1 >= len(_arg):
        sys.exit("--csv perlu jalur berkas tujuannya")
    CSV_TUJUAN = _arg[i + 1]
    _arg = _arg[:i] + _arg[i + 2:]

BARIS = int(_arg[0]) if _arg else 1_000_000
BUCKET = os.getenv("BESAR_BUCKET", "bucket-test")

# DUA POLA NIK, untuk dua pertanyaan yang berbeda.
#
#   konsisten  NIK disintesis dari kode wilayah sungguhan, dan konsisten dengan
#              tanggal lahir serta jenis kelamin baris itu. Grade-nya jadi
#              bermakna, jadi inilah pola untuk mengukur GRADING.
#              Konsekuensinya: NIK-nya baru, tidak ada di master, sehingga
#              matching menemukan nol kecocokan.
#
#   padan      NIK diambil dari master. Matching benar-benar menemukan
#              kecocokan, jadi inilah pola untuk mengukur MATCHING.
#              Konsekuensinya: NIK master tidak selalu konsisten dengan tanggal
#              lahir/jenis kelamin baris yang sama, jadi jumlah anomali naik dan
#              grade turun. Itu tidak mengganggu pengukuran matching.
#
# Memakai satu pola untuk keduanya akan selalu salah di salah satu sisi.
POLA = os.getenv("BESAR_POLA", "konsisten").strip().lower()
if POLA not in ("konsisten", "padan"):
    sys.exit(f"BESAR_POLA harus 'konsisten' atau 'padan', dapat: {POLA!r}")

NAMA = os.getenv("BESAR_NAMA",
                 f"besar-{BARIS // 1000}k" + ("" if POLA == "konsisten" else "-padan"))
KUNCI = f"uploads/{NAMA}/data.parquet"

# Kolom tambahan di luar 6 elemen inti — meniru lebar tabel Dukcapil yang
# sebenarnya. Nilainya sengaja bervariasi per baris supaya kompresi parquet
# tidak menyembunyikan biaya kolom lebar.
TAMBAHAN = """
    lpad(CAST(3200000000000000 + (i % 90000000) AS VARCHAR), 16, '0') AS no_kk,
    m.nama_ibu                                          AS nama_kepala_keluarga,
    CASE i % 6 WHEN 0 THEN 'KEPALA KELUARGA' WHEN 1 THEN 'ISTRI'
               WHEN 2 THEN 'ANAK' WHEN 3 THEN 'CUCU'
               WHEN 4 THEN 'ORANG TUA' ELSE 'FAMILI LAIN' END AS hubungan_keluarga,
    'JL. ' || m.tempat_lahir || ' NO. ' || CAST((i % 200) + 1 AS VARCHAR) AS alamat,
    lpad(CAST((i % 30) + 1 AS VARCHAR), 3, '0')         AS rt,
    lpad(CAST((i % 15) + 1 AS VARCHAR), 3, '0')         AS rw,
    w.nama_kec                                          AS kelurahan,
    w.nama_kec                                          AS kecamatan,
    w.nama_prov                                         AS kabupaten,
    w.nama_prov                                         AS provinsi,
    lpad(CAST(10000 + (i % 80000) AS VARCHAR), 5, '0')  AS kode_pos,
    CASE i % 6 WHEN 0 THEN 'ISLAM' WHEN 1 THEN 'KRISTEN' WHEN 2 THEN 'KATOLIK'
               WHEN 3 THEN 'HINDU' WHEN 4 THEN 'BUDDHA' ELSE 'KONGHUCU' END AS agama,
    CASE i % 4 WHEN 0 THEN 'BELUM KAWIN' WHEN 1 THEN 'KAWIN'
               WHEN 2 THEN 'CERAI HIDUP' ELSE 'CERAI MATI' END AS status_perkawinan,
    CASE i % 8 WHEN 0 THEN 'PETANI' WHEN 1 THEN 'PEGAWAI NEGERI SIPIL'
               WHEN 2 THEN 'KARYAWAN SWASTA' WHEN 3 THEN 'WIRASWASTA'
               WHEN 4 THEN 'PELAJAR/MAHASISWA' WHEN 5 THEN 'GURU'
               WHEN 6 THEN 'PERAWAT' ELSE 'MENGURUS RUMAH TANGGA' END AS pekerjaan,
    CASE i % 7 WHEN 0 THEN 'TIDAK/BELUM SEKOLAH' WHEN 1 THEN 'SD'
               WHEN 2 THEN 'SLTP' WHEN 3 THEN 'SLTA' WHEN 4 THEN 'D-III'
               WHEN 5 THEN 'S-1' ELSE 'S-2' END          AS pendidikan,
    CASE i % 8 WHEN 0 THEN 'A' WHEN 1 THEN 'B' WHEN 2 THEN 'AB' WHEN 3 THEN 'O'
               ELSE '-' END                              AS golongan_darah,
    'WNI'                                                AS kewarganegaraan,
    m.nama_lengkap                                       AS nama_ayah,
    lpad(CAST(3100000000000000 + (i % 70000000) AS VARCHAR), 16, '0') AS nik_ibu,
    lpad(CAST(3300000000000000 + (i % 70000000) AS VARCHAR), 16, '0') AS nik_ayah,
    CASE WHEN i % 3 = 0 THEN 'AL-' || CAST(i AS VARCHAR) END AS akta_lahir_no,
    CASE WHEN i % 5 = 0 THEN 'AK-' || CAST(i AS VARCHAR) END AS akta_kawin_no,
    CASE WHEN i % 97 = 0 THEN 'MENINGGAL' ELSE 'HIDUP' END AS status_hidup,
    strftime(DATE '2015-01-01' + to_days(CAST(i % 3650 AS INTEGER)), '%d-%m-%Y')
                                                         AS tanggal_perekaman,
    CASE WHEN i % 40 = 0 THEN 'X' || lpad(CAST(i % 1000000 AS VARCHAR), 7, '0') END
                                                         AS no_paspor,
    CAST(NULL AS VARCHAR)                                AS no_kitas,
    strftime(DATE '2020-01-01' + to_days(CAST(i % 2000 AS INTEGER)), '%Y-%m-%d')
                                                         AS created_at,
    strftime(DATE '2024-01-01' + to_days(CAST(i % 700 AS INTEGER)), '%Y-%m-%d')
                                                         AS updated_at
"""


def main() -> int:
    con = buka_koneksi()
    info = _wilayah.muat(con)
    if not info["tersedia"]:
        sys.exit("Rujukan wilayah tidak terbaca — NIK tidak bisa dibuat dengan "
                 "kode wilayah sungguhan. Jalankan siapkan_seaweed.py dulu.")

    # Master ditarik SEKALI ke memori DuckDB. Tanpa ini, tiap baris keluaran
    # memicu pembacaan ulang lewat postgres_scanner dan waktunya jadi waktu
    # PostgreSQL, bukan waktu pembangkitan.
    con.execute("""
        CREATE OR REPLACE TEMP TABLE m AS
        SELECT row_number() OVER () - 1 AS rn,
               nik AS nik_master,
               nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu,
               lower(trim(jenis_kelamin)) IN ('p','perempuan','wanita') AS p
          FROM pg.public.master
         WHERE nama_lengkap IS NOT NULL AND tempat_lahir IS NOT NULL
           AND tanggal_lahir IS NOT NULL AND jenis_kelamin IS NOT NULL
           AND nama_ibu IS NOT NULL
    """)
    n_master = con.execute("SELECT count(*) FROM m").fetchone()[0]

    con.execute("""
        CREATE OR REPLACE TEMP TABLE w AS
        SELECT row_number() OVER () - 1 AS rn, kode6, nama_kec, nama_prov
          FROM (SELECT DISTINCT kode6, nama_kec, nama_prov FROM ref_wilayah)
    """)
    n_wil = con.execute("SELECT count(*) FROM w").fetchone()[0]

    print(f"master {n_master:,} baris, rujukan {n_wil:,} kecamatan")
    print(f"pola NIK: {POLA}"
          + ("  (disintesis, konsisten - untuk mengukur grading)"
             if POLA == "konsisten" else
             "  (dari master, cocok - untuk mengukur matching)"))
    print(f"membangkitkan {BARIS:,} baris -> {CSV_TUJUAN or tujuan_parquet}\n")

    # NIK: kode wilayah 6 digit sungguhan + ddmmyy dari tanggal lahir baris ini
    # (+40 untuk perempuan) + nomor urut 4 digit. Kode wilayah berputar tiap
    # baris dan nomor urut naik tiap satu putaran, jadi pasangannya unik sampai
    # 7.265 x 10.000 baris tanpa satu pun NIK kembar yang tak disengaja.
    nik_benar = f"""
        w.kode6
     || lpad(CAST(day(m.tanggal_lahir) + CASE WHEN m.p THEN 40 ELSE 0 END AS VARCHAR), 2, '0')
     || lpad(CAST(month(m.tanggal_lahir) AS VARCHAR), 2, '0')
     || right(CAST(year(m.tanggal_lahir) AS VARCHAR), 2)
     || lpad(CAST((i // {n_wil}) % 10000 AS VARCHAR), 4, '0')
    """

    # Komposisi anomali ~10%, tiap jenis bisa dihitung terpisah.
    nik = f"""
    CASE
        WHEN i % 50 = 10 THEN w.kode6                         -- gender dibalik
             || lpad(CAST(day(m.tanggal_lahir) + CASE WHEN m.p THEN 0 ELSE 40 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(m.tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(m.tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST((i // {n_wil}) % 10000 AS VARCHAR), 4, '0')
        WHEN i % 50 = 11 THEN '99' || substr({nik_benar}, 3)  -- provinsi tak sah
        WHEN i % 50 = 12 THEN substr({nik_benar}, 1, 15)      -- 15 digit
        WHEN i % 50 = 13 THEN '3201010101010001'              -- duplikat
        WHEN i % 50 = 14 THEN w.kode6                         -- tanggal digeser
             || lpad(CAST(((day(m.tanggal_lahir) % 28) + 1)
                     + CASE WHEN m.p THEN 40 ELSE 0 END AS VARCHAR), 2, '0')
             || lpad(CAST(month(m.tanggal_lahir) AS VARCHAR), 2, '0')
             || right(CAST(year(m.tanggal_lahir) AS VARCHAR), 2)
             || lpad(CAST((i // {n_wil}) % 10000 AS VARCHAR), 4, '0')
        ELSE {'m.nik_master' if POLA == 'padan' else nik_benar}
    END"""

    tujuan_parquet = f"s3://{BUCKET}/{KUNCI}"
    tujuan = (f"'{CSV_TUJUAN}' (FORMAT CSV, HEADER)" if CSV_TUJUAN
              else f"'s3://{BUCKET}/{KUNCI}' (FORMAT PARQUET)")

    sql = f"""
        COPY (
            SELECT
                -- `id` WAJIB ada: node kedua matching menolak parquet tanpanya,
                -- dan berkas unggahan portal yang sungguhan memang punya kolom
                -- ini (integer mulai 1). Tanpa `id`, uji skala ini hanya bisa
                -- mengukur grading dan berhenti sebelum matching.
                i + 1                                        AS id,
                {nik}                                        AS nik,
                m.nama_lengkap                               AS nama_lengkap,
                CASE WHEN i % 50 = 20 THEN NULL ELSE m.tempat_lahir END AS tempat_lahir,
                strftime(m.tanggal_lahir, '%d-%m-%Y')        AS tanggal_lahir,
                CASE WHEN m.p THEN 'PEREMPUAN' ELSE 'LAKI-LAKI' END AS jenis_kelamin,
                CASE WHEN i % 50 = 21 THEN NULL ELSE m.nama_ibu END AS nama_ibu_kandung,
                {TAMBAHAN}
              FROM range(0, {BARIS}) t(i)
              JOIN m ON m.rn = i % {n_master}
              JOIN w ON w.rn = i % {n_wil}
        ) TO {tujuan}
    """

    mulai = time.perf_counter()
    con.execute(sql)
    detik = time.perf_counter() - mulai

    # Berkas yang BARU DITULIS yang dibaca ulang, bukan yang lain.
    #
    # Sebelumnya blok ini selalu membaca parquet di S3. Pada mode CSV, parquet
    # itu tidak ikut ditulis — yang terbaca sisa jalan sebelumnya, dan angka
    # yang dilaporkan bukan milik berkas yang baru dibuat.
    if CSV_TUJUAN:
        sumber = (f"read_csv_auto('{CSV_TUJUAN}', all_varchar = true, "
                  f"sample_size = -1)")
    else:
        sumber = f"read_parquet('s3://{BUCKET}/{KUNCI}')"

    n, kol = con.execute(f"SELECT count(*), 0 FROM {sumber}").fetchone()
    nama_kol = [r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM {sumber}").fetchall()]
    unik = con.execute(f"SELECT count(DISTINCT nik) FROM {sumber}").fetchone()[0]

    # Ukuran dibaca SETELAH berkasnya dibaca ulang. Bind-mount ke host Windows
    # masih menuliskan sisanya saat COPY sudah kembali, sehingga pembacaan yang
    # terlalu dini melaporkan ukuran jauh lebih kecil daripada yang sebenarnya.
    if CSV_TUJUAN:
        byte = os.path.getsize(CSV_TUJUAN)
    else:
        byte = con.execute(
            f"SELECT sum(total_compressed_size) FROM "
            f"parquet_metadata('s3://{BUCKET}/{KUNCI}')").fetchone()[0]

    print(f"selesai dalam {detik:,.1f} detik")
    print(f"  baris     : {n:,}")
    print(f"  kolom     : {len(nama_kol)}")
    print(f"  NIK unik  : {unik:,}  ({unik / n * 100:.1f}%)")
    print(f"  ukuran    : {byte / 1024 / 1024:,.1f} MB")
    print(f"  laju      : {n / detik:,.0f} baris/detik")
    print("")
    if CSV_TUJUAN:
        print(f"  berkas    : {CSV_TUJUAN}")
    else:
        print(f"  file_id   : {NAMA}")
        print(f"  kunci     : {KUNCI}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
