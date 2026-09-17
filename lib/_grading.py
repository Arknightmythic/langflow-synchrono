"""
Mesin grading — DuckDB-native, tanpa Polars.

ALUR

    parquet mentah (SeaweedFS)
        -> raw_df        : dibaca langsung lewat httpfs, tidak diunduh
        -> anomali_df    : NIK dibersihkan, anomali ditandai per baris
        -> enriched_df   : kolom asli + 8 kolom derivasi sesuai spesifikasi
        -> enriched.parquet (SeaweedFS)
        -> metrik agregat -> muatan callback

DUA ANGKA YANG BERBEDA ASALNYA

  * `grade` (A-F) ditentukan ATURAN STRUKTURAL — kolom apa yang ada dan seberapa
    terisi. Ini disalin apa adanya dari GraderService yang sudah berjalan di
    produksi, dan TIDAK boleh diturunkan dari skor.

    Alasannya bukan kerapian: matching memilih rumus pembobotan berdasarkan
    grade. Berkas 5 elemen lengkap TANPA kolom NIK adalah grade C — kalau
    grade diturunkan dari skor, berkas itu akan naik ke B dan matching memakai
    rumus ber-NIK terhadap kolom yang tidak ada.

  * `qualityScore` (0-100) adalah ukuran menerus, lalu DIPETAKAN ke dalam pita
    milik grade-nya (tabel `grade_bands`). Jadi keduanya tidak pernah
    bertentangan: grade A tidak mungkin berskor 40.

Ambang dan pita skor ada di PostgreSQL, bukan di kode — sama seperti
`grade_rules` milik matching, supaya bisa disetel tanpa deploy ulang.
"""

from __future__ import annotations

import json
import re
import time

from _config import baca_kriteria
from _nama import sql_ada_gelar, sql_ada_patronimik, sql_bersih
from _normalisasi import ALIAS, bangun_view, petakan_kolom  # noqa: F401
import _wilayah
# Dipakai aturan grade E: nama + tanggal lahir + wilayah. Termasuk kolom
# `wilayah` gabungan, bukan hanya pecahan per tingkat.
from _normalisasi import ELEMEN_WILAYAH as WILAYAH
from _shared import GENDER_L, GENDER_P, buka_koneksi

# Urutan ini dipakai untuk `elementDetails.columns` pada muatan callback.
ENAM_ELEMEN = ["nik", "nama", "tempat_lahir", "tanggal_lahir",
               "jenis_kelamin", "nama_ibu"]

# Kolom yang ditambahkan mesin ini ke enriched parquet (spesifikasi bagian 3.2).
# Delapan pertama sesuai spesifikasi bagian 3.2. `nama_clean` adalah tambahan:
# nama tanpa gelar dan tanpa patronimik, siap dipakai matching tanpa perlu
# dibersihkan ulang di sana.
KOLOM_DERIVASI = ["nik_clean", "nik_prov", "nik_hari", "nik_bulan", "nik_tahun",
                  "nik_trusted", "is_anomaly", "anomaly_notes", "nama_clean"]

HURUF = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}


def _kutip(nama: str) -> str:
    """Identifier SQL. Nama kolom dari CSV instansi bisa mengandung spasi."""
    return '"' + nama.replace('"', '""') + '"'


def _terisi(kolom: str | None) -> str:
    """Ekspresi boolean: kolom benar-benar berisi (NULL dan '' sama-sama kosong)."""
    if not kolom:
        return "FALSE"
    return f"nullif(trim(CAST({_kutip(kolom)} AS VARCHAR)), '') IS NOT NULL"


# ── Tahap 1: buka sesi ─────────────────────────────────────────────────────

def _endpoint_mustahil(endpoint: str) -> bool:
    """Endpoint yang tidak mungkin benar kalau dilihat dari dalam container."""
    host = re.sub(r"^https?://", "", endpoint).split(":")[0].strip().lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "")


def _pilih_sumber(job: dict, tujuan: str) -> str:
    """
    Tentukan berkas MASUKAN, dari beberapa kunci yang mungkin dikirim portal.

    Muatan portal memuat empat kunci sekaligus, dan artinya tidak seragam:

        csvKey             berkas unggahan asli          <- ADA
        rawSourceKey       sama dengan csvKey            <- ADA
        parquetKey         tempat parquet AKAN dibuat    <- belum ada
        enrichedParquetKey sama dengan parquetKey        <- belum ada

    Jadi `parquetKey` di sana BUKAN berkas masukan melainkan nama berkas
    keluaran — dan membacanya berarti membaca berkas yang belum ada, atau lebih
    buruk, membaca hasil job sebelumnya lalu menimpanya.

    Aturannya: pakai `parquetKey` kalau ia benar-benar berkas lain dari tujuan.
    Kalau ia sama dengan tujuan, ia jelas bukan masukan — mundur ke berkas
    unggahan asli. Menolak mentah-mentah tidak menolong siapa pun: muatannya
    sudah memuat berkas yang benar, hanya di kunci yang berbeda.
    """
    def ambil(*nama) -> str:
        for n in nama:
            v = str(job.get(n) or "").strip()
            if v:
                return v
        return ""

    parquet = ambil("parquet_key")
    mentah = ambil("csv_key", "raw_source_key")

    if parquet and parquet.strip("/") != tujuan.strip("/"):
        return parquet

    if mentah:
        if parquet:
            print(f"[G1] parquetKey '{parquet}' sama dengan berkas keluaran — "
                  f"itu nama tujuan, bukan sumber. Memakai berkas unggahan "
                  f"'{mentah}'.")
        return mentah

    if parquet:
        # Sama dengan tujuan DAN tidak ada berkas unggahan yang bisa dipakai.
        # Kalau diteruskan, engine membaca hasil lamanya sendiri lalu
        # menimpanya — data asli hilang tanpa satu pun galat.
        raise ValueError(
            f"parquetKey menunjuk berkas keluaran ({parquet}) dan tidak ada "
            f"csvKey/rawSourceKey sebagai gantinya. parquetKey harus berkas "
            f"UNGGAHAN; kalau sama dengan enrichedParquetKey, hasil grading "
            f"akan menimpa data aslinya."
        )

    raise ValueError("parquetKey, csvKey, maupun rawSourceKey kosong — "
                     "tidak ada berkas yang bisa dibaca")


def buka(job: dict) -> dict:
    """
    Koneksi DuckDB siap pakai + parameter job.

    `s3_endpoint` dari muatan backend menimpa nilai environment, KECUALI kalau
    ia menunjuk localhost — lihat catatannya di bawah. Berkas masukan dipilih
    oleh `_pilih_sumber`.
    """
    file_id = str(job.get("file_id") or "").strip()
    bucket = str(job.get("s3_bucket") or "").strip()

    if not file_id:
        raise ValueError("fileId kosong")
    if not bucket:
        raise ValueError("s3Bucket kosong")

    tujuan = str(job.get("enriched_key")
                 or f"uploads/{file_id}/enriched.parquet").strip()
    kunci = _pilih_sumber(job, tujuan)

    con = buka_koneksi()

    endpoint = str(job.get("s3_endpoint") or "").strip()
    if endpoint and _endpoint_mustahil(endpoint):
        # `localhost` dari dalam container ini berarti CONTAINER INI SENDIRI —
        # dan SeaweedFS tidak pernah berjalan di sini. Jadi nilai seperti itu
        # tidak mungkin benar, apa pun maksud pengirimnya: yang ia maksud adalah
        # localhost MESINNYA, yang tidak punya arti di sisi kami.
        #
        # Diabaikan, bukan diikuti sampai gagal. Sebelumnya ini menggagalkan
        # setiap job dengan "Could not connect to server ... localhost:8333",
        # padahal env engine sudah menunjuk SeaweedFS yang benar.
        print(f"[G1] s3Endpoint '{endpoint}' DIABAIKAN — 'localhost' di dalam "
              f"container menunjuk container ini sendiri. Memakai S3_ENDPOINT "
              f"dari environment.")
        endpoint = ""

    if endpoint:
        # DuckDB menginginkan host:port tanpa skema.
        bersih = re.sub(r"^https?://", "", endpoint).rstrip("/")
        pakai_ssl = endpoint.lower().startswith("https://")
        con.execute(f"""
            CREATE OR REPLACE SECRET seaweed (
                TYPE s3, KEY_ID '{job.get("s3_key") or _kunci_env()[0]}',
                SECRET '{job.get("s3_secret") or _kunci_env()[1]}',
                ENDPOINT '{bersih}', URL_STYLE 'path',
                USE_SSL {str(pakai_ssl).lower()}
            )
        """)

    sumber = f"s3://{bucket}/{kunci}"

    print(f"[G1] file_id={file_id}")
    print(f"[G1] sumber : {sumber}")
    print(f"[G1] tujuan : s3://{bucket}/{tujuan}")

    # Rujukan wilayah dimuat sekali per sesi, dari S3 — bukan dari daftar di
    # dalam kode. Berkasnya bisa disunting aplikasi Synchrono.
    info_wilayah = _wilayah.muat(con)

    return {
        "con": con,
        "wilayah": info_wilayah,
        "file_id": file_id,
        "bucket": bucket,
        "sumber": sumber,
        "enriched_key": tujuan,
        "mulai": time.perf_counter(),
    }


def _kunci_env() -> tuple[str, str]:
    from _shared import S3_KEY, S3_SECRET
    return S3_KEY, S3_SECRET


# Berkas yang dibaca sebagai teks berpemisah, bukan parquet.
POLA_TEKS = re.compile(r"\.(csv|tsv|txt)$", re.I)


def _sql_sumber(jalur: str) -> str:
    """
    Ekspresi pembacaan sumber — parquet atau CSV, dipilih dari akhiran namanya.

    Portal SEHARUSNYA mengubah unggahan jadi parquet lebih dulu, dan itu tetap
    jalur yang dianjurkan: parquet menyimpan tipe kolom, jauh lebih kecil, dan
    jauh lebih cepat dibaca pada berkas ratusan ribu baris. Tapi menolak CSV
    sama sekali berarti satu langkah konversi yang belum jadi di portal
    memblokir seluruh grading — padahal DuckDB bisa membacanya langsung.

    `all_varchar` BUKAN pilihan gaya, dan ini sudah diuji:

      * Tanpa itu, NIK 16 digit terbaca sebagai BIGINT. Nilainya memang masih
        utuh, tapi jalurnya jadi berbeda dari parquet kiriman portal yang
        seluruh kolomnya teks — dan perbedaan jalur adalah tempat bug bersembunyi.
      * Lebih penting: `tanggal_lahir` terdeteksi sebagai DATE, sehingga
        seluruh normalisasi tanggal (deteksi konvensi DD-MM vs MM-DD, tahun dua
        digit, serial Excel) DILEWATI diam-diam.

    `sample_size = -1` membaca seluruh berkas saat mendeteksi struktur, bukan
    hanya beberapa ribu baris pertama.

    Yang TIDAK perlu ditangani sendiri, sudah diuji ke DuckDB: BOM dari Excel
    dibuang otomatis (kolom pertama tidak jadi bernama '﻿nik'), dan
    pemisah titik koma terdeteksi sendiri.
    """
    if POLA_TEKS.search(jalur):
        return f"read_csv_auto('{jalur}', all_varchar = true, sample_size = -1)"
    return f"read_parquet('{jalur}')"


# ── Tahap 2: baca parquet mentah ───────────────────────────────────────────

def muat_raw(s: dict) -> dict:
    """
    Baca parquet, lalu NORMALISASI nama kolom dan format tanggal.

    Berkas tidak diunduh — DuckDB membacanya langsung dari SeaweedFS.

    Sesudah tahap ini seluruh pipeline bekerja di atas `norm_df`, yang kolomnya
    sudah bernama baku. Itu sebabnya `peta` di bawah menjadi pemetaan identitas:
    penyamaran nama kolom sudah selesai di sini, dan tahap berikutnya tidak
    perlu lagi tahu bahwa aslinya bernama "no_identitas" atau "tgl lhr".
    """
    con = s["con"]
    con.execute(f"CREATE OR REPLACE VIEW raw_df AS "
                f"SELECT * FROM {_sql_sumber(s['sumber'])}")

    kolom_asli = [r[0] for r in con.execute("DESCRIBE raw_df").fetchall()]
    jumlah = con.execute("SELECT count(*) FROM raw_df").fetchone()[0]
    if jumlah == 0:
        raise ValueError(f"Berkas sumber kosong: {s['sumber']}")

    bentuk = "CSV" if POLA_TEKS.search(s["sumber"]) else "parquet"
    print(f"[G2] {jumlah:,} baris, {len(kolom_asli)} kolom  (dibaca sebagai {bentuk})")

    hasil = petakan_kolom(con, "raw_df", kolom_asli,
                          izin_ai=s.get("izin_ai", True))
    peta_asli = hasil["peta"]

    for j in hasil["jejak"]:
        if j.get("elemen"):
            print(f"[G2] lapis {j['lapis']}: {j['elemen']:14s} <- "
                  f"{j['kolom']:<20} {j['dasar']}")
        else:
            print(f"[G2] lapis {j.get('lapis')}: {j['dasar']}")

    info = bangun_view(con, "raw_df", "norm_df", peta_asli, kolom_asli)
    kolom_norm = [r[0] for r in con.execute("DESCRIBE norm_df").fetchall()]

    if info["tanggal"]:
        print(f"[G2] tanggal: {info['tanggal']['dasar']}")
    if hasil["kolom_sisa"]:
        print(f"[G2] tidak dikenali, dibawa apa adanya: "
              f"{', '.join(hasil['kolom_sisa'][:8])}"
              + (" ..." if len(hasil["kolom_sisa"]) > 8 else ""))

    kenal = [e for e in ENAM_ELEMEN if e in peta_asli]
    print(f"[G2] elemen inti dikenali ({len(kenal)}/6): {', '.join(kenal) or 'tidak ada'}")

    return {
        **s,
        "kolom": kolom_norm,
        "kolom_asli": kolom_asli,
        # Identitas: di norm_df, nama kolom sudah sama dengan nama elemennya.
        "peta": {e: e for e in peta_asli},
        "peta_asli": peta_asli,
        "normalisasi": {**info, "jejak": hasil["jejak"]},
        "row_count": jumlah,
        "wilayah_ada": [w for w in WILAYAH if w in peta_asli],
    }


# ── Tahap 3 & 4: bersihkan NIK, tandai anomali ─────────────────────────────

def _sql_nik(kol_nik: str | None, wilayah_siap: bool = True) -> dict[str, str]:
    """
    Ekspresi pembersihan NIK.

    Ada DUA bentuk rusak yang sama-sama berasal dari spreadsheet, dan keduanya
    harus dibedakan karena akibatnya tidak sama:

      * NOTASI ILMIAH (3.20101E+15) — memungut digitnya saja menghasilkan
        "32010115" yang salah panjang, jadi nilainya dikembangkan dulu dari
        bentuk desimalnya. Digit belakang TETAP HILANG; pengembangan ini hanya
        memulihkan panjang, bukan isi. Karena itu barisnya selalu dinyatakan
        tidak tepercaya dan ditandai `hasExcelScientificNik`.

      * FLOAT UTUH (3201015107700001.0) — sering muncul kalau kolom NIK
        terlanjur dibaca sebagai angka. Di sini TIDAK ada yang hilang; cukup
        buang bagian pecahannya. Membuang seluruh karakter non-digit justru
        merusak: titik hilang tapi nol di belakangnya ikut terbaca, dan NIK
        berubah jadi 17 digit.
    """
    if not kol_nik:
        # NULL harus BERTIPE. Tanpa CAST, DuckDB menebak sendiri dan kolom
        # nik_hari di parquet hasil bisa lahir sebagai tipe yang tidak
        # diharapkan pembaca di sisi portal.
        return {
            "ada": "FALSE",
            "clean": "CAST(NULL AS VARCHAR)",
            "prov": "CAST(NULL AS VARCHAR)",
            "hari": "CAST(NULL AS INTEGER)",
            "bulan": "CAST(NULL AS INTEGER)",
            "tahun": "CAST(NULL AS INTEGER)",
            "hari_mentah": "CAST(NULL AS INTEGER)",
            "jk": "CAST(NULL AS VARCHAR)",
            "kec": "CAST(NULL AS VARCHAR)",
            "len_ok": "FALSE",
            "prov_ok": "FALSE",
            "kec_ok": "FALSE",
            "excel": "FALSE",
        }

    mentah = f"trim(CAST({_kutip(kol_nik)} AS VARCHAR))"
    excel = f"regexp_matches(upper({mentah}), '^[0-9](\\.[0-9]+)?E[+-]?[0-9]+$')"
    clean = f"""CASE
        WHEN {excel}
            THEN CAST(CAST(TRY_CAST({mentah} AS DOUBLE) AS DECIMAL(20,0)) AS VARCHAR)
        WHEN regexp_matches({mentah}, '^[0-9]+\\.0*$')
            THEN regexp_replace({mentah}, '\\..*$', '')
        ELSE regexp_replace({mentah}, '[^0-9]', '', 'g')
    END"""

    hari_mentah = "TRY_CAST(substr(__nik_clean, 7, 2) AS INTEGER)"
    return {
        "ada": "TRUE",
        "clean": clean,
        "excel": excel,
        "len_ok": "length(__nik_clean) = 16",
        "prov": "substr(__nik_clean, 1, 2)",
        "kec": "substr(__nik_clean, 1, 6)",
        # Kedua pemeriksaan membaca tabel rujukan yang dimuat dari S3, bukan
        # daftar di dalam kode. Dilaporkan TERPISAH: yang 2 digit sudah ada di
        # spesifikasi dan dipakai portal, yang 6 digit jauh lebih tajam.
        # Kalau rujukan tidak terbaca, pemeriksaan wilayah DIMATIKAN — bukan
        # dianggap gagal. Tabel rujukan yang kosong membuat setiap NIK jatuh
        # tidak sah, dan seluruh berkas akan anjlok gradenya hanya karena VPN
        # sedang putus.
        "prov_ok": (_wilayah.sql_prov_sah("__nik_clean") if wilayah_siap
                    else "TRUE"),
        "kec_ok": (_wilayah.sql_kec_sah("__nik_clean") if wilayah_siap
                   else "TRUE"),
        "hari_mentah": hari_mentah,
        "hari": "CASE WHEN __nik_hari_mentah > 40 THEN __nik_hari_mentah - 40 "
                "ELSE __nik_hari_mentah END",
        "bulan": "TRY_CAST(substr(__nik_clean, 9, 2) AS INTEGER)",
        "tahun": "TRY_CAST(substr(__nik_clean, 11, 2) AS INTEGER)",
        "jk": "CASE WHEN __nik_hari_mentah > 40 THEN 'p' ELSE 'l' END",
    }


def _sql_nama(kol_nama: str | None) -> dict[str, str]:
    """
    Pembersihan nama: gelar depan/belakang dan patronimik bin/binti.

    Berkas tanpa kolom nama tetap mendapat ketiga ekspresi ini — bernilai NULL
    dan FALSE — supaya tahap berikutnya tidak perlu bercabang.
    """
    if not kol_nama:
        return {"bersih": "CAST(NULL AS VARCHAR)",
                "gelar": "FALSE", "bin": "FALSE"}
    k = _kutip(kol_nama)
    return {"bersih": sql_bersih(k),
            "gelar": sql_ada_gelar(k),
            "bin": sql_ada_patronimik(k)}


def _sql_gender(kol: str | None) -> str:
    if not kol:
        return "NULL"
    b = f"lower(trim(CAST({_kutip(kol)} AS VARCHAR)))"
    isi_l = ", ".join(f"'{v}'" for v in GENDER_L)
    isi_p = ", ".join(f"'{v}'" for v in GENDER_P)
    return f"CASE WHEN {b} IN ({isi_l}) THEN 'l' WHEN {b} IN ({isi_p}) THEN 'p' END"


def bersihkan_dan_tandai(s: dict) -> dict:
    """
    Tabel `anomali_df`: kolom asli + kolom kerja `__*` + vonis per baris.

    Dibuat sebagai TABLE, bukan VIEW, karena dipindai berkali-kali sesudah ini
    (metrik agregat, lalu penulisan parquet). Sebagai view, seluruh pembersihan
    dan window function akan dihitung ulang tiap kali.
    """
    con, peta = s["con"], s["peta"]
    siap = (s.get("wilayah") or {}).get("tersedia", False)
    nik = _sql_nik(peta.get("nik"), siap)
    nama = _sql_nama(peta.get("nama"))
    kol_tgl = peta.get("tanggal_lahir")

    # Kaskade enam format tidak lagi diperlukan di sini: tahap G2 sudah
    # menyeragamkan tanggal jadi dd-mm-yyyy, termasuk nama bulan Indonesia,
    # tahun dua digit, dan baris ber-urutan MM-DD.
    tgl = (f"try_strptime({_kutip(kol_tgl)}, '%d-%m-%Y')"
           if kol_tgl else "NULL")
    jk = _sql_gender(peta.get("jenis_kelamin"))

    # Lapis 1 — normalisasi dasar.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _lapis1 AS
        SELECT r.*,
               {nik['clean']}       AS __nik_clean,
               {nik['excel']}       AS __nik_excel,
               CAST({tgl} AS DATE)  AS __tgl,
               {jk}                 AS __jk,
               {nama['bersih']}     AS __nama_clean,
               {nama['gelar']}      AS __nama_gelar,
               {nama['bin']}        AS __nama_bin
          FROM norm_df r
    """)

    # Lapis 2 — turunan yang bergantung pada __nik_clean.
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _lapis2 AS
        SELECT *,
               {nik['len_ok']}      AS __nik_len_ok,
               {nik['prov']}        AS __nik_prov,
               {nik['prov_ok']}     AS __nik_prov_ok,
               {nik['kec']}         AS __nik_kec,
               {nik['kec_ok']}      AS __nik_kec_ok,
               {nik['hari_mentah']} AS __nik_hari_mentah,
               {nik['bulan']}       AS __nik_bulan,
               {nik['tahun']}       AS __nik_tahun
          FROM _lapis1
    """)

    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _lapis3 AS
        SELECT *,
               {nik['hari']} AS __nik_hari,
               {nik['jk']}   AS __nik_jk
          FROM _lapis2
    """)

    # Lapis 4 — duplikasi. PARTITION dijaga agar NIK tak sah tidak saling
    # berkelompok: tanpa CASE, semua baris ber-NIK kosong akan dihitung duplikat
    # satu sama lain.
    con.execute("""
        CREATE OR REPLACE TEMP VIEW _lapis4 AS
        SELECT *,
               CASE WHEN __nik_len_ok
                    THEN count(*) OVER (PARTITION BY __nik_clean)
                    ELSE 1 END AS __nik_kembar
          FROM _lapis3
    """)

    # Lapis 5 — vonis per baris.
    tgl_cocok = (
        "__tgl IS NOT NULL AND __nik_len_ok AND ("
        "day(__tgl) <> __nik_hari OR month(__tgl) <> __nik_bulan "
        "OR (year(__tgl) % 100) <> __nik_tahun)"
    ) if kol_tgl else "FALSE"

    jk_cocok = (
        "__jk IS NOT NULL AND __nik_len_ok AND __jk <> __nik_jk"
    ) if peta.get("jenis_kelamin") else "FALSE"

    elemen_hadir = [e for e in ENAM_ELEMEN if e in peta]
    kosong_cek = [
        f"CASE WHEN {_terisi(peta[e])} THEN NULL ELSE '{e}' END"
        for e in elemen_hadir
    ] or ["NULL"]

    catatan = _sql_catatan(bool(peta.get("nik")), kol_tgl,
                           peta.get("jenis_kelamin"),
                           bool(peta.get("nama")))

    # Hanya SATU tabel yang dimaterialkan, dan di sinilah window function
    # duplikasi ikut terhitung sekali untuk selamanya.
    con.execute(f"""
        CREATE OR REPLACE TABLE vonis_df AS
        SELECT *,
               ({tgl_cocok})                       AS __beda_tgl,
               ({jk_cocok})                        AS __beda_jk,
               (__nik_kembar > 1)                  AS __nik_dobel,
               (__nik_hari NOT BETWEEN 1 AND 31
                OR __nik_bulan NOT BETWEEN 1 AND 12) AS __nik_tgl_ngawur,
               list_filter([{', '.join(kosong_cek)}], x -> x IS NOT NULL)
                                                   AS __elemen_kosong
          FROM _lapis4
    """)

    # `anomali_df` sengaja VIEW di atas tabel itu, bukan tabel kedua: membuat
    # tabel dari SELECT atas view yang merujuk dirinya sendiri akan ditolak
    # DuckDB sebagai ketergantungan melingkar. Kolom yang ditambahkan di sini
    # murni turunan baris — tidak ada pemindaian ulang yang mahal.
    ada_nik = "TRUE" if peta.get("nik") else "FALSE"
    # Kode 6 digit ikut menentukan kepercayaan HANYA kalau ditegakkan.
    kec_wajib = "__nik_kec_ok" if _wilayah.KECAMATAN_TEGAS else "TRUE"
    con.execute(f"""
        CREATE OR REPLACE VIEW anomali_df AS
        SELECT *,
               __trusted                                     AS nik_trusted,
               ((NOT __trusted AND {ada_nik})
                OR length(__elemen_kosong) > 0
                OR __nama_gelar OR __nama_bin)               AS is_anomaly,
               __nama_clean                                  AS nama_clean,
               __catatan                                     AS anomaly_notes
          FROM (
            SELECT *,
                   ({ada_nik}
                    AND __nik_len_ok
                    AND __nik_prov_ok
                    AND {kec_wajib}
                    AND NOT __nik_excel
                    AND NOT COALESCE(__nik_tgl_ngawur, TRUE)
                    AND NOT __beda_tgl
                    AND NOT __beda_jk
                    AND NOT __nik_dobel)  AS __trusted,
                   {catatan}              AS __catatan
              FROM vonis_df
          )
    """)

    n_anomali = con.execute(
        "SELECT count(*) FROM anomali_df WHERE is_anomaly").fetchone()[0]
    print(f"[G3] {n_anomali:,} dari {s['row_count']:,} baris ditandai anomali")
    return {**s, "anomaly_count": n_anomali}


def _sql_catatan(ada_nik: bool, kol_tgl: str | None, kol_jk: str | None,
                 ada_nama: bool = False) -> str:
    """Keterangan anomali per baris, digabung dengan '; '."""
    potong = []

    if ada_nik:
        potong += [
            "CASE WHEN __nik_excel THEN 'NIK rusak akibat notasi ilmiah Excel "
            "(digit belakang tidak dapat dipulihkan)' END",

            "CASE WHEN NOT __nik_len_ok THEN 'Panjang NIK ' || "
            "CAST(length(__nik_clean) AS VARCHAR) || ' digit, seharusnya 16' END",

            "CASE WHEN __nik_len_ok AND NOT __nik_prov_ok THEN "
            "'Kode provinsi NIK tidak dikenali: ' || __nik_prov END",

        ]

        # Hanya dicatat saat ditegakkan. Kalau tidak, seluruh berkas dummy akan
        # penuh catatan untuk sesuatu yang sengaja tidak memengaruhi apa pun —
        # dan `anomaly_notes` harus sejalan dengan `is_anomaly`.
        if _wilayah.KECAMATAN_TEGAS:
            potong.append(
                # Diperiksa hanya kalau provinsinya sudah benar, supaya baris
                # yang sama tidak dilaporkan dua kali untuk sebab yang sama.
                "CASE WHEN __nik_len_ok AND __nik_prov_ok AND NOT __nik_kec_ok "
                "THEN 'Kode wilayah 6 digit NIK tidak dikenali: ' || __nik_kec END"
            )

        potong += [

            "CASE WHEN __nik_len_ok AND __nik_tgl_ngawur THEN "
            "'Digit tanggal lahir pada NIK di luar rentang wajar' END",

            "CASE WHEN __nik_dobel THEN 'NIK duplikat di dalam berkas ("
            "muncul ' || CAST(__nik_kembar AS VARCHAR) || ' kali)' END",
        ]

    if kol_tgl and ada_nik:
        potong.append(
            "CASE WHEN __beda_tgl THEN 'Beda Tanggal Lahir: digit NIK "
            "menunjuk ' || CAST(__nik_hari AS VARCHAR) || '/' || "
            "CAST(__nik_bulan AS VARCHAR) || ', kolom tanggal lahir terisi ' || "
            "strftime(__tgl, '%d/%m/%Y') END"
        )

    if kol_jk and ada_nik:
        potong.append(
            "CASE WHEN __beda_jk THEN 'Beda Jenis Kelamin: digit hari NIK "
            "mengindikasikan ' || CASE WHEN __nik_jk = 'p' THEN 'wanita' "
            "ELSE 'pria' END || ' (' || CAST(__nik_hari_mentah AS VARCHAR) || "
            "'), namun kolom jenis kelamin terisi ' || upper(__jk) END"
        )

    if ada_nama:
        potong += [
            "CASE WHEN __nama_gelar THEN 'Nama memuat gelar akademik atau "
            "sebutan kehormatan; dibersihkan ke kolom nama_clean' END",
            "CASE WHEN __nama_bin THEN 'Nama memuat patronimik bin/binti; "
            "dibersihkan ke kolom nama_clean' END",
        ]

    potong.append(
        "CASE WHEN length(__elemen_kosong) > 0 THEN 'Elemen kosong: ' || "
        "array_to_string(__elemen_kosong, ', ') END"
    )

    return (f"array_to_string(list_filter([{', '.join(potong)}], "
            "x -> x IS NOT NULL), '; ')")


# ── Tahap 5: metrik, skor, grade ───────────────────────────────────────────

def skor_dan_grade(s: dict) -> dict:
    """Metrik agregat + grade struktural + skor mutu di dalam pita grade-nya."""
    con, peta = s["con"], s["peta"]
    total = s["row_count"]

    terisi = {e: _terisi(peta.get(e)) for e in ENAM_ELEMEN}
    pilih = [f"count(*) FILTER (WHERE {terisi[e]}) AS isi_{e}" for e in ENAM_ELEMEN]
    pilih += [
        "count(*) FILTER (WHERE nik_trusted)                       AS trusted",
        "count(*) FILTER (WHERE __nik_len_ok)                      AS len16",
        "count(*) FILTER (WHERE __nik_dobel)                       AS dobel",
        "count(*) FILTER (WHERE __beda_tgl)                        AS beda_tgl",
        "count(*) FILTER (WHERE __beda_jk)                         AS beda_jk",
        "count(*) FILTER (WHERE __nik_len_ok AND NOT __nik_prov_ok) AS prov_salah",
        "count(*) FILTER (WHERE __nik_len_ok AND NOT __nik_kec_ok)  AS kec_salah",
        "count(*) FILTER (WHERE __nama_gelar)                       AS nama_gelar",
        "count(*) FILTER (WHERE __nama_bin)                         AS nama_bin",
        "count(*) FILTER (WHERE __nik_excel)                       AS excel",
        "count(DISTINCT __nik_clean) FILTER (WHERE __nik_dobel)    AS grup_dobel",
        "count(*) FILTER (WHERE is_anomaly)                        AS anomali",
    ]
    baris = con.execute(f"SELECT {', '.join(pilih)} FROM anomali_df").fetchone()
    nama = [d[0] for d in con.description]
    m = dict(zip(nama, baris))

    ada = {e: e in peta for e in ENAM_ELEMEN}
    rate = {e: (m[f"isi_{e}"] / total if ada[e] else 0.0) for e in ENAM_ELEMEN}

    grade = _tentukan_grade(baca_kriteria(con), ada, rate, m, total,
                            s["wilayah_ada"])
    pita = _ambil_pita(con, grade)

    # Mutu menerus: seberapa baik berkas ini DI DALAM kelasnya sendiri.
    hadir = [e for e in ENAM_ELEMEN if ada[e]]
    kelengkapan = sum(rate[e] for e in hadir) / len(hadir) if hadir else 0.0
    if ada["nik"]:
        mutu = 0.6 * kelengkapan + 0.4 * (m["trusted"] / total)
    else:
        mutu = kelengkapan

    skor = round(pita["score_min"] + (pita["score_max"] - pita["score_min"]) * mutu)

    ambigu = _tanggal_ambigu(con, peta.get("tanggal_lahir"),
                             (s.get("normalisasi") or {}).get("tanggal"))

    print(f"[G4] grade {HURUF[grade]} ({grade}) — skor {skor} "
          f"[pita {pita['score_min']}-{pita['score_max']}, mutu {mutu:.3f}]")
    print(f"[G4] trusted {m['trusted']:,} / {total:,}, anomali {m['anomali']:,}")

    return {
        **s,
        "grade": grade,
        "quality_score": skor,
        "pita": pita,
        "metrik": m,
        "rate": rate,
        "ada": ada,
        "case_flags": {
            "hasExcelScientificNik": bool(m["excel"]),
            "hasAmbiguousDateFormats": ambigu,
        },
    }


def _cocok_kriteria(k: dict, ada: dict, rate: dict, trusted_rate: float) -> bool:
    """Apakah satu baris `grade_criteria` terpenuhi oleh berkas ini?"""
    # Syarat KEBERADAAN kolom NIK — terpisah dari ambang kelengkapannya.
    # Inilah yang memisahkan A/B dari C/D, dan tidak bisa diwakili angka.
    if k["nik_kolom"] == "wajib" and not ada["nik"]:
        return False
    if k["nik_kolom"] == "terlarang" and ada["nik"]:
        return False

    # NULL = tidak diperiksa. Ambang di atas 0 sekaligus mensyaratkan kolomnya
    # ada, karena kolom yang tidak ada selalu berkelengkapan 0.
    for elemen in ENAM_ELEMEN:
        minimum = k.get(f"min_{elemen}")
        if minimum is not None and rate[elemen] < minimum:
            return False

    minimum = k.get("min_nik_trusted")
    if minimum is not None and trusted_rate < minimum:
        return False

    return True


def _tentukan_grade(kriteria: list[dict], ada, rate, m, total, wilayah_ada) -> int:
    """
    Aturan grade A-F.

    A-D DIBACA DARI TABEL `grade_criteria`, bukan dari kode. Keempatnya berbagi
    bentuk aturan yang identik — syarat kolom NIK, ambang kelengkapan per
    elemen, ambang mutu NIK — jadi yang membedakannya hanya angka, dan angka
    itu bisa disetel lewat API tanpa deploy ulang.

    E dan F tetap di sini. Grade E berbentuk KOMBINASI kolom yang harus ada,
    bukan ambang persentase; grade F bukan aturan melainkan hasil "tidak satu
    pun di atas terpenuhi".

    Dua perbedaan yang disengaja dari GraderService lama tetap berlaku, dan
    keduanya membuat penilaian lebih ketat, bukan lebih longgar.

    1. KELENGKAPAN memakai "terisi" — NULL dan string kosong sama-sama
       dianggap kosong. Versi lama hanya menghitung NULL, padahal CSV yang
       dikonversi ke parquet menyimpan sel kosong sebagai string kosong,
       sehingga sel kosong terhitung terisi.

    2. MUTU NIK memakai `trusted`, bukan sekadar panjang 16 digit. Versi lama
       tidak punya konsep kepercayaan, jadi panjang adalah satu-satunya ukuran
       yang tersedia baginya.

       Ini bukan kerapian belaka. Berkas yang NIK-nya rusak jadi notasi ilmiah
       Excel akan lolos ukuran panjang — 3.20102E+15 dikembangkan menjadi tepat
       16 digit — padahal digit belakangnya sudah hilang dan tidak satu pun
       NIK-nya bisa dipercaya. Dengan ukuran panjang, berkas seperti itu
       dinilai GRADE A. Spesifikasi bagian 7 memang menuntut lebih: grade A
       mensyaratkan tidak ada duplikasi maupun inkonsistensi jenis kelamin dan
       tanggal lahir, dan grade B mensyaratkan isu NIK di bawah 30%.
    """
    if not kriteria:
        raise RuntimeError(
            "Tabel grade_criteria kosong — tidak ada satu pun kriteria aktif, "
            "sehingga setiap berkas akan jatuh ke E atau F. "
            "Jalankan: python infra/apply_schema.py"
        )

    trusted_rate = (m["trusted"] / total) if total else 0.0

    # Yang PERTAMA cocok menang, menurut kolom `urutan`.
    for k in kriteria:
        if _cocok_kriteria(k, ada, rate, trusted_rate):
            return k["grade_id"]

    if ada["nama"]:
        e1 = ada["tanggal_lahir"] and ada["jenis_kelamin"]
        e2 = ada["tempat_lahir"] and ada["tanggal_lahir"]
        e3 = ada["tempat_lahir"] and ada["nama_ibu"]
        e4 = ada["tanggal_lahir"] and bool(wilayah_ada)
        if e1 or e2 or e3 or e4:
            return 5  # E

    return 6  # F


def _ambil_pita(con, grade: int) -> dict:
    baris = con.execute(f"""
        SELECT score_min, score_max, severity_label, can_proceed, criteria_description
          FROM pg.grade_bands WHERE grade_id = {grade}
    """).fetchall()
    if not baris:
        raise RuntimeError(
            f"grade_bands belum terisi untuk grade {grade}. "
            "Jalankan: python infra/apply_schema.py"
        )
    kunci = ["score_min", "score_max", "severity_label", "can_proceed",
             "criteria_description"]
    return dict(zip(kunci, baris[0]))


def _tanggal_ambigu(con, kol: str | None, info: dict | None = None) -> bool:
    """
    Benar kalau ada tanggal di berkas ini yang urutannya tidak bisa dipastikan.

    DUA SEBAB, dan keduanya harus diperiksa.

    1. SELURUH berkas ambigu: tidak satu pun barisnya berhari di atas 12, jadi
       tidak ada yang membuktikan DD/MM maupun MM/DD. Begitu ada satu baris
       seperti 31/03/1990, keraguan itu hilang untuk seluruh berkas.

    2. Berkas MENCAMPUR dua urutan: ada baris yang hanya masuk akal sebagai
       DD-MM dan ada yang hanya masuk akal sebagai MM-DD. Di berkas semacam
       itu, baris yang kedua bagiannya sama-sama <= 12 mustahil dipastikan —
       ia dibaca mengikuti mayoritas berkas, dan sebagian pasti keliru.

    Sebab kedua sempat terlewat. Berkas uji yang sengaja mencampur tujuh bentuk
    tanggal punya hari sampai 31, sehingga lolos pemeriksaan pertama dan
    dilaporkan TIDAK ambigu — padahal 78 barisnya memang tidak bisa dipastikan.
    """
    if not kol:
        return False

    if info and info.get("bukti_dmy") and info.get("bukti_mdy"):
        return True

    baris = con.execute(
        "SELECT max(day(__tgl)) FROM anomali_df WHERE __tgl IS NOT NULL"
    ).fetchone()[0]
    return baris is not None and baris <= 12


# ── Tahap 6: tulis enriched parquet ────────────────────────────────────────

def tulis_enriched(s: dict) -> dict:
    """
    View `enriched_df` (kolom asli + 8 kolom derivasi) lalu COPY ke SeaweedFS.

    Kolom kerja `__*` dibuang di sini — berkas hasil hanya memuat apa yang
    dijanjikan spesifikasi. Kolom asli yang namanya bentrok dengan kolom
    derivasi juga dibuang, supaya parquet tidak punya nama ganda.
    """
    con, peta = s["con"], s["peta"]
    derivasi = {k.lower() for k in KOLOM_DERIVASI}
    asli = [k for k in s["kolom"] if k.lower() not in derivasi]

    if len(asli) < len(s["kolom"]):
        dibuang = [k for k in s["kolom"] if k.lower() in derivasi]
        print(f"[G5] kolom asli ditimpa karena bentrok nama: {', '.join(dibuang)}")

    nik_ada = bool(peta.get("nik"))
    pilih = [_kutip(k) for k in asli] + [
        ("__nik_clean AS nik_clean" if nik_ada else "CAST(NULL AS VARCHAR) AS nik_clean"),
        ("__nik_prov  AS nik_prov" if nik_ada else "CAST(NULL AS VARCHAR) AS nik_prov"),
        ("CASE WHEN __nik_len_ok THEN __nik_hari  END AS nik_hari"),
        ("CASE WHEN __nik_len_ok THEN __nik_bulan END AS nik_bulan"),
        ("CASE WHEN __nik_len_ok THEN __nik_tahun END AS nik_tahun"),
        "nik_trusted",
        "is_anomaly",
        "anomaly_notes",
        "nama_clean",
    ]

    con.execute(f"CREATE OR REPLACE VIEW enriched_df AS "
                f"SELECT {', '.join(pilih)} FROM anomali_df")

    tujuan = f"s3://{s['bucket']}/{s['enriched_key']}"
    con.execute(f"COPY (SELECT * FROM enriched_df) TO '{tujuan}' (FORMAT PARQUET)")

    ukuran = _ukuran_parquet(con, tujuan)
    print(f"[G5] ditulis: {tujuan}" + (f" (~{ukuran:,} byte)" if ukuran else ""))

    return {**s, "enriched_path": tujuan, "parquet_size_bytes": ukuran}


def _ukuran_parquet(con, jalur: str) -> int | None:
    """
    Ukuran dari metadata parquet — penjumlahan column chunk terkompresi.

    Ini sedikit lebih kecil dari ukuran objek S3 sebenarnya (header dan footer
    tidak terhitung). Dipakai karena membaca ukuran objek menuntut permintaan S3
    tersendiri, sementara metadata sudah ikut terbaca.
    """
    try:
        n = con.execute(
            f"SELECT sum(total_compressed_size) FROM parquet_metadata('{jalur}')"
        ).fetchone()[0]
        return int(n) if n is not None else None
    except Exception as e:  # noqa: BLE001
        print(f"[G5] ukuran parquet tidak terbaca: {e}")
        return None


# ── Tahap 7: susun muatan callback ─────────────────────────────────────────

def _ringkas_normalisasi(s: dict) -> dict:
    """Bagian `normalization` pada muatan callback."""
    n = s.get("normalisasi") or {}
    tgl = n.get("tanggal") or {}
    jejak = n.get("jejak") or []

    return {
        # Berapa kolom dikenali tiap lapis — cara tercepat melihat apakah
        # berkas ini butuh pertolongan AI atau tidak.
        "byLayer": {
            f"layer{l}": sum(1 for j in jejak
                             if j.get("lapis") == l and j.get("elemen"))
            for l in (1, 2, 3, 4, 5)
        },
        "renamed": [
            {"from": j["kolom"], "to": j["elemen"],
             "layer": j["lapis"], "reason": j["dasar"]}
            for j in jejak if j.get("elemen") and j["kolom"] != j["elemen"]
        ],
        "unrecognisedColumns": n.get("kolom_dibawa_apa_adanya", []),
        "dateNormalisation": {
            "convention": tgl.get("konvensi"),
            "outputFormat": "dd-mm-yyyy",
            "evidenceDayMonth": tgl.get("bukti_dmy"),
            "evidenceMonthDay": tgl.get("bukti_mdy"),
            "ambiguousRows": tgl.get("ambigu"),
            "hasMonthNames": tgl.get("ada_nama_bulan"),
            "note": tgl.get("dasar"),
        } if tgl else None,
        "aiUsed": any(j.get("lapis") == 5 and j.get("model") for j in jejak),
        "trace": jejak,
    }


def susun_hasil(s: dict) -> dict:
    """Muatan persis seperti GradingCallbackPayload pada spesifikasi bagian 4.1."""
    m, peta, total = s["metrik"], s["peta"], s["row_count"]
    grade, pita = s["grade"], s["pita"]
    durasi = int((time.perf_counter() - s["mulai"]) * 1000)

    # Alasan pemblokiran menyebut sebab yang SEBENARNYA, bukan ambang skor.
    # Satu-satunya grade yang tidak boleh lanjut adalah F, dan penyebabnya bukan
    # mutu data melainkan kolom yang tidak dikenali — matching belum punya
    # pemetaan untuk dipakai. Menyalahkan skor di situ menyesatkan: berkas F bisa
    # saja isinya rapi, hanya nama kolomnya yang tidak standar.
    alasan = None
    if not pita["can_proceed"]:
        dikenali = [e for e in ENAM_ELEMEN if e in peta]
        alasan = (
            f"Grade {HURUF[grade]}: kolom berkas tidak dapat dipetakan ke elemen "
            f"kependudukan yang dibutuhkan "
            f"({len(dikenali)} dari 6 elemen dikenali"
            + (f": {', '.join(dikenali)}" if dikenali else "")
            + "). Berkas memerlukan pemetaan kolom kustom sebelum dapat dicocokkan."
        )

    return {
        "fileId": s["file_id"],
        "status": "COMPLETED",
        "enrichedParquetKey": s["enriched_key"],
        "parquetSizeBytes": s.get("parquet_size_bytes"),
        "gradingDurationMs": durasi,
        "summary": {
            "grade": grade,
            "gradeLetter": HURUF[grade],
            "qualityScore": s["quality_score"],
            "severityLabel": pita["severity_label"],
            "canProceedToSync": bool(pita["can_proceed"]),
            "blockedReason": alasan,
            "recordCount": total,
            "anomalyCount": m["anomali"],
            "criteriaDescription": pita["criteria_description"],
        },
        "anomalyMetrics": {
            "totalAnomalies": m["anomali"],
            "trustedNikCount": m["trusted"],
            "untrustedNikCount": (total - m["trusted"]) if peta.get("nik") else 0,
            "duplicateNikCount": m["dobel"],
            "duplicateNikGroupsCount": m["grup_dobel"],
            "nikDobMismatchCount": m["beda_tgl"],
            "nikGenderMismatchCount": m["beda_jk"],
            "nikProvinceInvalidCount": m["prov_salah"],
            # TAMBAHAN di luar spesifikasi bagian 4.1. `nikProvinceInvalidCount`
            # SENGAJA dipertahankan — portal sudah memakainya — dan yang di
            # bawah ini melengkapinya, bukan menggantikannya.
            "nikKecamatanInvalidCount": m["kec_salah"],
            "nameWithTitleCount": m["nama_gelar"],
            "nameWithPatronymCount": m["nama_bin"],
        },
        "referenceData": {
            "wilayahSource": (s.get("wilayah") or {}).get("sumber"),
            "wilayahAvailable": (s.get("wilayah") or {}).get("tersedia"),
            "wilayahKecamatanCount": (s.get("wilayah") or {}).get("kecamatan"),
        },
        "elementDetails": {
            # Kolom SESUDAH normalisasi — inilah yang ada di enriched.parquet.
            "columns": s["kolom"],
            "columnCount": len(s["kolom"]),
            "originalColumns": s.get("kolom_asli", s["kolom"]),
            # Di luar spesifikasi, tapi menjawab pertanyaan yang selalu muncul
            # saat grade tak sesuai dugaan: elemen mana yang dikenali mesin,
            # dan dari kolom asli yang mana.
            "recognisedElements": {e: (s.get("peta_asli") or {}).get(e)
                                   for e in ENAM_ELEMEN},
            "completeness": {e: round(s["rate"][e], 4) for e in ENAM_ELEMEN},
        },
        # Jejak normalisasi: lapis mana yang mengenali kolom apa, atas dasar
        # apa, dan bagaimana urutan tanggal diputuskan. Tanpa ini, grade yang
        # mengejutkan hampir mustahil ditelusuri.
        "normalization": _ringkas_normalisasi(s),
        "caseFlags": s["case_flags"],
    }


# ── Rangkaian penuh ────────────────────────────────────────────────────────

def jalankan_penuh(job: dict, lapor=None) -> dict:
    """
    Enam tahap berurutan, dari parquet mentah sampai muatan callback.

    Dipakai oleh dua pemanggil: thread latar belakang milik node dispatch, dan
    `run_grading_local.py`. Node-node di kanvas memanggil tahapannya satu per
    satu lewat fungsi di atas, jadi keduanya berbagi jalur kode yang sama.
    """
    def kabar(tahap: str):
        print(f"\n--- {tahap} ---")
        if lapor:
            lapor(tahap)

    kabar("G1 open session")
    s = buka(job)
    try:
        kabar("G2 load raw parquet")
        s = muat_raw(s)
        kabar("G3 clean NIK & flag anomalies")
        s = bersihkan_dan_tandai(s)
        kabar("G4 score & grade")
        s = skor_dan_grade(s)
        kabar("G5 write enriched parquet")
        s = tulis_enriched(s)
        kabar("G6 build callback payload")
        hasil = susun_hasil(s)
        return {"sesi": s, "hasil": hasil}
    finally:
        try:
            s["con"].close()
        except Exception:  # noqa: BLE001
            pass


def ringkas(hasil: dict) -> str:
    return json.dumps(hasil, ensure_ascii=False, indent=2)
