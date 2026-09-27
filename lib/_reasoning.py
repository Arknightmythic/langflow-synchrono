"""
AI Reasoning — isi kolom `reasoning` hasil matching (spesifikasi §6).

ASAL-USUL

Algoritmanya dari cabang `ai_reasoning_parquet_version` (lib/_reasoning.py di
sana), dan kerangkanya dipertahankan:

    vonis per elemen (SAME / DIFFERENT / EMPTY_IN_INSTITUTION / EMPTY_IN_MASTER)
      -> signature -> hash
      -> cache `reasoning_patterns`; LLM hanya untuk pola yang belum ada
      -> templat berplaceholder -> hidrasi massal dengan REPLACE di SQL

Tabel cache-nya pun sama persis (migrasi 005 disalin apa adanya).

YANG BERUBAH, DAN KENAPA

  1. Inline, bukan job terpisah. Berjalan di dalam job matching, sebelum
     result.parquet ditulis: portal menerima hasil lewat penyuntikan, dan
     tidak ada tahap kedua yang bisa mengisi kolomnya belakangan. Tabel
     manual_matches/institution dan reasoning_jobs tidak dipakai.

  2. Bahasa Indonesia, mengikuti contoh §6. Versi asal berbahasa Inggris.

  3. Semua status, bukan hanya REVIEW — §6 memberi contoh untuk AUTO, REVIEW,
     CONFLICT, dan UNMATCH. Signature memuat status, pass, dan pattern_group,
     karena kalimat yang benar bergantung pada ketiganya.

  4. Tanggal dibandingkan sebagai TANGGAL. Versi asal membandingkan teksnya,
     jadi '02-02-1992' dan '1992-02-02' — tanggal yang sama — divonis
     DIFFERENT. Jenis kelamin dinormalisasi di KEDUA sisi ('LAKI-LAKI' = 'L').

  5. LLM TIDAK PERNAH MELIHAT NILAI. Versi asal mengirim nama, tanggal lahir,
     dan nama ibu ke LLM, lalu mencari nilai-nilai itu di jawabannya untuk
     ditukar menjadi placeholder. Kalau LLM menuliskannya sedikit berbeda
     ('Budi Santoso' untuk 'BUDI  SANTOSO', atau menyingkatnya), pencariannya
     meleset dan nama orang itu TERSIMPAN DI TEMPLAT — lalu tercetak di
     penjelasan setiap baris lain yang polanya sama, di berkas mana pun.
     Di sini kalimatnya disusun deterministik dengan placeholder sejak awal,
     dan LLM hanya diminta memperhalus bahasa kalimat berplaceholder itu.
     Tidak ada nilai yang bisa bocor, karena tidak ada nilai yang dikirim.

  6. Jawaban LLM DIPERIKSA sebelum dipakai (`periksa`): placeholder utuh,
     tidak ada angka yang bertambah atau hilang, dan kata kunci makna
     (identik, berbeda, kosong, ...) tidak boleh muncul atau lenyap. Gagal
     periksa -> kalimat deterministik.

  7. Kunci cache memuat versi, model, dan kalimat dasarnya. Versi asal
     menyimpan hasil fallback deterministik ke cache dengan kunci yang sama
     dengan hasil LLM, selamanya: pola yang sekali gagal ke LLM tidak pernah
     dicoba lagi, dan perbaikan kalimat tidak pernah sampai ke pola lama.

  8. Tanpa REASONING_AI_BASE_URL, LLM mati dan TIDAK ADA yang menyentuh
     database — seluruh kalimat deterministik. Sengaja variabel tersendiri,
     bukan ikut NORMALISASI_AI_BASE_URL/OLLAMA_LOCAL_BASE_URL seperti versi
     asal: menyalakan AI untuk pengenalan kolom tidak boleh diam-diam juga
     menyalakannya untuk ratusan pola reasoning per job. Endpoint di luar
     jaringan (mis. https://ollama.com) ditolak kecuali
     REASONING_AI_ALLOW_EXTERNAL=1.

TIDAK PERNAH MENGGAGALKAN MATCHING. Pemanggilnya (`_matching`) membungkus
seluruh tahap ini; galat di sini hanya membuat kolom `reasoning` kosong.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter

from _jobs import q
from _llm import _lokal
from _nama import sql_bersih
from _shared import GENDER_L, GENDER_P, _sql_daftar

BASE_URL = os.getenv("REASONING_AI_BASE_URL", "").strip().strip('"')
MODEL = os.getenv("REASONING_AI_MODEL", "").strip() or "gemma3:12b"
API_KEY = os.getenv("REASONING_AI_API_KEY", "").strip() or "ollama"
TIMEOUT = int(os.getenv("REASONING_AI_TIMEOUT", "30"))
RETRIES = int(os.getenv("REASONING_AI_RETRIES", "2"))

# Batas per job. Pola baru dikirim ke LLM mulai dari yang paling banyak
# barisnya; yang tidak kebagian memakai kalimat deterministik di job ini dan
# mendapat giliran di job berikutnya. Cache membuat biayanya menyusut sendiri.
MAKS_POLA = int(os.getenv("REASONING_AI_MAX_PATTERNS_PER_JOB", "25"))
BATAS_DETIK = float(os.getenv("REASONING_AI_BUDGET_SECONDS", "90"))

# Endpoint di luar jaringan (mis. https://ollama.com) hanya dengan izin
# eksplisit — polanya sama dengan NORMALISASI_AI_IZIN_SAMPEL_LUAR. Yang dikirim
# ke LLM hanya kalimat berplaceholder, tidak ada nilai baris, jadi izin ini soal
# kebijakan jaringan, bukan kebocoran data; tetap harus disengaja.
IZIN_LUAR = os.getenv("REASONING_AI_ALLOW_EXTERNAL", "0").strip() == "1"

# Naikkan bila PROMPT atau `periksa` berubah: templat lama tidak lagi dipakai.
# id-2: kata kunci "beda" (akar kata) menggantikan "berbeda".
VERSI = "id-2"
TABEL_CACHE = "pg.reasoning_patterns"

TOKEN_KOSONG = ("", "-", "null", "none", "nan", "kosong")
PLACEHOLDER = re.compile(r"\{[a-z0-9_]+(?:\.[a-z0-9_]+)?\}")

# (kunci signature, label, placeholder incoming, placeholder master)
ELEMEN = [
    ("nik", "NIK", "{incoming.nik}", "{master.nik}"),
    ("nama", "nama lengkap", "{incoming.nama}", "{master.nama}"),
    ("tgl", "tanggal lahir", "{incoming.tanggal_lahir}", "{master.tanggal_lahir}"),
    ("jk", "jenis kelamin", "{incoming.jenis_kelamin}", "{master.jenis_kelamin}"),
    ("ibu", "nama ibu kandung", "{incoming.nama_ibu}", "{master.nama_ibu}"),
    ("tmp", "tempat lahir", "{incoming.tempat_lahir}", "{master.tempat_lahir}"),
]
KUNCI = [e[0] for e in ELEMEN]

# Kolom tabel `pasangan` (lihat _matching.susun_pasangan) per elemen.
MENTAH_I = {"nik": "i_nik", "nama": "i_nama", "tgl": "i_tgl_mentah",
            "jk": "i_jk", "ibu": "i_ibu", "tmp": "i_tmp"}
MENTAH_M = {"nik": "m_nik", "nama": "m_nama", "tgl": "m_tgl_mentah",
            "jk": "m_jk", "ibu": "m_ibu", "tmp": "m_tmp"}


def _teks(kol: str) -> str:
    return f"trim(CAST({kol} AS VARCHAR))"


def _iso(kol: str) -> str:
    return f"strftime(CAST({kol} AS DATE), '%Y-%m-%d')"


# Placeholder -> nilai per baris. Tanggal ditulis ISO, sama dengan snapshot.
NILAI = {
    "{incoming.nik}": _teks("i_nik"),
    "{master.nik}": _teks("m_nik"),
    "{incoming.nama}": _teks("i_nama"),
    "{master.nama}": _teks("m_nama"),
    "{incoming.tanggal_lahir}": f"COALESCE({_iso('i_tgl')}, {_teks('i_tgl_mentah')})",
    "{master.tanggal_lahir}": f"COALESCE({_iso('m_tgl')}, {_teks('m_tgl_mentah')})",
    "{incoming.jenis_kelamin}": _teks("i_jk"),
    "{master.jenis_kelamin}": _teks("m_jk"),
    "{incoming.nama_ibu}": _teks("i_ibu"),
    "{master.nama_ibu}": _teks("m_ibu"),
    "{incoming.tempat_lahir}": _teks("i_tmp"),
    "{master.tempat_lahir}": _teks("m_tmp"),
    "{kandidat2.nik}": _teks("nik_2"),
    "{kandidat2.nama}": _teks("k2_nama"),
    "{kandidat2.tanggal_lahir}": _iso("k2_tgl"),
    "{skor}": "CAST(skor AS VARCHAR)",
    "{skor2}": "CAST(skor_2 AS VARCHAR)",
    "{jw_nama}": "CAST(round(j(i_nama_clean, m_nama_clean) * 100, 1) AS VARCHAR)",
    "{n_kandidat}": "CAST(n_kandidat AS VARCHAR)",
}


# ── Vonis & signature (SQL) ─────────────────────────────────────────────────

def _kosong(kol: str) -> str:
    daftar = ", ".join(q(t) for t in TOKEN_KOSONG)
    return f"(COALESCE(lower(trim(CAST({kol} AS VARCHAR))), '') IN ({daftar}))"


def _jk(kol: str) -> str:
    return (f"(CASE {_sql_daftar(kol, GENDER_L, 'l')} {_sql_daftar(kol, GENDER_P, 'p')} "
            f"ELSE lower(trim(CAST({kol} AS VARCHAR))) END)")


def _vonis_master(f: str) -> str:
    """Vonis satu elemen, incoming vs master pemenang."""
    ki, km = _kosong(MENTAH_I[f]), _kosong(MENTAH_M[f])
    if f == "nik":
        # `nik_di_master` hanya bermakna untuk NIK tepercaya — Pass 1 hanya
        # mencari NIK tepercaya — jadi yang tidak tepercaya diperiksa lebih dulu.
        return (f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN i_nik = m_nik THEN 'SAME' "
                f"WHEN NOT COALESCE(i_nik_trusted, FALSE) THEN 'UNTRUSTED' "
                f"WHEN nik_di_master THEN 'OWNED_BY_OTHER' "
                f"ELSE 'NOT_IN_MASTER' END")
    if f == "tgl":
        return (f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN i_tgl IS NULL THEN 'UNREADABLE_IN_INSTITUTION' "
                f"WHEN {km} THEN 'EMPTY_IN_MASTER' "
                f"WHEN i_tgl = m_tgl THEN 'SAME' ELSE 'DIFFERENT' END")
    sama = (f"{_jk('i_jk')} = {_jk('m_jk')}" if f == "jk"
            else f"i_{f}_clean = m_{f}_clean")
    # Nama yang sama setelah gelar, bin/binti, dan spasi ganda dibuang — syarat
    # yang sama dengan pola TITLE_DEGREE. Pada grade E uji, 23.058 baris AUTO
    # berbeda HANYA karena itu; "nama berbeda" saja menyesatkan operator.
    gelar = (f"WHEN {sql_bersih('i_nama')} = {sql_bersih('m_nama')} THEN 'SAME_CLEANED' "
             if f == "nama" else "")
    return (f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' "
            f"WHEN {km} THEN 'EMPTY_IN_MASTER' "
            f"WHEN {sama} THEN 'SAME' {gelar}ELSE 'DIFFERENT' END")


def _keadaan_incoming(f: str) -> str:
    """Untuk UNMATCH: tidak ada master pembanding, hanya keadaan sisi incoming."""
    ki = _kosong(MENTAH_I[f])
    if f == "nik":
        return (f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN NOT COALESCE(i_nik_trusted, FALSE) THEN 'UNTRUSTED' "
                f"WHEN nik_di_master THEN 'IN_MASTER' ELSE 'NOT_IN_MASTER' END")
    if f == "tgl":
        return (f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' "
                f"WHEN i_tgl IS NULL THEN 'UNREADABLE_IN_INSTITUTION' ELSE 'PRESENT' END")
    return f"CASE WHEN {ki} THEN 'EMPTY_IN_INSTITUTION' ELSE 'PRESENT' END"


def sql_tanda(ada: dict[str, bool]) -> str:
    """
    Signature per baris: `kunci=nilai` dipisah `|`, HANYA memuat yang benar-benar
    mengubah kalimatnya.

    Elemen yang sama sekali tidak terisi di seluruh berkas bernilai NA dan tidak
    disebut. Berkas grade C-E memang tidak boleh memuat NIK; tanpa ini setiap
    barisnya berbunyi "NIK kosong", yang benar tapi tidak berguna.
    """
    def rangkai(vonis) -> str:
        return ", ".join(f"'{f}=' || " + (f"({vonis(f)})" if ada[f] else "'NA'")
                         for f in KUNCI)

    return f"""CASE
        WHEN status = 'UNMATCH' THEN concat_ws('|', 'status=UNMATCH',
            'sebab=' || CASE WHEN n_kandidat = 0 THEN 'NO_CANDIDATE' ELSE 'SCORED' END,
            {rangkai(_keadaan_incoming)})
        WHEN status = 'CONFLICT' AND method = 'SCORING' THEN concat_ws('|',
            'status=CONFLICT', 'metode=SCORING',
            'k2tgl=' || CASE WHEN m_tgl IS NULL OR k2_tgl IS NULL THEN 'NA'
                             WHEN m_tgl = k2_tgl THEN 'SAME' ELSE 'DIFFERENT' END)
        WHEN status = 'CONFLICT' THEN concat_ws('|', 'status=CONFLICT', 'metode=' || method,
            'jumlah=' || CASE WHEN n_kandidat > 2 THEN 'BANYAK' ELSE 'DUA' END)
        ELSE concat_ws('|', 'status=' || status, 'metode=' || method,
            'pola=' || COALESCE(pattern_group, '-'),
            {rangkai(_vonis_master)})
    END"""


# ── Kalimat deterministik ───────────────────────────────────────────────────

def _daftar(xs: list[str]) -> str:
    if len(xs) == 1:
        return xs[0]
    if len(xs) == 2:
        return f"{xs[0]} dan {xs[1]}"
    return ", ".join(xs[:-1]) + f", dan {xs[-1]}"


def _kapital(s: str) -> str:
    return s[:1].upper() + s[1:]


NIK_VS_MASTER = {
    # "berbeda", bukan "tidak sama": gemma4:31b menulis ulang frasa ini menjadi
    # "berbeda dengan" pada 3 dari 3 pola, dan pemeriksa kata kunci menolaknya.
    "UNTRUSTED": "NIK berkas ({incoming.nik}) berbeda dengan NIK master dan sudah "
                 "ditandai tidak tepercaya saat grading",
    "NOT_IN_MASTER": "NIK berkas ({incoming.nik}) tidak terdaftar di master",
    "OWNED_BY_OTHER": "Perhatian: NIK berkas ({incoming.nik}) terdaftar di master "
                      "atas nama orang lain",
}

NIK_UNMATCH = {
    # Sengaja TIDAK menyebut "sehingga tidak dipakai": itu bergantung pada versi
    # query grade 1/2 di tabel matching_queries — seeder tidak pernah menimpa,
    # dan DB yang di-seed sebelum penjaga nik_trusted ditambahkan (23 Sep) masih
    # memakai NIK tak tepercaya untuk blocking. Hanya fakta yang pasti.
    "UNTRUSTED": "NIK berkas ({incoming.nik}) ditandai tidak tepercaya saat grading",
    "NOT_IN_MASTER": "NIK berkas ({incoming.nik}) tidak terdaftar di master",
    "IN_MASTER": "NIK berkas ({incoming.nik}) terdaftar di master, tetapi data identitas "
                 "lainnya tidak cukup cocok dengan pemilik NIK tersebut",
}

TGL_TAK_TERBACA = ("tanggal lahir pada data incoming ('{incoming.tanggal_lahir}') "
                   "tidak dapat dibaca sebagai tanggal")

# `sql_bersih` membuang gelar depan/belakang DAN bin/binti — kalimatnya harus
# menyebut keduanya, bukan hanya gelar.
BEDA_GELAR = "gelar akademis/keagamaan atau bin/binti"


def _klausa_beda(kunci: str, label: str, ph_i: str, ph_m: str) -> str:
    if kunci == "nama":
        return f"nama lengkap berbeda ('{ph_i}' vs '{ph_m}', Jaro-Winkler {{jw_nama}}%)"
    if kunci == "tgl":
        return f"tanggal lahir berbeda ({ph_i} vs {ph_m})"
    return f"{label} berbeda ('{ph_i}' vs '{ph_m}')"


def _rincian(s: dict, lewati: set[str], juga: bool) -> list[str]:
    """Kalimat untuk elemen yang belum disebut kalimat pembuka."""
    catatan, beda, sama, kosong_i, kosong_m = [], [], [], [], []
    for kunci, label, ph_i, ph_m in ELEMEN:
        v = s.get(kunci, "NA")
        if kunci in lewati or v == "NA":
            continue
        if v == "SAME":
            sama.append(label)
        elif v == "DIFFERENT":
            beda.append(_klausa_beda(kunci, label, ph_i, ph_m))
        elif v == "SAME_CLEANED":
            beda.append(f"nama lengkap hanya berbeda pada {BEDA_GELAR} ('{ph_i}' vs '{ph_m}')")
        elif v == "EMPTY_IN_INSTITUTION":
            kosong_i.append(label)
        elif v == "EMPTY_IN_MASTER":
            kosong_m.append(label)
        elif v == "UNREADABLE_IN_INSTITUTION":
            catatan.append(TGL_TAK_TERBACA)
        elif kunci == "nik" and v in NIK_VS_MASTER:
            catatan.append(NIK_VS_MASTER[v])
    kalimat = [f"{c}." for c in catatan]
    if beda:
        kalimat.append(f"{_daftar(beda)}.")
    if sama:
        kalimat.append(f"{_daftar(sama)} {'juga ' if juga else ''}identik.")
    if kosong_i:
        kalimat.append(f"{_daftar(kosong_i)} kosong pada data incoming.")
    if kosong_m:
        kalimat.append(f"{_daftar(kosong_m)} kosong pada master.")
    return [_kapital(k) for k in kalimat]


def _templat_cocok(s: dict) -> str:
    """AUTO dan REVIEW — ada satu master pemenang."""
    status, metode, pola = s["status"], s["metode"], s["pola"]
    juga = False
    if status == "AUTO" and metode == "PASS1_NIK_NAMA":
        buka = ("Cocok otomatis melalui pencocokan deterministik Pass 1: NIK "
                "({master.nik}) dan nama lengkap identik dengan master.")
        lewati, juga = {"nik", "nama"}, True
    elif status == "AUTO" and metode == "PASS2_NAMA_TGL_IBU":
        buka = ("Cocok otomatis melalui pencocokan deterministik Pass 2: nama lengkap, "
                "tanggal lahir ({master.tanggal_lahir}), dan nama ibu kandung identik "
                "dengan master NIK {master.nik}.")
        lewati, juga = {"nama", "tgl", "ibu"}, True
    elif status == "AUTO":
        buka = ("Cocok otomatis melalui pencocokan skor (Pass 3): skor kemiripan "
                "{skor}% terhadap master NIK {master.nik}.")
        lewati = set()
    elif pola == "NIK_CONFLICT" and s.get("nik") == "OWNED_BY_OTHER":
        # Aturan pengaman 1 di Pass 2: identitas menunjuk X, NIK menunjuk orang lain.
        buka = ("Peringatan: nama lengkap, tanggal lahir, dan nama ibu kandung identik "
                "dengan master NIK {master.nik}, tetapi NIK berkas ({incoming.nik}) "
                "terdaftar di master atas nama orang lain. Disarankan verifikasi fisik "
                "dokumen.")
        lewati, juga = {"nik", "nama", "tgl", "ibu"}, True
    elif pola == "NIK_CONFLICT" and s.get("nik") == "SAME":
        buka = ("Peringatan: NIK cocok dengan master ({master.nik}), namun nama warga "
                "('{incoming.nama}') berbeda total dengan master ('{master.nama}', "
                "Jaro-Winkler {jw_nama}%). Disarankan verifikasi fisik dokumen.")
        lewati = {"nik", "nama"}
    elif pola == "TITLE_DEGREE":
        buka = (f"Skor kemiripan {{skor}}%. Nama hanya berbeda pada {BEDA_GELAR} "
                "('{incoming.nama}' vs '{master.nama}').")
        lewati = {"nama"}
    elif pola == "SWAPPED_DOB":
        buka = ("Skor kemiripan {skor}%. Hari dan bulan lahir terindikasi tertukar "
                "({incoming.tanggal_lahir} pada data incoming vs {master.tanggal_lahir} "
                "pada master).")
        lewati = {"tgl"}
    elif pola == "SPELLING_NAME":
        buka = ("Skor kemiripan {skor}%. Terdapat perbedaan ejaan nama ('{incoming.nama}' "
                "vs '{master.nama}', Jaro-Winkler {jw_nama}%).")
        lewati = {"nama"}
    else:
        buka = ("Skor kemiripan {skor}% terhadap master NIK {master.nik} belum memenuhi "
                "syarat pencocokan otomatis.")
        lewati = set()
    return " ".join([buka] + _rincian(s, lewati, juga))


def _templat_konflik(s: dict) -> str:
    if s["metode"] == "SCORING":
        # n_kandidat di sini menghitung SEMUA kandidat hasil blocking, bukan
        # yang seri — jadi tidak disebut. Yang pasti seri hanya dua teratas.
        buka = ("Dua kandidat teratas memiliki skor seimbang: Kandidat 1 NIK {master.nik} "
                "({master.nama}, {skor}%) dan Kandidat 2 NIK {kandidat2.nik} "
                "({kandidat2.nama}, {skor2}%)")
        if s.get("k2tgl") == "SAME":
            buka += ", dengan tanggal lahir sama ({master.tanggal_lahir})"
        elif s.get("k2tgl") == "DIFFERENT":
            buka += (", dengan tanggal lahir berbeda ({master.tanggal_lahir} vs "
                     "{kandidat2.tanggal_lahir})")
    else:
        kriteria = ("NIK dan nama lengkap" if s["metode"] == "PASS1_NIK_NAMA"
                    else "nama lengkap, tanggal lahir, dan nama ibu kandung")
        pasangan = ("Kandidat 1 NIK {master.nik} ({master.nama}) dan Kandidat 2 NIK "
                    "{kandidat2.nik} ({kandidat2.nama})")
        if s.get("jumlah") == "BANYAK":
            buka = (f"Ditemukan {{n_kandidat}} kandidat master dengan {kriteria} identik, "
                    f"antara lain {pasangan}")
        else:
            buka = f"Ditemukan 2 kandidat master dengan {kriteria} identik: {pasangan}"
    return buka + ". Sistem tidak memilih salah satunya secara otomatis."


def _templat_unmatch(s: dict) -> str:
    if s["sebab"] == "NO_CANDIDATE":
        buka = ("Tidak ditemukan catatan kependudukan yang relevan pada Master Data "
                "Dukcapil: tidak ada kandidat yang lolos penyaringan awal (blocking).")
    else:
        # Tanpa angka ambang: skor tersimpan dibulatkan 2 desimal, jadi 79,996
        # tercetak 80.0 — dan "80.0% di bawah ambang 80%" terbaca keliru.
        buka = ("Tidak ditemukan catatan kependudukan yang cukup mirip pada Master Data "
                "Dukcapil: kandidat terdekat memperoleh skor {skor}% dan tidak memenuhi "
                "kriteria pencocokan otomatis maupun tinjauan.")
    kalimat = [buka]
    if s.get("nik") in NIK_UNMATCH:
        kalimat.append(f"{NIK_UNMATCH[s['nik']]}.")
    kosong = [label for kunci, label, _, _ in ELEMEN
              if s.get(kunci) == "EMPTY_IN_INSTITUTION"]
    if kosong:
        kalimat.append(_kapital(f"{_daftar(kosong)} kosong pada data incoming."))
    if s.get("tgl") == "UNREADABLE_IN_INSTITUTION":
        kalimat.append(_kapital(f"{TGL_TAK_TERBACA}."))
    return " ".join(kalimat)


def templat(tanda: str) -> str:
    """Signature -> kalimat berplaceholder. Murni: sama masukan, sama keluaran."""
    s = dict(bagian.split("=", 1) for bagian in tanda.split("|"))
    if s["status"] == "UNMATCH":
        return _templat_unmatch(s)
    if s["status"] == "CONFLICT":
        return _templat_konflik(s)
    return _templat_cocok(s)


# ── LLM ─────────────────────────────────────────────────────────────────────

PROMPT = """Anda adalah penyunting bahasa untuk penjelasan hasil pencocokan data kependudukan. Anda menerima satu teks penjelasan yang isinya sudah BENAR. Tugas Anda hanya memperhalus bahasanya agar enak dibaca operator.

ATURAN WAJIB:
1. Setiap placeholder berkurung kurawal — misalnya {incoming.nama}, {master.nik}, {skor} — harus muncul PERSIS seperti aslinya dan sebanyak aslinya. Jangan menerjemahkan, mengubah, menggabungkan, atau menghapus placeholder.
2. Jangan menambah fakta, angka, nama, atau placeholder baru. Jangan menghilangkan informasi apa pun.
3. Jangan membalik makna: yang identik tetap identik, yang berbeda tetap berbeda, yang kosong tetap kosong.
4. Bahasa Indonesia baku dan ringkas, paling banyak tiga kalimat.
5. Balas HANYA dengan teks hasilnya — tanpa tanda kutip pembungkus, tanpa judul, tanpa penjelasan."""

# Kata yang menentukan MAKNA. Tiap kata harus ada di jawaban LLM jika dan hanya
# jika ada di kalimat dasarnya — "identik" yang berubah jadi "berbeda" ditolak.
#
# "beda" berupa AKAR kata, bukan "berbeda": LLM wajar menulis "terdapat
# perbedaan pada ..." untuk "... berbeda", dan maknanya sama. Inversi tetap
# tertangkap — "identik" yang diganti "berbeda" menghilangkan kata "identik".
KATA_KUNCI = ("identik", "beda", "kosong", "tidak", "tertukar", "gelar",
              "peringatan", "perhatian", "seimbang", "tepercaya", "terdaftar",
              "otomatis", "ejaan", "kandidat")


def periksa(dasar: str, jawab: str) -> tuple[str | None, str]:
    """
    (templat yang aman dipakai, alasan). Templat None = jawaban ditolak.

    Yang dijamin: tidak ada nilai, angka, atau placeholder yang ditambah atau
    dibuang, dan kata kunci makna tidak berubah. Yang TIDAK bisa dijamin:
    parafrase yang mengubah makna tanpa menyentuh satu pun kata kunci. Itu
    sebabnya kalimat dasarnya deterministik dan sudah benar — LLM hanya
    memperhalus, tidak pernah menjadi sumber fakta.
    """
    teks = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", (jawab or "").strip())
    while len(teks) >= 2 and teks[0] == teks[-1] and teks[0] in "\"'":
        teks = teks[1:-1].strip()
    teks = re.sub(r"\s+", " ", teks).strip()
    if not teks:
        return None, "jawaban kosong"
    if Counter(PLACEHOLDER.findall(teks)) != Counter(PLACEHOLDER.findall(dasar)):
        return None, "placeholder tidak utuh"
    luar, luar_dasar = PLACEHOLDER.sub("", teks), PLACEHOLDER.sub("", dasar)
    if re.search(r"[{}*#`]", luar):
        return None, "karakter terlarang di luar placeholder"
    if Counter(re.findall(r"\d+", luar)) != Counter(re.findall(r"\d+", luar_dasar)):
        return None, "angka bertambah atau hilang"
    for kata in KATA_KUNCI:
        if (kata in luar.lower()) != (kata in luar_dasar.lower()):
            return None, f"kata kunci '{kata}' berubah"
    if not 0.6 * len(dasar) <= len(teks) <= 1.6 * len(dasar):
        return None, "panjangnya menyimpang"
    return teks, "ok"


def endpoint_aman(url: str) -> bool:
    """On-prem saja: alamat privat/loopback, atau nama layanan Docker satu label."""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    return _lokal(url) or (bool(host) and "." not in host)


def _tanya_llm(dasar: str) -> str:
    url = BASE_URL.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions" if url.endswith("/v1") else "/v1/chat/completions"
    badan = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": PROMPT},
                     {"role": "user", "content": f"Teks:\n{dasar}"}],
        "temperature": 0,
        "stream": False,
    }).encode()
    galat = None
    for percobaan in range(1, RETRIES + 1):
        req = urllib.request.Request(url, data=badan, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            galat = f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # noqa: BLE001 — timeout, DNS, koneksi putus
            galat = f"{type(e).__name__}: {e}"
        if percobaan < RETRIES:
            time.sleep(1.5 * percobaan)
    raise RuntimeError(f"LLM gagal setelah {RETRIES} percobaan — {galat}")


def kunci_cache(dasar: str) -> str:
    return hashlib.md5(f"{VERSI}|{MODEL}|{dasar}".encode()).hexdigest()


def _pakai_llm(con, job: dict, pola: list[tuple], dasar: dict, pakai: dict) -> Counter:
    """
    Isi `pakai` dengan templat LLM dari cache atau panggilan baru.

    Yang disimpan ke cache: hasil LLM yang LOLOS periksa, dan juga penolakan —
    sebagai kalimat dasarnya, bertanda `llm-ditolak` — supaya pola itu tidak
    ditanyakan ulang di setiap job. Temperature 0 TIDAK menjamin jawaban sama:
    pada gemma4:31b cloud, 3 dari 7 pola yang ditolak lolos saat ditanya ulang.
    Tapi kalimat deterministiknya sudah benar, jadi mengulang tiap job hanya
    membuang anggaran. Kegagalan JARINGAN tidak disimpan: itu sesaat.
    """
    stat = Counter()
    if not endpoint_aman(BASE_URL):
        if not IZIN_LUAR:
            print(f"[R] REASONING_AI_BASE_URL {BASE_URL} bukan alamat on-prem — LLM "
                  f"tidak dipanggil. Yang dikirim ke LLM memang hanya kalimat "
                  f"berplaceholder, tapi batas jaringan tetap dijaga; setel "
                  f"REASONING_AI_ALLOW_EXTERNAL=1 kalau memang disengaja.")
            stat["endpoint_ditolak"] = 1
            return stat
        print(f"[R] endpoint LUAR {BASE_URL} dipakai dengan izin eksplisit "
              f"REASONING_AI_ALLOW_EXTERNAL=1 — yang dikirim hanya kalimat "
              f"berplaceholder, tanpa nilai baris")

    kunci = {tanda: kunci_cache(dasar[tanda]) for tanda, _, _ in pola}
    try:
        daftar = ", ".join(q(h) for h in set(kunci.values()))
        simpanan = dict(con.execute(
            f"SELECT pattern_hash, reason_template FROM {TABEL_CACHE} "
            f"WHERE pattern_hash IN ({daftar})").fetchall())
    except Exception as e:  # noqa: BLE001
        print(f"[R] cache {TABEL_CACHE} tidak terbaca ({type(e).__name__}: {e}) — "
              f"LLM dilewati, seluruh kalimat deterministik. Sudah jalankan migrasi 005?")
        stat["cache_gagal"] = 1
        return stat

    mulai, mati, baru, terpakai = time.perf_counter(), False, [], Counter()
    for tanda, n, contoh in pola:
        h = kunci[tanda]
        if h in simpanan:
            pakai[tanda] = simpanan[h]
            terpakai[h] += n
            stat["cache"] += 1
            continue
        if mati or stat["llm_dipanggil"] >= MAKS_POLA \
                or time.perf_counter() - mulai > BATAS_DETIK:
            stat["terlewat"] += 1
            continue
        stat["llm_dipanggil"] += 1
        t = time.perf_counter()
        try:
            jawab = _tanya_llm(dasar[tanda])
        except Exception as e:  # noqa: BLE001
            # Endpoint yang gagal setelah percobaan ulang tidak akan pulih dalam
            # hitungan detik; sisa pola langsung deterministik.
            print(f"[R] {e} — sisa pola job ini memakai kalimat deterministik")
            stat["llm_gagal"] += 1
            mati = True
            continue
        finally:
            stat["llm_ms"] += int((time.perf_counter() - t) * 1000)
        hasil, alasan = periksa(dasar[tanda], jawab)
        if hasil:
            stat["llm_diterima"] += 1
            pakai[tanda] = hasil
        else:
            stat["llm_ditolak"] += 1
            print(f"[R] jawaban LLM ditolak ({alasan}) untuk pola {tanda}")
        sumber = "llm" if hasil else "llm-ditolak"
        ringkas = "/".join(dict(b.split("=", 1) for b in tanda.split("|")).get(k, "-")
                           for k in ("status", "metode", "pola"))
        baru.append((h, f"{sumber}:{ringkas}:{h[:8]}"[:255], tanda, pakai[tanda],
                     f"{job.get('job_id')}:{contoh}", n))

    for h, nama, tanda, isi, contoh, n in baru:
        try:
            con.execute(f"INSERT INTO {TABEL_CACHE} (pattern_hash, pattern_name, "
                        f"pattern_signature, reason_template, sample_id, hit_count) "
                        f"VALUES (?, ?, ?, ?, ?, ?)", [h, nama, tanda, isi, contoh, n])
        except Exception as e:  # noqa: BLE001 — mis. job lain menyimpan kunci yang sama
            print(f"[R] pola {h[:8]} tidak tersimpan ke cache: {type(e).__name__}: {e}")
    for h, n in terpakai.items():
        try:
            con.execute(f"UPDATE {TABEL_CACHE} SET hit_count = hit_count + ?, "
                        f"updated_at = now() WHERE pattern_hash = ?", [n, h])
        except Exception as e:  # noqa: BLE001
            print(f"[R] hit_count {h[:8]} tidak terbarui: {type(e).__name__}: {e}")
    return stat


# ── Rangkaian ───────────────────────────────────────────────────────────────

def _sql_rangkai(templat: str) -> str:
    """
    Templat -> concat(potongan teks, nilai, potongan teks, ...).

    Versi asal menghidrasi dengan REPLACE bersarang, satu per placeholder.
    Terukur pada 200 ribu baris: 3,1 detik — setiap baris menjalankan semua
    REPLACE, termasuk untuk placeholder yang tidak ada di templatnya. Dirangkai
    begini, hanya placeholder milik templat itu yang dihitung: 0,11 detik.

    Sekaligus menutup celah REPLACE berantai: nilai yang memuat teks '{skor}'
    akan ikut diganti oleh REPLACE berikutnya. Di sini nilai disisipkan dan
    tidak pernah dipindai ulang.
    """
    bagian, awal = [], 0
    for m in PLACEHOLDER.finditer(templat):
        if m.start() > awal:
            bagian.append(q(templat[awal:m.start()]))
        bagian.append(f"COALESCE({NILAI[m.group()]}, '(kosong)')")
        awal = m.end()
    if awal < len(templat):
        bagian.append(q(templat[awal:]))
    return f"concat({', '.join(bagian)})"


def _elemen_ada(con) -> dict[str, bool]:
    kolom = [f"count(*) FILTER (WHERE NOT {_kosong(MENTAH_I[f])}) > 0" for f in KUNCI]
    return dict(zip(KUNCI, con.execute(f"SELECT {', '.join(kolom)} FROM pasangan").fetchone()))


def isi(con, job: dict) -> dict:
    """
    Tabel `pasangan` -> tabel `alasan` (id, reasoning), satu baris per baris.

    Kalimat dasar disusun per SIGNATURE, bukan per baris: 200 ribu baris
    biasanya hanya puluhan signature. Nilai baris baru masuk saat hidrasi.
    """
    ada = _elemen_ada(con)
    con.execute(f"CREATE OR REPLACE TABLE vonis AS SELECT *, {sql_tanda(ada)} AS tanda "
                f"FROM pasangan")
    pola = con.execute("""SELECT tanda, count(*) AS n, min(id) AS contoh FROM vonis
                          GROUP BY tanda ORDER BY n DESC, tanda""").fetchall()
    dasar = {tanda: templat(tanda) for tanda, _, _ in pola}
    pakai = dict(dasar)
    stat = _pakai_llm(con, job, pola, dasar, pakai) if BASE_URL else Counter()

    # CASE memilih rangkaian milik signature baris itu; DuckDB mengevaluasi
    # tiap cabang hanya untuk baris yang masuk ke cabang tersebut.
    cabang = " ".join(f"WHEN {q(tanda)} THEN {_sql_rangkai(t)}" for tanda, t in pakai.items())
    con.execute(f"""CREATE OR REPLACE TABLE alasan AS
                    SELECT id, CASE tanda {cabang} END AS reasoning FROM vonis""")

    n_alasan, n_kosong = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE reasoning IS NULL) FROM alasan").fetchone()
    n_pasangan = con.execute("SELECT count(*) FROM pasangan").fetchone()[0]
    if n_alasan != n_pasangan or n_kosong:
        raise RuntimeError(f"hidrasi reasoning tidak utuh: {n_alasan:,} kalimat untuk "
                           f"{n_pasangan:,} baris, {n_kosong:,} kosong")
    return {"baris": n_alasan, "pola": len(pola), **stat}
