"""
Pipeline matching sesuai spesifikasi integrasi portal (versi 27 Sep 2026).

    payload dispatch
      -> incoming (S3) + master (S3)
      -> Pass 1: NIK tepercaya + nama PERSIS
      -> Pass 2: nama + tanggal lahir + nama ibu PERSIS
      -> Pass 3: blocking & skor milik grade berkas, untuk sisanya
      -> klasifikasi AUTO / REVIEW / UNMATCH / CONFLICT, pola review, snapshot
      -> reasoning per baris (_reasoning.py)
      -> matching-results/{jobId}/result.parquet (S3)
      -> suntik ke DB PORTAL: syncrono_matching_result + syncrono_matching_job
      -> callback ke portal

PER BARIS, BUKAN PER BERKAS

Engine lama memilih SATU jalur untuk seluruh berkas menurut grade-nya. Di sini
setiap baris melewati pass berurutan, dan yang sudah ketemu tidak lanjut. Grade
tidak hilang — ia pindah jadi "blocking dan rumus skor mana untuk Pass 3".

Diukur pada 1 juta baris (lima berkas uji x 200 ribu) terhadap cara lama:
tidak SATU baris pun berpindah ke orang yang berbeda. Grade A, C, D, E hasilnya
sama, hanya berlabel; grade B berubah di dua tempat, dan dua-duanya benar:

    17.218 REVIEW -> AUTO   NIK + nama persis; tempat/ibu KOSONG, tidak ada
                            satu pun yang bertentangan dengan master
    23.922 UNMATCH -> AUTO  23.921 NIK-nya tidak ada di master sama sekali —
                            Pass 2 memulihkan orang yang benar dari NIK sampah

DUA ATURAN PENGAMAN

Keduanya tidak mengubah apa pun pada data uji, dan keduanya menutup kasus yang
berbahaya pada data sungguhan:

  1. Pass 2 menemukan orang X lewat identitas, padahal NIK berkas itu milik
     ORANG LAIN di master -> REVIEW + NIK_CONFLICT, bukan AUTO. Barisnya bisa X
     (menurut identitas) atau pemilik NIK itu (menurut NIK) — manusia yang harus
     memutuskan. Pada data uji: 1 baris dari 200 ribu.

  2. Pass 1/2 menemukan kecocokan, tapi atribut lain yang TERISI di kedua sisi
     BERTENTANGAN -> tidak dianggap deterministik, turun ke Pass 3. Kosong tidak
     dihitung bertentangan: yang dicegah hanya bukti yang saling membantah.

UNMATCH TIDAK MEMBAWA KANDIDAT

`master_nik` dan `master_snapshot` NULL untuk UNMATCH (spesifikasi §4.1), meski
Pass 3 sempat menemukan kandidat terdekat. Skornya tetap disimpan, dan
reasoning menyebutnya — tanpa menunjuk orang yang justru ditolak.

REASONING TIDAK BISA MENGGAGALKAN MATCHING

Kolom `reasoning` opsional di spesifikasi. Galat di tahap itu hanya membuat
kolomnya kosong (`_isi_reasoning`); hasil matching tetap tersuntik.

YANG SENGAJA BELUM DI SINI

  * `rulePreset` diterima dan dicatat, tapi belum berpengaruh: spesifikasi
    menyebut nilainya (FAST/BALANCED/THOROUGH/STRICT) tanpa mendefinisikan
    artinya. Menebak artinya lebih berbahaya daripada mengabaikannya.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

import _reasoning
from _config import HURUF, aturan_matching, rekam_versi
from _grading import pasang_endpoint_s3
from _jobs import CALLBACK_PERCOBAAN, CALLBACK_TIMEOUT, q
from _nama import sql_bersih
from _shared import (BOBOT_BAWAAN, MAKS_KANDIDAT, SQL_KLASIFIKASI, buka_koneksi,
                     kolom_kurang, sql_missing, sql_skor, sql_view_incoming,
                     sql_view_master)

# DB PORTAL tempat hasil disuntikkan (opsi B). Terpisah dari PG_DSN milik
# engine, dan sengaja TIDAK punya nilai bawaan: menyuntik ke database yang
# salah jauh lebih buruk daripada gagal dengan pesan yang jelas.
PORTAL_PG_DSN = os.getenv("PORTAL_PG_DSN", "").strip()

# Ambang, bobot, elemen kosong, pembersihan nama, blocking, selisih seri
# (`matching.conflictEpsilon`), dan ambang nama ibu bertentangan
# (`matching.contradictionJw`) semuanya KONFIGURASI — dibaca sekali di awal
# job lewat `_config.aturan_matching()`. Dua yang terakhir dulu hanya env
# (MATCHING_CONFLICT_EPSILON, MATCHING_KONTRA_JW); env itu kini jadi nilai
# bawaan selama belum ada setelan di tabel engine_config.

# Ambang pola review.
AMBANG_NAMA_BEDA_TOTAL = 0.70     # NIK cocok, nama di bawah ini -> NIK_CONFLICT
AMBANG_SALAH_EJA = 0.85           # nama di atas ini -> SPELLING_NAME

AKTOR_ENGINE = "DataScienceMatchingEngine"


# ── Muatan ──────────────────────────────────────────────────────────────────

def susun_job(muatan: dict) -> dict:
    """
    Payload dispatch (spesifikasi §3.2) -> job internal.

    Semua field bertanda **Ya** di §3.2 benar-benar diwajibkan, dan semua yang
    kurang dilaporkan SEKALIGUS — bukan satu per satu, yang memaksa portal
    mencoba ulang berkali-kali untuk menemukan semuanya.
    """
    masuk = muatan.get("incomingFile") or {}
    master = muatan.get("masterDataFile") or {}
    job = {
        "job_id": str(muatan.get("jobId") or "").strip(),
        "file_id": str(muatan.get("fileId") or "").strip(),
        "master_file_id": str(muatan.get("masterFileId") or "").strip(),
        "actor": str(muatan.get("actor") or "").strip(),
        "callback_url": str(muatan.get("callbackUrl") or "").strip(),
        "s3_bucket": str(muatan.get("s3Bucket") or "").strip(),
        "s3_endpoint": str(muatan.get("s3Endpoint") or "").strip(),
        "incoming_key": str(masuk.get("s3Key") or "").strip(),
        "master_key": str(master.get("s3Key") or "").strip(),
        "rule_preset": str(muatan.get("rulePreset") or "").strip() or None,
        # Di luar spesifikasi, opsional: menimpa grade hasil pencarian. Hanya
        # untuk pengujian — lihat `cari_grade`.
        "grade": muatan.get("grade"),
    }
    wajib = {"jobId": "job_id", "fileId": "file_id", "masterFileId": "master_file_id",
             "actor": "actor", "callbackUrl": "callback_url", "s3Bucket": "s3_bucket",
             "incomingFile.s3Key": "incoming_key", "masterDataFile.s3Key": "master_key"}
    kurang = [nama for nama, kunci in wajib.items() if not job[kunci]]
    if kurang:
        raise ValueError(f"Field wajib tidak ada: {', '.join(kurang)}")
    return job


# ── DB portal ───────────────────────────────────────────────────────────────

def _pasang_portal(con) -> None:
    if not PORTAL_PG_DSN:
        raise RuntimeError(
            "PORTAL_PG_DSN belum diisi. Hasil matching disuntikkan ke database "
            "PORTAL (tabel syncrono_matching_result & syncrono_matching_job), "
            "dan alamatnya sengaja tidak punya nilai bawaan — menyuntik ke "
            "database yang salah jauh lebih buruk daripada gagal di sini.")
    con.execute(f"ATTACH {q(PORTAL_PG_DSN)} AS portal (TYPE postgres)")


def _status_portal(con, job_id: str) -> str | None:
    r = con.execute(
        f"SELECT status FROM portal.syncrono_matching_job WHERE id = {q(job_id)}"
    ).fetchone()
    return r[0] if r else None


def _pg(con, sql: str) -> None:
    """
    Jalankan SQL PostgreSQL ASLI di DB portal.

    Bukan lewat UPDATE/DELETE DuckDB, dan ini ditemukan dengan menjalankannya:
    DuckDB menerjemahkan UPDATE ke tabel sementara di PostgreSQL yang kolomnya
    TEXT, lalu menyalinnya ke kolom sasaran. Untuk kolom JSONB hasilnya

        column "stage_durations" is of type jsonb but expression is of type
        character varying

    dan CAST di sisi DuckDB tidak menolong — ia hilang di tabel sementara itu.
    Lewat `postgres_execute`, PostgreSQL sendiri yang mengurai `::jsonb`.

    Sudah diuji: `postgres_execute` IKUT transaksi DuckDB yang sedang berjalan
    — ROLLBACK membatalkannya — jadi atomisitas penyuntikan tetap utuh.
    """
    con.execute(f"CALL postgres_execute('portal', {q(sql)})")


def _tandai(con, job: dict, **kolom) -> None:
    """UPDATE kecil pada baris job portal — di luar transaksi penyuntikan."""
    isi = ", ".join(f"{k} = {'now()' if v == 'now()' else q(v)}"
                    for k, v in kolom.items())
    _pg(con, f"UPDATE syncrono_matching_job SET {isi}, updated_at = now(), "
             f"updated_by = {q(AKTOR_ENGINE)} WHERE id = {q(job['job_id'])}")


class Dibatalkan(Exception):
    """Operator menekan Batalkan di portal (spesifikasi §8.2)."""


def _cek_batal(con, job: dict) -> None:
    if _status_portal(con, job["job_id"]) == "CANCELLED":
        raise Dibatalkan(job["job_id"])


# ── Grade ───────────────────────────────────────────────────────────────────

def cari_grade(con, job: dict) -> int:
    """
    Grade berkas incoming — dari hasil grading ENGINE INI SENDIRI.

    Portal tidak mengirim grade (spesifikasi versi 27 Sep tidak menambahkannya),
    dan memang tidak perlu: engine inilah yang menghitungnya saat grading, dan
    menyimpannya di `grading_jobs.result.summary.grade`. Grade dari sumber lain
    — misalnya diketik ulang portal — hanya membuka peluang keduanya berbeda.
    """
    if job.get("grade") not in (None, ""):
        print(f"[M] grade DITIMPA muatan: {job['grade']} (hanya untuk pengujian)")
        return int(job["grade"])
    r = con.execute(f"""
        SELECT CAST(json_extract(CAST(result AS VARCHAR), '$.summary.grade') AS INTEGER)
        FROM pg.grading_jobs
        WHERE file_id = {q(job['file_id'])} AND status = 'COMPLETED'
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    if not r or r[0] is None:
        raise ValueError(
            f"Berkas '{job['file_id']}' belum pernah digrading oleh engine ini, "
            f"jadi grade-nya tidak diketahui. Matching memilih blocking dan rumus "
            f"skor menurut grade — jalankan grading lebih dulu.")
    if r[0] not in (1, 2, 3, 4, 5):
        raise ValueError(
            f"Berkas '{job['file_id']}' ber-grade {r[0]}; matching hanya tersedia "
            f"untuk grade 1-5. Grade 6 (F) tidak punya elemen yang cukup untuk "
            f"dicocokkan dengan aman.")
    return int(r[0])


def ringkas_aturan(grade: int, aturan: dict, versi: str) -> dict:
    """
    Aturan yang DIPAKAI job ini — ditulis ke `blocking_metrics` job portal dan
    ikut di callback, supaya hasil bisa dijelaskan walau aturannya berubah kelak.
    Isi lengkapnya bisa diambil lagi lewat GET /api/v1/config/versions/{versi}.
    """
    return {
        "configVersion": versi,
        "grade": grade,
        "gradeLetter": HURUF.get(grade),
        "thresholds": {
            "autoMissingMax": aturan["auto_missing_max"],
            "autoScoreMin": aturan["auto_score_min"],
            "reviewMissingCount": aturan["review_missing_count"],
            "reviewScoreMin": aturan["review_score_min"],
            "reviewScoreMax": aturan["review_score_max"],
        },
        "weights": {f: b for f, b in aturan["bobot"]},
        "missingElements": aturan["elemen_kosong"],
        "nameCleaning": aturan["bersih_nama"],
        "dateMatch": aturan.get("cocok_tanggal"),
        "conflictEpsilon": aturan["epsilon"],
        "contradictionJw": aturan["kontra_jw"],
    }


def _sql_aturan(a: dict) -> str:
    def nilai(v, tipe):
        return f"CAST(NULL AS {tipe})" if v is None else f"{v}::{tipe}"
    return (f"SELECT {nilai(a['auto_missing_max'], 'INTEGER')} AS auto_missing_max, "
            f"{nilai(a['auto_score_min'], 'DOUBLE')} AS auto_score_min, "
            f"{nilai(a['review_missing_count'], 'INTEGER')} AS review_missing_count, "
            f"{nilai(a['review_score_min'], 'DOUBLE')} AS review_score_min, "
            f"{nilai(a['review_score_max'], 'DOUBLE')} AS review_score_max")


# ── Masukan ─────────────────────────────────────────────────────────────────

def muat_masukan(con, sumber: str, master: str, bersih: dict | None = None) -> int:
    """
    Incoming dimaterialkan, master TIDAK.

    `bersih` = sakelar pembersihan nama grade berkas (`nameCleaning`),
    diterapkan sama di kedua sisi — termasuk Pass 1/2 yang membandingkan nama
    persis, dan blocking yang memakai 3 huruf awal nama.

    Incoming kecil dan dibaca berkali-kali (tiga pass, lalu snapshot). Master
    bisa ratusan juta baris; sebagai VIEW, setiap pass MENGALIRKANNYA sebagai
    sisi probe hash join, dengan incoming sebagai sisi build — memorinya
    sebanding incoming, bukan master.

    Keduanya berupa lokasi parquet (`s3://...` saat job berjalan), bukan job:
    seluruh rangkaian pass bisa diuji pada berkas lokal tanpa S3.
    """
    kolom = {r[0].strip().lower() for r in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{sumber}')").fetchall()}

    # `id_incoming` = "ID atau nomor baris" (spesifikasi §4.1). Enriched hasil
    # grading TIDAK memuat kolom `id` — terverifikasi pada berkas uji — jadi
    # tanpa jalan ini seluruh unggahan CSV tanpa kolom id tidak bisa dicocokkan
    # sama sekali. `file_row_number` adalah posisi baris di dalam berkasnya:
    # stabil, tidak bergantung urutan eksekusi seperti row_number() OVER ().
    id_sql = ("trim(CAST(id AS VARCHAR))" if "id" in kolom
              else "CAST(file_row_number + 1 AS VARCHAR)")
    con.execute(f"""
        CREATE OR REPLACE TABLE incoming_semua AS
        SELECT {id_sql} AS id,
               {sql_view_incoming(kolom - {'id'}, bersih)}
        FROM read_parquet('{sumber}', file_row_number = true)
    """)

    dobel = con.execute("""SELECT count(*) - count(DISTINCT id)
                           FROM incoming_semua""").fetchone()[0]
    if dobel:
        raise ValueError(
            f"Kolom id pada berkas incoming tidak unik ({dobel:,} duplikat). "
            f"id_incoming harus menunjuk tepat satu baris.")

    con.execute(f"""CREATE OR REPLACE VIEW master_df AS
                    SELECT {sql_view_master(bersih)} FROM read_parquet('{master}')""")
    return con.execute("SELECT count(*) FROM incoming_semua").fetchone()[0]


# ── Pass ────────────────────────────────────────────────────────────────────

def _sql_bertentangan(tanpa_ibu: bool = False, kontra_jw: float = 0.80) -> str:
    """
    Aturan pengaman 2: atribut yang TERISI di kedua sisi dan saling membantah.

    Tempat lahir sengaja TIDAK ikut. Variasinya terlalu besar untuk jadi bukti
    bantahan: "Bogor" dan "Kab. Bogor" adalah tempat yang sama, dan menghitung
    keduanya bertentangan akan menurunkan kecocokan yang sah ke Pass 3.

    `kontra_jw` = ambang Jaro-Winkler nama ibu (`matching.contradictionJw`):
    hanya di bawah ini yang dihitung membantah; di atasnya perbedaan ejaan biasa.
    """
    syarat = [
        "(i.tanggal_lahir_clean IS NOT NULL AND m.tanggal_lahir_master_clean IS NOT NULL "
        " AND CAST(i.tanggal_lahir_clean AS DATE) <> m.tanggal_lahir_master_clean)",
        "(i.jenis_kelamin_clean IS NOT NULL AND m.jenis_kelamin_master_clean IS NOT NULL "
        " AND i.jenis_kelamin_clean <> m.jenis_kelamin_master_clean)",
    ]
    if not tanpa_ibu:
        syarat.append(
            "(nullif(i.nama_ibu_clean, '') IS NOT NULL "
            " AND nullif(m.nama_ibu_master_clean, '') IS NOT NULL "
            f" AND j(i.nama_ibu_clean, m.nama_ibu_master_clean) < {float(kontra_jw)})")
    return "(" + " OR ".join(syarat) + ")"


def pass1(con, kontra_jw: float = 0.80) -> None:
    """
    NIK tepercaya + nama persis.

    `nik_cocok` menyimpan SEMUA pasangan yang NIK-nya cocok, bukan hanya yang
    namanya juga persis — dari satu pemindaian master yang sama, ia sekaligus
    menjawab "apakah NIK berkas ini milik seseorang di master", yang dibutuhkan
    aturan pengaman 1 di Pass 2 tanpa memindai master sekali lagi.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE nik_cocok AS
        SELECT i.id, m.nik,
               i.nama_clean = m.nama_master_clean AS nama_persis,
               {_sql_bertentangan(kontra_jw=kontra_jw)} AS bertentangan
        FROM incoming_semua i
        JOIN master_df m ON i.nik_trusted AND i.nik = m.nik
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE p1 AS
        SELECT id, count(DISTINCT nik) AS n_kandidat, min(nik) AS nik,
               list_sort(list_distinct(list(nik)))[1:{MAKS_KANDIDAT}] AS calon
        FROM nik_cocok WHERE nama_persis AND NOT bertentangan
        GROUP BY id
    """)


# Urutan kandidat CONFLICT Pass 2: NIK kandidat yang berbeda paling banyak
# sekian digit dari NIK berkas ditaruh di depan.
BEDA_DIGIT_NIK = 2


def _sql_kata(kolom: str) -> str:
    """Kata-kata unik sebuah teks bersih: 'prov. aceh' -> ['prov', 'aceh']."""
    return (f"list_distinct(list_filter(string_split(regexp_replace("
            f"COALESCE({kolom}, ''), '[^a-z0-9]+', ' ', 'g'), ' '), w -> w <> ''))")


def pass2(con) -> None:
    """
    Nama + tanggal lahir + nama ibu persis, untuk yang belum ketemu.

    Bisa ada lebih dari satu orang di master dengan nama, tanggal lahir, dan
    nama ibu identik: CONFLICT, manusia yang memilih. NIK yang hanya mirip
    TIDAK memecah seri — NIK yang tidak persis sama bukan bukti.

    Kandidat diurutkan dari yang paling dekat dengan berkas — ketiga kunci
    identitasnya sudah sama, jadi hanya atribut sisa yang membedakan:

      1. NIK berbeda <= BEDA_DIGIT_NIK digit dari NIK berkas;
      2. kesamaan KATA tempat lahir: bagian kata yang dimiliki keduanya dari
         yang lebih pendek. "prov. aceh" vs "aceh" = 1, vs "jawa timur" = 0.
         Jaro-Winkler saja menilai awalan "prov."/"kab." sebagai beda besar,
         dan pernah menaruh JAWA TIMUR di atas ACEH untuk "PROV. ACEH";
      3. Jaro-Winkler tempat lahir (salah ketik: "bogr" vs "bogor");
      4. NIK, supaya urutannya sama setiap dijalankan.

    Yang pertama menjadi `master_nik` — portal hanya membandingkan baris dengan
    kandidat itu — dan MAKS_KANDIDAT teratas disimpan untuk reasoning.
    `n_kandidat` tetap jumlah seluruhnya.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE p2 AS
        WITH cocok AS (
            SELECT i.id, m.nik,
                   {_sql_bertentangan(tanpa_ibu=True)} AS bertentangan,
                   -- Aturan pengaman 1: NIK berkas ini ADA di master (tercatat
                   -- di nik_cocok, yang hanya memuat NIK tepercaya) tapi orang
                   -- yang ditemukan lewat identitas BUKAN pemiliknya.
                   (i.id IN (SELECT id FROM nik_cocok) AND m.nik <> i.nik)
                       AS nik_milik_lain,
                   -- CASE, bukan AND: hamming() galat untuk panjang berbeda, dan
                   -- AND tidak menjamin sisi kanannya dilewati.
                   CASE WHEN length(i.nik) = length(m.nik)
                        THEN hamming(i.nik, m.nik) <= {BEDA_DIGIT_NIK}
                        ELSE FALSE END AS nik_dekat,
                   {_sql_kata('i.tempat_lahir_clean')} AS kata_i,
                   {_sql_kata('m.tempat_lahir_master_clean')} AS kata_m,
                   j(i.tempat_lahir_clean, m.tempat_lahir_master_clean) AS mirip_tempat
            FROM incoming_semua i
            JOIN master_df m
              ON  i.nama_clean = m.nama_master_clean
              AND CAST(i.tanggal_lahir_clean AS DATE) = m.tanggal_lahir_master_clean
              AND i.nama_ibu_clean = m.nama_ibu_master_clean
            WHERE i.id NOT IN (SELECT id FROM p1)
              AND nullif(i.nama_clean, '') IS NOT NULL
              AND i.tanggal_lahir_clean IS NOT NULL
              AND nullif(i.nama_ibu_clean, '') IS NOT NULL
        ),
        -- Satu baris per (incoming, NIK): master bisa memuat NIK yang sama dua kali.
        per_nik AS (
            SELECT id, nik, bool_or(nik_dekat) AS dekat,
                   max(CASE WHEN least(len(kata_i), len(kata_m)) = 0 THEN 0.0
                            ELSE len(list_intersect(kata_i, kata_m))
                                 / least(len(kata_i), len(kata_m)) END) AS kata_tempat,
                   max(mirip_tempat) AS mirip_tempat,
                   bool_or(nik_milik_lain) AS nik_milik_lain
            FROM cocok
            WHERE NOT bertentangan
            GROUP BY id, nik
        ),
        urut AS (
            SELECT id, count(*) AS n_kandidat,
                   list(nik ORDER BY dekat DESC, kata_tempat DESC, mirip_tempat DESC, nik)
                       AS calon,
                   bool_or(nik_milik_lain) AS nik_milik_lain
            FROM per_nik GROUP BY id
        )
        SELECT id, n_kandidat,
               calon[1] AS nik,
               calon[1:{MAKS_KANDIDAT}] AS calon,   -- rincian CONFLICT
               nik_milik_lain
        FROM urut
    """)


def pass3(con, grade: int, aturan: dict, kueri: str) -> None:
    """
    Blocking & skor milik grade berkas, HANYA untuk yang belum ketemu.

    Query di `matching_queries` merujuk view `incoming_df`. View itu di sini
    dibatasi ke baris yang tersisa, jadi query-nya berjalan tanpa diubah satu
    huruf pun — ia tidak tahu dirinya sedang jadi pass ketiga.

    Pemenang dipilih dengan AGREGASI (`arg_min(..., n)`), bukan window
    function: memorinya n kandidat per baris incoming, berapa pun jumlah
    pasangannya. Yang teratas sekaligus memberi deteksi seri tanpa pemindaian
    kedua. NIK yang muncul lebih dari sekali (baris master ganda) disisakan
    satu dulu — itu bukan konflik. Semua yang skornya dalam `epsilon` dari skor
    tertinggi adalah kandidat seri: dua atau lebih = CONFLICT. Seri dihitung
    dari 2 x MAKS_KANDIDAT teratas, jadi `n_seri` paling besar sebanyak itu.

    Bobot (`bobot`), elemen kosong (`elemen_kosong`), dan selisih seri
    (`epsilon`) dari konfigurasi grade; yang tidak ada memakai bawaan.
    """
    skor = sql_skor(grade, aturan.get("bobot"), aturan.get("cocok_tanggal") or "similarity")
    kosong = sql_missing(grade, aturan.get("elemen_kosong"))
    eps = float(aturan.get("epsilon", 0.0))
    con.execute("""
        CREATE OR REPLACE VIEW incoming_df AS
        SELECT * FROM incoming_semua
        WHERE id NOT IN (SELECT id FROM p1) AND id NOT IN (SELECT id FROM p2)
    """)
    con.execute(f"CREATE OR REPLACE VIEW joined_df AS {kueri}")

    # Skor dibaca dari KELUARAN kueri blocking. API config menolak bobot pada
    # elemen yang tidak dikeluarkannya; penjaga ini untuk konfigurasi yang
    # disunting langsung di basis data — pesan yang jelas, bukan BinderException.
    ada = {r[0] for r in con.execute("DESCRIBE joined_df").fetchall()}
    bobot = aturan.get("bobot")
    kurang = kolom_kurang(ada, BOBOT_BAWAAN[grade] if bobot is None else bobot,
                          aturan.get("elemen_kosong"))
    if kurang:
        raise ValueError(
            f"Konfigurasi grade {grade} memakai elemen yang tidak dikeluarkan kueri "
            f"blocking grade itu: {kurang}. Ubah bobot/elemen kosong lewat API config, "
            f"atau tambahkan kolomnya ke matching_queries (lihat migrasi 007).")
    con.execute(f"""
        CREATE OR REPLACE TABLE p3 AS
        WITH skor AS (
            SELECT incoming_row_id AS id, nik_master,
                   CASE WHEN nik_master IS NULL THEN 0.0 ELSE {skor} END AS skor,
                   CASE WHEN nik_master IS NULL THEN 0 ELSE {kosong} END
                       AS missing_count
            FROM joined_df
        ),
        agg AS (
            SELECT id,
                   arg_min({{'nik': nik_master, 'skor': skor, 'miss': missing_count}},
                           {{'a': -skor, 'b': nik_master}}, {2 * MAKS_KANDIDAT}) AS top,
                   count(nik_master) AS n_kandidat
            FROM skor GROUP BY id
        ),
        unik AS (
            -- Kemunculan pertama tiap NIK; urutannya (skor turun, NIK) tetap.
            SELECT id, n_kandidat,
                   list_filter(top, (x, i) -> NOT list_contains(
                       list_transform(top[1:i - 1], y -> y.nik), x.nik)) AS top
            FROM agg
        ),
        s AS (
            SELECT id, n_kandidat,
                   top[1].nik AS nik, top[1].skor AS skor, top[1].miss AS missing_count,
                   list_filter(top, x -> x.nik IS NOT NULL
                                         AND top[1].skor - x.skor <= {eps}) AS seri_daftar
            FROM unik
        )
        SELECT s.id, s.n_kandidat, s.nik, s.skor,
               len(s.seri_daftar) > 1 AS seri,
               len(s.seri_daftar) AS n_seri,
               s.seri_daftar[1:{MAKS_KANDIDAT}] AS calon,
               CASE WHEN s.nik IS NULL THEN 3 ELSE {SQL_KLASIFIKASI} END AS kelas
        FROM s CROSS JOIN ({_sql_aturan(aturan)}) r
    """)


def gabung(con) -> None:
    """
    Satu baris keputusan untuk SETIAP baris incoming — tidak lebih, tidak kurang.

    UNMATCH tidak membawa `master_nik` (spesifikasi §4.1: "NULL jika
    UNMATCH"), meski Pass 3 sempat menemukan kandidat terdekat — kandidat itu
    justru yang DITOLAK, dan portal yang menampilkannya akan menunjuk orang
    yang salah. Skornya tetap disimpan. `rank_conflict` pun FALSE untuk
    UNMATCH: dua kandidat yang seri di bawah ambang tetap sama-sama ditolak.

    Khusus CONFLICT: `n_seri` = jumlah kandidat yang seri, `kandidat` =
    MAKS_KANDIDAT teratas di antaranya (NIK + skor), urut seperti yang
    ditampilkan — yang pertama adalah `master_nik`. Bahan reasoning.
    """
    tipe = "STRUCT(nik VARCHAR, skor DOUBLE)[]"
    con.execute(f"""
        CREATE OR REPLACE TABLE keputusan AS
        SELECT id, nik AS master_nik, 100.0 AS skor,
               CASE WHEN n_kandidat > 1 THEN 'CONFLICT' ELSE 'AUTO' END AS status,
               'PASS1_NIK_NAMA' AS method, n_kandidat > 1 AS rank_conflict,
               n_kandidat, FALSE AS nik_milik_lain,
               CASE WHEN n_kandidat > 1 THEN n_kandidat END AS n_seri,
               CASE WHEN n_kandidat > 1 THEN CAST(list_transform(
                   calon, x -> {{'nik': x, 'skor': 100.0}}) AS {tipe}) END AS kandidat
        FROM p1
        UNION ALL
        SELECT id, nik, 100.0,
               CASE WHEN n_kandidat > 1 THEN 'CONFLICT'
                    WHEN nik_milik_lain THEN 'REVIEW' ELSE 'AUTO' END,
               'PASS2_NAMA_TGL_IBU', n_kandidat > 1, n_kandidat, nik_milik_lain,
               CASE WHEN n_kandidat > 1 THEN n_kandidat END,
               CASE WHEN n_kandidat > 1 THEN CAST(list_transform(
                   calon, x -> {{'nik': x, 'skor': 100.0}}) AS {tipe}) END
        FROM p2
        UNION ALL
        SELECT id,
               CASE WHEN tolak THEN NULL ELSE nik END,
               round(skor, 2),
               CASE WHEN tolak THEN 'UNMATCH'
                    WHEN seri THEN 'CONFLICT'
                    WHEN kelas = 1 THEN 'AUTO' ELSE 'REVIEW' END,
               'SCORING', seri AND NOT tolak, n_kandidat, FALSE,
               CASE WHEN seri AND NOT tolak THEN n_seri END,
               CASE WHEN seri AND NOT tolak THEN CAST(list_transform(
                   calon, x -> {{'nik': x.nik, 'skor': round(x.skor, 2)}}) AS {tipe}) END
        FROM (SELECT *, nik IS NULL OR kelas = 3 AS tolak FROM p3)
        UNION ALL
        -- Jaring pengaman: query grade yang memakai INNER JOIN tidak memancarkan
        -- baris tanpa kandidat sama sekali. Tanpa ini baris itu hilang dari
        -- hasil, dan portal tidak pernah tahu ia pernah ada.
        SELECT i.id, NULL, 0.0, 'UNMATCH', 'SCORING', FALSE, 0, FALSE, NULL, NULL
        FROM incoming_df i WHERE i.id NOT IN (SELECT id FROM p3)
    """)


# ── Keluaran ────────────────────────────────────────────────────────────────

def _sql_tgl(kol_date: str, kol_mentah: str) -> str:
    return (f"COALESCE(strftime(CAST({kol_date} AS DATE), '%Y-%m-%d'), "
            f"CAST({kol_mentah} AS VARCHAR))")


def sql_pola() -> str:
    """
    Pola review (spesifikasi §4.1 `pattern_group`), dari pemenang vs incoming.

    Alias yang dirujuk: `k` (keputusan), `i` (incoming), `m` (master pemenang).
    Urutannya prioritas — pola yang lebih spesifik dan lebih berbahaya lebih
    dulu: NIK yang menunjuk orang lain mengalahkan sekadar salah eja.
    """
    return f"""
        CASE
            WHEN k.nik_milik_lain THEN 'NIK_CONFLICT'
            WHEN i.nik = m.nik
                 AND j(i.nama_clean, m.nama_master_clean) < {AMBANG_NAMA_BEDA_TOTAL}
                THEN 'NIK_CONFLICT'
            WHEN i.nama_clean <> m.nama_master_clean
                 AND {sql_bersih('i.nama')} = {sql_bersih('m.nama_lengkap')}
                THEN 'TITLE_DEGREE'
            WHEN i.tanggal_lahir_clean IS NOT NULL
                 AND m.tanggal_lahir_master_clean IS NOT NULL
                 AND CAST(i.tanggal_lahir_clean AS DATE) <> m.tanggal_lahir_master_clean
                 AND day(i.tanggal_lahir_clean) = month(m.tanggal_lahir_master_clean)
                 AND month(i.tanggal_lahir_clean) = day(m.tanggal_lahir_master_clean)
                THEN 'SWAPPED_DOB'
            WHEN i.nama_clean <> m.nama_master_clean
                 AND j(i.nama_clean, m.nama_master_clean) >= {AMBANG_SALAH_EJA}
                THEN 'SPELLING_NAME'
            ELSE 'GENERAL_REVIEW'
        END"""


def susun_pasangan(con, master: str, bersih: dict | None = None) -> None:
    """
    Setiap keputusan berdampingan dengan data incoming dan master-nya.

    `bersih` harus sama dengan yang dipakai `muat_masukan`, supaya nama bersih
    master yang dibaca reasoning sama dengan yang dibandingkan saat matching.

    Satu tabel yang dibaca DUA tahap — reasoning dan snapshot — supaya
    keduanya melihat pasangan yang sama persis. Master diambil lewat semi-join
    pada NIK pemenang dan kandidat CONFLICT saja: satu pemindaian master dengan
    daftar NIK kecil sebagai filter, bukan join penuh.

    `kand`: rincian kandidat CONFLICT (NIK, skor, nama, tempat & tanggal lahir
    master) sesuai urutan `keputusan.kandidat`, untuk reasoning.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE master_terpilih AS
        -- DISTINCT ON: master bisa memuat NIK yang sama dua kali. Tanpa ini
        -- baris incoming itu berlipat di hasil, dan id_incoming tidak lagi
        -- menunjuk tepat satu baris.
        --
        -- Dibaca dari parquet mentahnya, bukan dari view `master_df`: view itu
        -- hanya membawa `provinsi` dalam bentuk bersih (huruf kecil), sedangkan
        -- snapshot adalah "data asli" (spesifikasi §4.1).
        SELECT DISTINCT ON (nik) {sql_view_master(bersih)}, provinsi AS provinsi_asli
        FROM read_parquet('{master}')
        WHERE nik IN (SELECT master_nik FROM keputusan WHERE master_nik IS NOT NULL
                      UNION ALL
                      SELECT unnest(list_transform(kandidat, x -> x.nik))
                      FROM keputusan WHERE kandidat IS NOT NULL)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE kandidat_rinci AS
        SELECT u.id,
               list({{'nik': u.nik, 'skor': u.skor, 'nama': m.nama_lengkap,
                      'tempat': m.tempat_lahir, 'tgl': m.tanggal_lahir_master_clean}}
                    ORDER BY u.urut) AS kand
        FROM (SELECT k.id, r.urut, k.kandidat[r.urut].nik AS nik,
                     k.kandidat[r.urut].skor AS skor
              FROM keputusan k
              JOIN (SELECT unnest(range(1, {MAKS_KANDIDAT} + 1)) AS urut) r
                ON r.urut <= len(k.kandidat)
              WHERE k.kandidat IS NOT NULL) u
        LEFT JOIN master_terpilih m ON m.nik = u.nik
        GROUP BY u.id
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE pasangan AS
        SELECT k.id, k.master_nik, k.skor, k.status, k.method, k.rank_conflict,
               k.n_kandidat, k.n_seri, kd.kand,
               CASE WHEN k.status IN ('REVIEW', 'CONFLICT') THEN {sql_pola()} END
                   AS pattern_group,
               -- NIK berkas ini milik SESEORANG di master (hanya NIK tepercaya
               -- yang dicari Pass 1, jadi hanya untuk itu jawabannya bermakna).
               i.id IN (SELECT id FROM nik_cocok) AS nik_di_master,
               i.nik AS i_nik, i.nik_trusted AS i_nik_trusted,
               i.nama AS i_nama, i.nama_clean AS i_nama_clean,
               i.tanggal_lahir AS i_tgl_mentah,
               CAST(i.tanggal_lahir_clean AS DATE) AS i_tgl,
               i.jenis_kelamin AS i_jk,
               i.nama_ibu AS i_ibu, i.nama_ibu_clean AS i_ibu_clean,
               i.tempat_lahir AS i_tmp, i.tempat_lahir_clean AS i_tmp_clean,
               i.provinsi AS i_provinsi,
               m.nik AS m_nik,
               m.nama_lengkap AS m_nama, m.nama_master_clean AS m_nama_clean,
               m.tanggal_lahir AS m_tgl_mentah, m.tanggal_lahir_master_clean AS m_tgl,
               m.jenis_kelamin AS m_jk,
               m.nama_ibu AS m_ibu, m.nama_ibu_master_clean AS m_ibu_clean,
               m.tempat_lahir AS m_tmp, m.tempat_lahir_master_clean AS m_tmp_clean,
               m.provinsi_asli AS m_provinsi
        FROM keputusan k
        JOIN incoming_semua i ON i.id = k.id
        LEFT JOIN master_terpilih m ON m.nik = k.master_nik
        LEFT JOIN kandidat_rinci kd ON kd.id = k.id
    """)


def _isi_reasoning(con, job: dict) -> None:
    """
    Tabel `alasan` — selalu ada sesudahnya, kosong kalau reasoning gagal.

    Reasoning opsional di spesifikasi dan boleh bergantung pada LLM; matching
    tidak. Apa pun yang gagal di sini hanya dicatat.
    """
    try:
        info = _reasoning.isi(con, job)
        print(f"[M] {job['job_id']} reasoning: {info}")
    except Exception as e:  # noqa: BLE001
        print(f"[M] {job['job_id']} reasoning GAGAL — kolom reasoning dikosongkan, "
              f"matching tetap berlanjut: {type(e).__name__}: {e}")
        con.execute("CREATE OR REPLACE TABLE alasan (id VARCHAR, reasoning VARCHAR)")


def susun_hasil(con, job: dict) -> None:
    """Tabel 18 kolom persis spesifikasi §4.1, dari `pasangan` + `alasan`."""
    con.execute(f"""
        CREATE OR REPLACE TABLE hasil AS
        SELECT
            -- UUIDv7, bukan v4. Keduanya UUID yang sah (spesifikasi hanya
            -- menuntut "UUID unik per baris"), tapi v4 acak membuat setiap
            -- sisipan mendarat di tempat acak dalam indeks primary key portal.
            -- v7 berurutan waktu, jadi sisipan menumpuk di ujung indeks.
            -- Terukur ke tabel portal tiruan (PK + 2 indeks + FK), 200.020
            -- baris: v4 21,2 detik, v7 15,7 detik. Selisihnya MEMBESAR seiring
            -- tabel tumbuh, karena indeks v4 makin sulit muat di memori.
            CAST(uuidv7() AS VARCHAR)         AS id,
            {q(job['file_id'])}               AS csv_file_id,
            {q(job['master_file_id'])}        AS master_file_id,
            {q(job['job_id'])}                AS job_id,
            p.id                              AS id_incoming,
            p.master_nik,
            CAST(p.skor AS DOUBLE)            AS score,
            p.status,
            p.method,
            p.rank_conflict,
            p.pattern_group,
            a.reasoning,
            CAST(json_object(
                'nama', p.i_nama, 'nik', p.i_nik,
                'tanggal_lahir', {_sql_tgl('p.i_tgl', 'p.i_tgl_mentah')},
                'jenis_kelamin', p.i_jk, 'nama_ibu', p.i_ibu,
                'tempat_lahir', p.i_tmp, 'provinsi', p.i_provinsi
            ) AS VARCHAR)                     AS incoming_snapshot,
            CASE WHEN p.master_nik IS NULL THEN NULL ELSE CAST(json_object(
                'nama_lengkap', p.m_nama, 'nik', p.m_nik,
                'tanggal_lahir', {_sql_tgl('p.m_tgl', 'p.m_tgl_mentah')},
                'jenis_kelamin', p.m_jk, 'nama_ibu', p.m_ibu,
                'tempat_lahir', p.m_tmp, 'provinsi', p.m_provinsi
            ) AS VARCHAR) END                 AS master_snapshot,
            {q(job['actor'])}                 AS created_by,
            {q(job['actor'])}                 AS updated_by,
            CAST(now() AS TIMESTAMP)          AS created_at,
            CAST(now() AS TIMESTAMP)          AS updated_at
        FROM pasangan p
        LEFT JOIN alasan a ON a.id = p.id
    """)


def metrik(con) -> dict:
    r = con.execute("""
        SELECT count(*),
               sum(n_kandidat),
               count(*) FILTER (WHERE method = 'PASS1_NIK_NAMA'),
               count(*) FILTER (WHERE method = 'PASS2_NAMA_TGL_IBU'),
               count(*) FILTER (WHERE method = 'SCORING'),
               count(*) FILTER (WHERE status = 'AUTO'),
               count(*) FILTER (WHERE status = 'REVIEW'),
               count(*) FILTER (WHERE status = 'UNMATCH'),
               count(*) FILTER (WHERE status = 'CONFLICT')
        FROM keputusan""").fetchone()
    total = int(r[0])
    kandidat = int(r[1] or 0)
    return {
        "totalIncoming": total,
        "totalCandidates": kandidat,
        "avgCandidatesPerRow": round(kandidat / total, 2) if total else 0.0,
        "pass1Count": int(r[2]), "pass2Count": int(r[3]), "scoringCount": int(r[4]),
        "autoCount": int(r[5]), "reviewCount": int(r[6]),
        "unmatchCount": int(r[7]), "conflictCount": int(r[8]),
    }


def unggah_parquet(con, job: dict) -> str:
    kunci = f"matching-results/{job['job_id']}/result.parquet"
    con.execute(f"""COPY hasil TO 's3://{job['s3_bucket']}/{kunci}'
                    (FORMAT parquet, COMPRESSION zstd)""")
    return kunci


def _ada_kolom_portal(con, tabel: str, kolom: str) -> bool:
    return bool(con.execute(f"""
        SELECT count(*) FROM duckdb_columns()
         WHERE database_name = 'portal' AND table_name = {q(tabel)}
           AND column_name = {q(kolom)}""").fetchone()[0])


def suntik(con, job: dict, kunci: str, m: dict, durasi: dict, rss_mb: int,
           aturan_dipakai: dict | None = None) -> int:
    """
    DELETE hasil lama, INSERT dari parquet, UPDATE job — dalam SATU transaksi.

    Contoh di spesifikasi §5.1 menjalankan ketiganya sebagai perintah terpisah.
    Kalau proses mati di antara DELETE dan INSERT, portal tertinggal TANPA hasil
    untuk berkas itu, padahal job-nya belum pernah dinyatakan gagal. Di sini
    ketiganya satu transaksi — sudah diuji: INSERT yang gagal membatalkan
    DELETE-nya, dan baris lama tetap utuh.

    Semua nilai lewat `q()`, BUKAN ditempel mentah seperti contoh di
    spesifikasi. `actor` adalah email operator yang dikirim portal; menempelnya
    langsung ke SQL berarti injeksi SQL ke database portal.

    INSERT membaca dari parquet di S3, bukan dari tabel di memori — persis
    alur di spesifikasi, dan sekaligus membuktikan berkas yang terunggah itu
    utuh dan terbaca.
    """
    sumber = f"s3://{job['s3_bucket']}/{kunci}"

    # Aturan yang dipakai -> `blocking_metrics` (spesifikasi: "statistik detail
    # dari rule blocking yang diterapkan"). Hanya kalau kolomnya ada: DB portal
    # yang dibuat sebelum kolom itu masuk spesifikasi tidak boleh membuat
    # seluruh penyuntikan gagal karena catatan tambahan.
    set_aturan = ""
    if aturan_dipakai and _ada_kolom_portal(con, "syncrono_matching_job",
                                            "blocking_metrics"):
        set_aturan = (f"blocking_metrics = "
                      f"{q(json.dumps(aturan_dipakai, ensure_ascii=False))}::jsonb,")

    con.execute("BEGIN TRANSACTION")
    try:
        _pg(con, f"DELETE FROM syncrono_matching_result "
                 f"WHERE csv_file_id = {q(job['file_id'])} "
                 f"AND master_file_id = {q(job['master_file_id'])}")
        con.execute(f"""
            INSERT INTO portal.syncrono_matching_result (
                id, csv_file_id, master_file_id, job_id, id_incoming, master_nik,
                score, status, method, rank_conflict, pattern_group, reasoning,
                incoming_snapshot, master_snapshot,
                created_at, updated_at, created_by, updated_by)
            SELECT id, csv_file_id, master_file_id, job_id, id_incoming, master_nik,
                   score, status, method, rank_conflict, pattern_group, reasoning,
                   -- Cast EKSPLISIT. Kolom portal bertipe JSONB, dan UPDATE lewat
                   -- DuckDB menolak VARCHAR ke JSONB ("column is of type jsonb but
                   -- expression is of type character varying") — terbukti saat
                   -- dijalankan. INSERT kebetulan lolos tanpa cast; mengandalkan
                   -- kebetulan itu untuk satu perintah dan tidak untuk yang lain
                   -- hanya menunggu perilaku DuckDB berubah.
                   CAST(incoming_snapshot AS JSON), CAST(master_snapshot AS JSON),
                   created_at, updated_at, created_by, updated_by
            FROM read_parquet('{sumber}')
        """)
        _pg(con, f"""
            UPDATE syncrono_matching_job SET
                status = 'COMPLETED', current_stage = 'COMPLETED',
                completed_at = now(), last_error = NULL,
                total_incoming = {m['totalIncoming']},
                total_candidates = {m['totalCandidates']},
                avg_candidates_per_row = {m['avgCandidatesPerRow']},
                pass1_count = {m['pass1Count']}, pass2_count = {m['pass2Count']},
                scoring_count = {m['scoringCount']},
                auto_count = {m['autoCount']}, review_count = {m['reviewCount']},
                unmatch_count = {m['unmatchCount']},
                conflict_count = {m['conflictCount']},
                result_parquet_key = {q(kunci)},
                stage_durations = {q(durasi)}::jsonb,
                {set_aturan}
                peak_rss_mb = {rss_mb},
                updated_at = now(), updated_by = {q(AKTOR_ENGINE)}
            WHERE id = {q(job['job_id'])}""")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return con.execute(f"""SELECT count(*) FROM portal.syncrono_matching_result
                           WHERE job_id = {q(job['job_id'])}""").fetchone()[0]


def kirim_callback(url: str, muatan: dict) -> str:
    """
    POST ke `callbackUrl` (spesifikasi §7). Kembalikan keterangan hasilnya.

    Kebijakan ulangnya sama dengan callback grading: 4xx selain 429 berarti
    muatannya yang salah dan tidak diulang. Kegagalan di sini TIDAK
    menggagalkan job — hasilnya sudah ada di tabel portal, dan portal bisa
    membacanya langsung dari sana.
    """
    badan = json.dumps(muatan, ensure_ascii=False).encode()
    header = {"Content-Type": "application/json",
              "x-callback-source": "matching-engine"}
    galat = None
    for percobaan in range(1, CALLBACK_PERCOBAAN + 1):
        try:
            req = urllib.request.Request(url, data=badan, headers=header, method="POST")
            with urllib.request.urlopen(req, timeout=CALLBACK_TIMEOUT) as r:
                return f"terkirim, HTTP {r.status} (percobaan {percobaan})"
        except urllib.error.HTTPError as e:
            galat = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001 — jaringan, DNS, timeout
            galat = f"{type(e).__name__}: {e}"
        if percobaan < CALLBACK_PERCOBAAN:
            time.sleep(2 ** percobaan)
    return f"GAGAL: {galat}"


def _rss_puncak_mb() -> int:
    """
    Puncak RSS PROSES ini, dalam MB.

    Proses ini adalah Langflow utuh, jadi angkanya mencakup lebih dari satu job
    ini — ia batas atas, bukan ukuran tepat. Tidak ada cara jujur memisahkan
    memori satu thread dari prosesnya.
    """
    try:
        # Diimpor di sini, bukan di kepala modul: `resource` hanya ada di Linux,
        # dan memaksanya di tingkat modul membuat seluruh logika matching tidak
        # bisa diimpor untuk diuji di luar container.
        import resource
    except ImportError:
        return 0
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


# ── Rangkaian penuh ─────────────────────────────────────────────────────────

def jalankan(job: dict, lapor=lambda t: None) -> dict:
    """Satu job utuh, dari payload sampai callback. Galat dilempar ke pemanggil."""
    ms = {}
    awal = time.perf_counter()

    def jam(nama, mulai):
        ms[nama] = int((time.perf_counter() - mulai) * 1000)

    t = time.perf_counter()
    con = buka_koneksi()
    try:
        pasang_endpoint_s3(con, job, "[M]")
        _pasang_portal(con)

        status = _status_portal(con, job["job_id"])
        if status is None:
            raise ValueError(
                f"Job '{job['job_id']}' tidak ada di syncrono_matching_job. Portal "
                f"membuat baris job-nya SEBELUM dispatch (spesifikasi §1 langkah 1); "
                f"tanpa baris itu hasilnya tidak punya tempat.")
        if status == "CANCELLED":
            raise Dibatalkan(job["job_id"])

        _tandai(con, job, status="IN_PROGRESS", current_stage="BLOCKING",
                started_at="now()")
        grade = cari_grade(con, job)
        # Aturan dibaca SEKALI di sini dan dipakai sampai job selesai: mengubah
        # konfigurasi lewat API tidak mengganggu job yang sedang berjalan.
        aturan = aturan_matching(con, grade)
        versi = rekam_versi(con)
        dipakai = ringkas_aturan(grade, aturan, versi)
        master = f"s3://{job['s3_bucket']}/{job['master_key']}"
        n = muat_masukan(con, f"s3://{job['s3_bucket']}/{job['incoming_key']}", master,
                         aturan["bersih_nama"])
        print(f"[M] {job['job_id']} {n:,} baris incoming, grade {grade}, "
              f"preset {job['rule_preset'] or '-'}, konfigurasi {versi}")
        print(f"[M] {job['job_id']} bobot {dipakai['weights']}, "
              f"kosong {dipakai['missingElements']}, "
              f"bersih-nama {aturan['bersih_nama']}")
        jam("prepMs", t)

        _cek_batal(con, job)
        _tandai(con, job, current_stage="DETERMINISTIC")
        lapor("M1 pass 1")
        t = time.perf_counter()
        pass1(con, aturan["kontra_jw"])
        jam("pass1Ms", t)

        lapor("M2 pass 2")
        t = time.perf_counter()
        pass2(con)
        jam("pass2Ms", t)

        _cek_batal(con, job)
        _tandai(con, job, current_stage="SCORING")
        lapor("M3 pass 3")
        t = time.perf_counter()
        pass3(con, grade, aturan, aturan["kueri"])
        # Blocking dan scoring Pass 3 berjalan MENYATU: kandidat mengalir dari
        # join langsung ke agregasi tanpa pernah dimaterialkan (lihat N5). Tidak
        # ada titik untuk mengukur blocking sendirian tanpa menjalankannya dua
        # kali — jadi seluruh waktunya dilaporkan di scoringMs, dan blockingMs 0.
        ms["blockingMs"] = 0
        jam("scoringMs", t)

        _tandai(con, job, current_stage="CLASSIFYING")
        lapor("M4 klasifikasi")
        t = time.perf_counter()
        gabung(con)
        susun_pasangan(con, master, aturan["bersih_nama"])
        kelas_ms = time.perf_counter() - t

        # Masih tahap CLASSIFYING bagi portal: menambah nilai current_stage
        # baru berisiko ditolak constraint atau tidak dikenali UI portal.
        lapor("M4 reasoning")
        t = time.perf_counter()
        _isi_reasoning(con, job)
        jam("reasoningMs", t)

        t = time.perf_counter()
        susun_hasil(con, job)
        n_hasil = con.execute("SELECT count(*) FROM hasil").fetchone()[0]
        if n_hasil != n:
            raise RuntimeError(
                f"Hasil memuat {n_hasil:,} baris untuk {n:,} baris incoming. "
                f"Setiap baris incoming harus punya tepat satu baris hasil.")
        m = metrik(con)
        ms["classificationMs"] = int((time.perf_counter() - t + kelas_ms) * 1000)

        _cek_batal(con, job)
        lapor("M5 unggah parquet")
        t = time.perf_counter()
        kunci = unggah_parquet(con, job)
        jam("parquetUploadMs", t)

        lapor("M6 suntik ke portal")
        t = time.perf_counter()
        ms["totalMs"] = int((time.perf_counter() - awal) * 1000)
        rss = _rss_puncak_mb()
        tersuntik = suntik(con, job, kunci, m, ms, rss, dipakai)
        jam("duckdbInjectMs", t)
        ms["totalMs"] = int((time.perf_counter() - awal) * 1000)
        # stage_durations di tabel portal ditulis DI DALAM transaksi, sebelum
        # durasi penyuntikan itu sendiri diketahui. Angka lengkapnya disusulkan.
        _pg(con, f"UPDATE syncrono_matching_job SET stage_durations = "
                 f"{q(ms)}::jsonb WHERE id = {q(job['job_id'])}")
    finally:
        con.close()

    m["stageDurations"] = ms
    m["peakRssMb"] = rss
    # Tambahan di luar spesifikasi: aturan yang dipakai job ini.
    m["configVersion"] = versi
    m["rulesApplied"] = dipakai
    muatan = {
        "jobId": job["job_id"], "fileId": job["file_id"],
        "masterFileId": job["master_file_id"], "status": "COMPLETED",
        "resultParquetKey": kunci, "metrics": m,
        "message": "Pencocokan data selesai, Parquet terunggah ke S3 dan berhasil "
                   "diinjeksi via DuckDB.",
    }
    print(f"[M] {job['job_id']} SELESAI: {tersuntik:,} baris tersuntik — "
          f"AUTO {m['autoCount']:,} REVIEW {m['reviewCount']:,} "
          f"UNMATCH {m['unmatchCount']:,} CONFLICT {m['conflictCount']:,} "
          f"({ms['totalMs']:,} ms)")
    print(f"[M] {job['job_id']} callback {kirim_callback(job['callback_url'], muatan)}")
    return muatan


def tutup_gagal(job: dict, galat: str) -> None:
    """
    Job gagal: tandai FAILED di portal, lalu callback FAILED (§8.1).

    Koneksinya BARU, bukan milik job yang gagal — koneksi itu mungkin justru
    yang rusak. Kalau menandai pun gagal, callback tetap dicoba: portal lebih
    baik tahu dari salah satu jalur daripada tidak tahu sama sekali.
    """
    try:
        con = buka_koneksi()
        try:
            _pasang_portal(con)
            _tandai(con, job, status="FAILED", current_stage="FAILED",
                    failed_at="now()", last_error=galat[:2000])
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        print(f"[M] {job.get('job_id')} status FAILED pun gagal ditulis: {e}")

    if job.get("callback_url"):
        hasil = kirim_callback(job["callback_url"], {
            "jobId": job.get("job_id"), "fileId": job.get("file_id"),
            "masterFileId": job.get("master_file_id"), "status": "FAILED",
            "error": galat[:2000], "message": "Proses matching gagal dieksekusi.",
        })
        print(f"[M] {job.get('job_id')} callback FAILED {hasil}")
