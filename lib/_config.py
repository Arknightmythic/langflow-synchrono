"""
Konfigurasi aturan grading & matching — baca, validasi, tulis, jejak.

Dipakai tiga pihak yang berbeda kebutuhan:

  * mesin grading   -> `baca_kriteria()`, `nilai_global()`, `rekam_versi()`
  * mesin matching  -> `aturan_matching()`, `rekam_versi()`
  * API konfigurasi -> `baca_semua()`, `perbarui()`, `perbarui_global()`,
                       `baca_riwayat()`, `baca_versi()`

YANG BISA DIATUR

  Per grade — dilihat pengguna sebagai satu "aturan grade":

    grade_criteria    ambang kelengkapan & mutu NIK   criteria          (A-D)
    grade_bands       pita skor, label, kelayakan     score             (A-F)
    grade_rules       ambang AUTO/REVIEW              matching          (A-F)
                      bobot skor per elemen           matching.weights          (A-E)
                      elemen yang dihitung "kosong"   matching.missingElements  (A-E)
                      pembersihan nama                matching.nameCleaning     (A-E)
    matching_queries  kueri blocking                  matching.blocking — HANYA DIBACA

  Global — tabel `engine_config`, satu baris per kunci:

    grading.scoreWeights        bobot skor mutu grading (kelengkapan, NIK tepercaya)
    grading.gradeECombinations  kombinasi kolom yang membuat berkas jadi grade E
    matching.conflictEpsilon    selisih skor dua kandidat yang masih dianggap seri
    matching.contradictionJw    di bawah ini nama ibu dianggap bertentangan

  Kunci global yang belum pernah disimpan memakai env (untuk dua kunci
  matching, seperti sebelumnya) atau nilai bawaan. Menyimpan `null` menghapus
  barisnya — kembali ke env/bawaan.

VALIDASI BUKAN HIASAN

Tabel yang bisa diedit dari UI berarti angkanya bisa dibuat saling
bertentangan. Yang paling berbahaya bukan nilai di luar rentang — itu sudah
ditolak CHECK constraint — melainkan aturan yang membuat sebuah grade TIDAK
PERNAH TERCAPAI. Kalau ambang B dibuat sama ketat atau lebih ketat dari A,
setiap berkas yang lolos B pasti sudah lolos A lebih dulu, dan B mati diam-diam
tanpa satu pun pesan galat. `validasi()` menangkap keadaan itu.

PERINGATAN, BUKAN PENOLAKAN

Konfigurasi matching yang SAH bisa tetap menghasilkan perilaku yang tidak
disangka: pita REVIEW yang tidak mungkin tercapai, atau baris yang selebihnya
cocok sempurna tapi jatuh ke UNMATCH karena kosongnya tidak sesuai aturan.
`analisis_matching()` menghitung skor tertinggi yang MASIH MUNGKIN untuk
setiap jumlah elemen kosong — dengan aritmetika pecahan yang sama persis
dengan SQL-nya — lalu menjelaskan keadaan seperti itu. Tidak menolak: bisa
jadi memang disengaja.

JEJAK

  config_riwayat  setiap perubahan lewat API: siapa, kapan, dari berapa ke berapa
  config_versi    isi konfigurasi lengkap per versi (sidik 12 heksa). Setiap
                  job grading & matching mencatat versi yang dipakainya, jadi
                  hasil lama tetap bisa ditelusuri setelah aturannya berubah.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import os

import duckdb

from _jobs import jalankan_pg, q
from _shared import (BERSIH_NAMA_BAWAAN, BOBOT_BAWAAN, ELEMEN_SKOR, MISSING,
                     SQL_MACRO, kolom_elemen, kolom_kurang, sql_view_incoming,
                     sql_view_master)

# Urutan ini dipakai di seluruh muatan API.
ELEMEN = ["nik", "nama", "tempat_lahir", "tanggal_lahir",
          "jenis_kelamin", "nama_ibu"]

KOLOM_MIN = [f"min_{e}" for e in ELEMEN] + ["min_nik_trusted"]

NIK_KOLOM_SAH = ("wajib", "terlarang", "abaikan")

HURUF = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}

# Grade yang kriterianya berbentuk tabel. E dan F sengaja di luar — lihat
# komentar di infra/skema/db/migrasi/003_config.sql.
GRADE_KONFIGURABEL = (1, 2, 3, 4)

# Grade yang dicocokkan. F ditolak sebelum matching, jadi tidak punya bobot.
GRADE_MATCHING = (1, 2, 3, 4, 5)

CATATAN_TAK_KONFIGURABEL = {
    5: ("Grade E memakai KOMBINASI kolom yang harus ada, bukan ambang "
        "persentase, sehingga bentuknya tidak muat di tabel ini. Kombinasinya "
        "diatur di bagian global: `grading.gradeECombinations`."),
    6: ("Grade F bukan aturan melainkan hasil: tidak satu pun kriteria di atas "
        "terpenuhi. Tidak ada yang bisa disetel."),
}

# Bawaan kombinasi grade E — disalin dari GraderService lama.
KOMBINASI_E = [
    ["nama", "tanggal_lahir", "jenis_kelamin"],
    ["nama", "tempat_lahir", "tanggal_lahir"],
    ["nama", "tempat_lahir", "nama_ibu"],
    ["nama", "tanggal_lahir", "wilayah"],
]
ELEMEN_KOMBINASI_E = ["nama", "tempat_lahir", "tanggal_lahir",
                      "jenis_kelamin", "nama_ibu", "wilayah"]

# Bawaan bobot skor mutu grading.
BOBOT_SKOR = {"kelengkapan": 0.6, "nik_tepercaya": 0.4}

KUNCI_BERSIH = ("titles", "patronym", "abbreviations")

KOLOM_ATURAN = ["auto_missing_max", "auto_score_min", "review_missing_count",
                "review_score_min", "review_score_max"]

# Toleransi "jumlah bobot = 100". Bobot pecahan seperti 33,3 + 33,3 + 33,4
# dijumlah dalam biner, jadi pembandingan persis akan menolak yang sah.
TOLERANSI_JUMLAH = 1e-6


def _env_angka(nama: str, bawaan: float) -> tuple[float, str]:
    mentah = os.getenv(nama, "").strip()
    if mentah:
        try:
            return float(mentah), "env"
        except ValueError:
            pass
    return bawaan, "default"


# kunci -> nilai bawaan beserta sumbernya ("env" atau "default")
GLOBAL = {
    "grading.scoreWeights": lambda: (dict(BOBOT_SKOR), "default"),
    "grading.gradeECombinations": lambda: ([list(k) for k in KOMBINASI_E], "default"),
    "matching.conflictEpsilon": lambda: _env_angka("MATCHING_CONFLICT_EPSILON", 0.0),
    "matching.contradictionJw": lambda: _env_angka("MATCHING_KONTRA_JW", 0.80),
}


# ── Pembacaan ──────────────────────────────────────────────────────────────

def _baris_jadi_dict(kur) -> list[dict]:
    nama = [d[0] for d in kur.description]
    return [dict(zip(nama, b)) for b in kur.fetchall()]


def _json(nilai):
    """Kolom JSONB dari PostgreSQL tiba sebagai teks lewat DuckDB."""
    if nilai is None or isinstance(nilai, (dict, list)):
        return nilai
    return json.loads(nilai)


def _baca_pg(con, sql: str):
    """
    SELECT ke PostgreSQL engine, dengan satu kali coba ulang.

    DuckDB menyimpan katalog PostgreSQL di koneksinya. Kolom atau tabel yang
    ditambahkan migrasi SESUDAH koneksi itu dibuka tidak terlihat olehnya
    sampai cache-nya dibersihkan — pesan galatnya "column not found", padahal
    kolomnya ada.
    """
    try:
        return con.execute(sql)
    except Exception:  # noqa: BLE001
        try:
            con.execute("CALL pg_clear_cache()")
        except Exception:  # noqa: BLE001
            pass
        try:
            return con.execute(sql)
        except Exception as e:
            if any(k in str(e) for k in ("bobot", "engine_config", "config_")):
                raise RuntimeError(
                    "Tabel/kolom konfigurasi dinamis belum ada di basis data engine. "
                    "Jalankan migrasi: python infra/skema/migrate.py") from e
            raise


def baca_kriteria(con) -> list[dict]:
    """
    Kriteria A-D terurut evaluasi. Inilah yang dipakai mesin grading.

    Hanya baris `aktif` yang dikembalikan, sehingga satu grade bisa
    dinonaktifkan sementara tanpa menghapus konfigurasinya.
    """
    kur = con.execute(f"""
        SELECT grade_id, urutan, nik_kolom, {', '.join(KOLOM_MIN)}
          FROM pg.public.grade_criteria
         WHERE aktif
         ORDER BY urutan
    """)
    return _baris_jadi_dict(kur)


def baca_global(con) -> dict:
    """Nilai global yang BERLAKU beserta sumbernya: config, env, atau default."""
    tersimpan = {
        k: (_json(v), at, oleh) for k, v, at, oleh in _baca_pg(con, """
            SELECT kunci, nilai, CAST(diubah_at AS VARCHAR), diubah_oleh
              FROM pg.public.engine_config""").fetchall()
    }
    hasil = {}
    for kunci, bawaan in GLOBAL.items():
        if kunci in tersimpan:
            nilai, at, oleh = tersimpan[kunci]
            hasil[kunci] = {"value": nilai, "source": "config",
                            "updatedAt": at, "updatedBy": oleh}
        else:
            nilai, sumber = bawaan()
            hasil[kunci] = {"value": nilai, "source": sumber,
                            "updatedAt": None, "updatedBy": None}
    return hasil


def nilai_global(con) -> dict:
    """Hanya nilainya, untuk mesin."""
    return {k: v["value"] for k, v in baca_global(con).items()}


def _bobot_dari_db(nilai, grade: int) -> list[list] | None:
    """[[elemen, persen], ...] BERURUTAN. NULL = bawaan grade itu."""
    isi = _json(nilai)
    if isi is None:
        return [list(p) for p in BOBOT_BAWAAN[grade]] if grade in BOBOT_BAWAAN else None
    return [[f, b] for f, b in isi]


def _baca_aturan(con) -> dict[int, dict]:
    """grade_rules per grade, kolom JSON sudah diurai dan diisi bawaan."""
    kur = _baca_pg(con, f"""
        SELECT grade_code, {', '.join(KOLOM_ATURAN)},
               bobot, elemen_kosong, bersih_nama,
               CAST(diubah_at AS VARCHAR) AS diubah_at, diubah_oleh
          FROM pg.public.grade_rules""")
    hasil = {}
    for r in _baris_jadi_dict(kur):
        g = r["grade_code"]
        r["bobot"] = _bobot_dari_db(r["bobot"], g)
        kosong = _json(r["elemen_kosong"])
        r["elemen_kosong"] = (kosong if kosong is not None
                              else (list(MISSING[g]) if g in MISSING else None))
        r["bersih_nama"] = ({**BERSIH_NAMA_BAWAAN, **(_json(r["bersih_nama"]) or {})}
                            if g in GRADE_MATCHING else None)
        hasil[g] = r
    return hasil


def _baca_kueri(con) -> dict[int, str]:
    return dict(con.execute(
        "SELECT grade_code, matching_query FROM pg.public.matching_queries"
    ).fetchall())


# Kolom MENTAH berkas incoming & master — cukup untuk membangun view yang
# dirujuk kueri blocking, persis seperti saat job berjalan.
_MENTAH_INCOMING = frozenset({
    "nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu",
    "provinsi", "kabupaten", "kecamatan", "kelurahan", "status_hidup", "nik_trusted"})
_MENTAH_MASTER = ["nik", "nama_lengkap", "tempat_lahir", "tanggal_lahir",
                  "jenis_kelamin", "nama_ibu", "status_kematian", "provinsi",
                  "kabupaten", "kecamatan", "kelurahan"]


@functools.lru_cache(maxsize=32)
def kolom_blocking(kueri: str) -> frozenset | None:
    """
    Kolom yang DIKELUARKAN kueri blocking.

    Skor Pass 3 hanya bisa membaca kolom ini. Dicari dengan menjalankan
    kuerinya pada view KOSONG yang bentuknya sama dengan saat job berjalan —
    pasti, bukan tebakan dari teks (kueri grade D punya empat cabang CTE).
    None kalau kuerinya tidak bisa diperiksa; validasi lalu tidak menolak.
    """
    try:
        c = duckdb.connect()
        try:
            c.execute(SQL_MACRO)
            kosong_i = ", ".join(f"CAST(NULL AS VARCHAR) AS {k}"
                                 for k in sorted(_MENTAH_INCOMING))
            c.execute(f"""CREATE VIEW incoming_df AS
                          SELECT CAST(NULL AS VARCHAR) AS id,
                                 {sql_view_incoming(set(_MENTAH_INCOMING))}
                          FROM (SELECT {kosong_i}) WHERE FALSE""")
            kosong_m = ", ".join(f"CAST(NULL AS VARCHAR) AS {k}" for k in _MENTAH_MASTER)
            c.execute(f"""CREATE VIEW master_df AS SELECT {sql_view_master()}
                          FROM (SELECT {kosong_m}) WHERE FALSE""")
            return frozenset(r[0] for r in
                             c.execute(f"DESCRIBE SELECT * FROM ({kueri})").fetchall())
        finally:
            c.close()
    except Exception as e:  # noqa: BLE001
        print(f"[config] kueri blocking tidak bisa diperiksa: {' '.join(str(e).split())[:200]}")
        return None


def elemen_tersedia(kueri: str | None) -> dict | None:
    """Elemen yang bisa diberi bobot / dihitung kosong dengan kueri blocking ini."""
    kolom = kolom_blocking(kueri) if kueri else None
    if kolom is None:
        return None
    return {
        "weights": [e for e in ELEMEN_SKOR
                    if all(c in kolom for c in kolom_elemen(e, "i") + kolom_elemen(e, "m"))],
        "missingElements": [e for e in ELEMEN_SKOR
                            if all(c in kolom for c in kolom_elemen(e, "i"))],
    }


def _baca_tabel_grade(con) -> tuple[dict, dict]:
    kriteria = {r["grade_id"]: r for r in _baris_jadi_dict(con.execute(f"""
        SELECT grade_id, urutan, nik_kolom, {', '.join(KOLOM_MIN)}, aktif,
               CAST(diubah_at AS VARCHAR) AS diubah_at, diubah_oleh
          FROM pg.public.grade_criteria
    """))}
    pita = {r["grade_id"]: r for r in _baris_jadi_dict(con.execute("""
        SELECT grade_id, grade_letter, score_min, score_max, severity_label,
               can_proceed, criteria_description
          FROM pg.public.grade_bands
    """))}
    return kriteria, pita


def aturan_matching(con, grade: int) -> dict:
    """Seluruh aturan yang dipakai SATU job matching, sekali baca di awal job."""
    atr = _baca_aturan(con).get(grade)
    if not atr:
        raise ValueError(f"grade_rules untuk grade {grade} tidak ada")
    kueri = _baca_kueri(con).get(grade)
    if not kueri:
        raise ValueError(f"matching_queries untuk grade {grade} tidak ada")
    glob = nilai_global(con)
    return {
        **{k: atr[k] for k in KOLOM_ATURAN},
        "bobot": atr["bobot"],
        "elemen_kosong": atr["elemen_kosong"],
        "bersih_nama": atr["bersih_nama"],
        "kueri": kueri,
        "epsilon": float(glob["matching.conflictEpsilon"]),
        "kontra_jw": float(glob["matching.contradictionJw"]),
    }


def baca_semua(con, grade_id: int | None = None) -> dict:
    """Gambaran utuh per grade — kriteria, pita skor, aturan matching, global."""
    kriteria, pita = _baca_tabel_grade(con)
    aturan = _baca_aturan(con)
    kueri = _baca_kueri(con)
    glob = baca_global(con)

    daftar = []
    for gid in sorted(set(pita) | set(kriteria) | set(aturan)):
        if grade_id is not None and gid != grade_id:
            continue
        daftar.append(_susun_grade(gid, kriteria.get(gid), pita.get(gid),
                                   aturan.get(gid), kueri.get(gid)))

    bs = glob["grading.scoreWeights"]
    return {
        "grades": daftar,
        "elements": ELEMEN,
        "matchingElements": ELEMEN_SKOR,
        # Dua kunci di bawah dipertahankan dalam bentuk lamanya supaya UI yang
        # sudah membacanya tetap jalan; bentuk lengkap & yang bisa diubah ada
        # di `global`.
        "scoreWeights": {**bs["value"], "editable": True, "source": bs["source"],
                         "note": "Bobot skor mutu grading. Diubah lewat "
                                 "`global.grading.scoreWeights`."},
        "gradeECombinations": glob["grading.gradeECombinations"]["value"],
        "global": _susun_global(glob),
        "configVersion": _versi(_snapshot(kriteria, pita, aturan, kueri, glob)),
    }


def _susun_global(glob: dict) -> dict:
    hasil = {"grading": {}, "matching": {}, "meta": {}}
    for kunci, isi in glob.items():
        bagian, field = kunci.split(".", 1)
        hasil[bagian][field] = isi["value"]
        hasil["meta"][kunci] = {"source": isi["source"], "updatedAt": isi["updatedAt"],
                                "updatedBy": isi["updatedBy"]}
    hasil["analysis"] = {"warnings": analisis_global(
        {k: v["value"] for k, v in glob.items()})}
    return hasil


def _susun_grade(gid: int, kri: dict | None, pit: dict | None,
                 atr: dict | None, kueri: str | None = None) -> dict:
    hasil = {
        "gradeId": gid,
        "gradeLetter": (pit or {}).get("grade_letter") or HURUF.get(gid),
        "criteriaEditable": gid in GRADE_KONFIGURABEL,
        "note": CATATAN_TAK_KONFIGURABEL.get(gid),
    }

    hasil["criteria"] = None if not kri else {
        "order": kri["urutan"],
        "active": kri["aktif"],
        # wajib | terlarang | abaikan
        "nikColumn": kri["nik_kolom"],
        "minCompleteness": {e: kri[f"min_{e}"] for e in ELEMEN},
        "minNikTrusted": kri["min_nik_trusted"],
        "updatedAt": kri.get("diubah_at"),
        "updatedBy": kri.get("diubah_oleh"),
    }

    hasil["score"] = None if not pit else {
        "min": pit["score_min"],
        "max": pit["score_max"],
        "severityLabel": pit["severity_label"],
        "canProceed": pit["can_proceed"],
        "criteriaDescription": pit["criteria_description"],
    }

    hasil["matching"] = None if not atr else _susun_matching(gid, atr, kueri)
    return hasil


def _susun_matching(gid: int, atr: dict, kueri: str | None) -> dict:
    m = {
        "autoMissingMax": atr["auto_missing_max"],
        "autoScoreMin": atr["auto_score_min"],
        "reviewMissingCount": atr["review_missing_count"],
        "reviewScoreMin": atr["review_score_min"],
        "reviewScoreMax": atr["review_score_max"],
    }
    if gid in GRADE_MATCHING:
        # Objek JSON menjaga urutan sisipnya, dan urutan bobot MENENTUKAN hasil
        # penjumlahan pecahan (lihat _shared.py) — jadi urutannya dipertahankan.
        m["weights"] = {f: b for f, b in atr["bobot"]}
        m["missingElements"] = list(atr["elemen_kosong"])
        m["nameCleaning"] = dict(atr["bersih_nama"])
    else:
        m["weights"] = m["missingElements"] = m["nameCleaning"] = None
    m["blocking"] = {
        "query": kueri,
        "editable": False,
        "note": "Kueri blocking hanya bisa dibaca lewat API; diubah lewat "
                "basis data/migrasi.",
    }
    # Skor dihitung dari keluaran kueri blocking, jadi hanya elemen yang
    # dikeluarkannya yang bisa diberi bobot.
    m["availableElements"] = elemen_tersedia(kueri) if gid in GRADE_MATCHING else None
    m["analysis"] = analisis_matching(m) if gid in GRADE_MATCHING else None
    m["updatedAt"] = atr.get("diubah_at")
    m["updatedBy"] = atr.get("diubah_oleh")
    return m


# ── Analisis: apa akibat konfigurasi ini ───────────────────────────────────

def _skor_terbaik(bobot: dict, kosong: tuple) -> float:
    """
    Skor baris yang elemen `kosong`-nya kosong dan SISANYA cocok sempurna.

    Dihitung dengan aritmetika yang sama dengan SQL (`sql_skor`): pecahan
    persen/100, dijumlah berurutan dari kiri, lalu dikali 100. Hasilnya bisa
    89,99999999999999, bukan 90 — dan justru itu yang menentukan apakah baris
    masuk pita REVIEW yang batas atasnya 90.
    """
    total = 0.0
    for elemen, persen in bobot.items():
        if not persen:
            continue
        mirip = 0.0 if elemen in kosong else 1.0
        total = total + mirip * (float(persen) / 100)
    return total * 100


def _angka(x) -> str:
    if x is None:
        return "-"
    x = float(x)
    return f"{x:.16g}" if x != round(x, 2) else f"{round(x, 2):g}"


def analisis_matching(m: dict) -> dict:
    """
    Skor tertinggi per jumlah elemen kosong, dan peringatan yang menyertainya.

    `m` berbentuk bagian `matching` di API (weights, missingElements, ambang).
    Aturannya sama dengan SQL_KLASIFIKASI: AUTO dulu, lalu REVIEW.
    """
    bobot = m.get("weights") or {}
    kosong = list(m.get("missingElements") or [])
    amax, amin = m.get("autoMissingMax"), m.get("autoScoreMin")
    rcnt = m.get("reviewMissingCount")
    rmin, rmax = m.get("reviewScoreMin"), m.get("reviewScoreMax")

    per_n = {n: [(_skor_terbaik(bobot, s), s)
                 for s in itertools.combinations(kosong, n)]
             for n in range(len(kosong) + 1)}
    terbaik = {n: max(x for x, _ in isi) for n, isi in per_n.items()}

    def boleh_auto(n, skor):
        return amin is not None and (amax is None or n <= amax) and skor >= amin

    def boleh_review(n, skor):
        return ((rcnt is None or n == rcnt) and rmin is not None and rmax is not None
                and rmin <= skor < rmax)

    catatan = []
    if amin is not None and terbaik[0] < amin:
        catatan.append(
            f"AUTO tidak mungkin tercapai lewat skor: skor tertinggi "
            f"{_angka(terbaik[0])} di bawah autoScoreMin {_angka(amin)}.")

    if rcnt is not None:
        if rcnt > len(kosong):
            daftar = f" ({', '.join(kosong)})" if kosong else ""
            catatan.append(
                f"REVIEW tidak pernah terjadi: butuh tepat {rcnt} elemen kosong, "
                f"padahal elemen yang dihitung kosong hanya {len(kosong)}{daftar}.")
        elif rmin is not None and terbaik[rcnt] < rmin:
            catatan.append(
                f"REVIEW tidak mungkin tercapai: dengan {rcnt} elemen kosong, skor "
                f"tertinggi {_angka(terbaik[rcnt])} di bawah reviewScoreMin {_angka(rmin)}.")

    # Baris yang selebihnya cocok sempurna, tapi tidak lolos AUTO maupun REVIEW
    # -> UNMATCH. Hanya dilaporkan kalau skornya cukup tinggi untuk dianggap
    # kecocokan seandainya kosongnya diizinkan.
    batas = min((x for x in (amin, rmin) if x is not None), default=None)
    if batas is not None:
        for n in range(len(kosong) + 1):
            jatuh = [(skor, s) for skor, s in per_n[n]
                     if skor >= batas and not boleh_auto(n, skor)
                     and not boleh_review(n, skor)]
            if not jatuh:
                continue
            if amax is not None and n > amax:
                sebab_auto = f"tidak bisa AUTO (kosong {n} > autoMissingMax {amax})"
            else:
                sebab_auto = f"tidak bisa AUTO (skor di bawah autoScoreMin {_angka(amin)})"
            if rcnt is not None and n != rcnt:
                sebab_review = f"REVIEW hanya untuk tepat {rcnt} elemen kosong"
            else:
                sebab_review = (f"skornya di luar pita REVIEW "
                                f"{_angka(rmin)}–<{_angka(rmax)}")
            pola = "; ".join(f"{' + '.join(s)} kosong -> {_angka(x)}" for x, s in jatuh[:4])
            if len(jatuh) > 4:
                pola += f"; dan {len(jatuh) - 4} pola lain"
            catatan.append(
                f"Baris dengan {n} elemen kosong yang selebihnya cocok sempurna "
                f"jatuh ke UNMATCH: {sebab_auto}, dan {sebab_review}. ({pola})")

    return {
        "maxScoreByMissingCount": {str(n): terbaik[n] for n in sorted(terbaik)},
        "warnings": catatan,
    }


def analisis_global(nilai: dict) -> list[str]:
    catatan = []
    for kombinasi in nilai.get("grading.gradeECombinations") or []:
        if not isinstance(kombinasi, list):
            continue
        if "nama" not in kombinasi or "tanggal_lahir" not in kombinasi:
            catatan.append(
                f"Kombinasi grade E {kombinasi}: blocking grade E mensyaratkan nama "
                f"DAN tanggal lahir (3 huruf awal nama, hari & bulan lahir). Berkas "
                f"yang hanya memenuhi kombinasi ini tidak akan mendapat kandidat di "
                f"Pass 3.")
    eps = nilai.get("matching.conflictEpsilon")
    if isinstance(eps, (int, float)) and eps > 5:
        catatan.append(
            f"conflictEpsilon {_angka(eps)} poin cukup lebar: kandidat yang skornya "
            f"berbeda sebesar itu pun dianggap seri, sehingga CONFLICT akan banyak.")
    return catatan


# ── Validasi ───────────────────────────────────────────────────────────────

def validasi(kriteria: list[dict], pita: list[dict] | None = None) -> list[str]:
    """Daftar masalah. Kosong berarti konfigurasinya sehat."""
    masalah = []

    urutan = [k["urutan"] for k in kriteria]
    if len(set(urutan)) != len(urutan):
        masalah.append(f"Nilai `urutan` berulang: {sorted(urutan)}. "
                       "Urutan evaluasi jadi tidak tentu.")

    for k in kriteria:
        g = HURUF.get(k["grade_id"], k["grade_id"])
        if k["nik_kolom"] not in NIK_KOLOM_SAH:
            masalah.append(f"Grade {g}: nik_kolom '{k['nik_kolom']}' tidak sah "
                           f"(pilih salah satu dari {NIK_KOLOM_SAH}).")
        for kol in KOLOM_MIN:
            v = k.get(kol)
            if v is not None and not 0.0 <= v <= 1.0:
                masalah.append(f"Grade {g}: {kol} = {v}, harus antara 0 dan 1.")

        if k["nik_kolom"] == "terlarang":
            if k.get("min_nik") is not None:
                masalah.append(
                    f"Grade {g}: kolom NIK dinyatakan terlarang, tapi min_nik "
                    f"diisi {k['min_nik']}. Keduanya tidak bisa benar bersamaan.")
            if k.get("min_nik_trusted") is not None:
                masalah.append(
                    f"Grade {g}: kolom NIK dinyatakan terlarang, tapi "
                    f"min_nik_trusted diisi {k['min_nik_trusted']}. Tidak ada "
                    "NIK untuk dipercaya, jadi grade ini tak akan pernah cocok.")

    masalah += _cek_terjangkau(kriteria)

    if pita:
        masalah += _cek_pita(pita)
    return masalah


def _cek_terjangkau(kriteria: list[dict]) -> list[str]:
    """
    Grade yang dievaluasi belakangan harus LEBIH LONGGAR dari pendahulunya.

    Kalau tidak, setiap berkas yang lolos grade belakangan pasti sudah lolos
    yang lebih dulu, dan grade belakangan itu tidak akan pernah muncul. Hanya
    dibandingkan antar grade dengan syarat kolom NIK yang sama — A/B tidak
    sebanding dengan C/D karena yang satu mewajibkan kolom NIK dan yang lain
    melarangnya.
    """
    masalah = []
    urut = sorted(kriteria, key=lambda k: k["urutan"])

    for i, nanti in enumerate(urut):
        for lebih_dulu in urut[:i]:
            if nanti["nik_kolom"] != lebih_dulu["nik_kolom"]:
                continue
            # NULL = tanpa syarat = paling longgar.
            if all((nanti.get(kol) or 0.0) >= (lebih_dulu.get(kol) or 0.0)
                   for kol in KOLOM_MIN):
                masalah.append(
                    f"Grade {HURUF.get(nanti['grade_id'])} tidak akan pernah "
                    f"tercapai: seluruh ambangnya sama ketat atau lebih ketat "
                    f"dari grade {HURUF.get(lebih_dulu['grade_id'])} yang "
                    f"dievaluasi lebih dulu, sehingga berkas selalu tertangkap "
                    f"di sana."
                )
                break
    return masalah


def _cek_pita(pita: list[dict]) -> list[str]:
    masalah = []
    for p in pita:
        g = p.get("grade_letter") or HURUF.get(p["grade_id"])
        if p["score_min"] > p["score_max"]:
            masalah.append(f"Grade {g}: score_min ({p['score_min']}) melebihi "
                           f"score_max ({p['score_max']}).")
        if not (0 <= p["score_min"] <= 100 and 0 <= p["score_max"] <= 100):
            masalah.append(f"Grade {g}: pita skor di luar 0-100.")

    urut = sorted(pita, key=lambda p: p["score_min"])
    for a, b in zip(urut, urut[1:]):
        if b["score_min"] <= a["score_max"]:
            masalah.append(
                f"Pita skor tumpang tindih: grade "
                f"{a.get('grade_letter')} ({a['score_min']}-{a['score_max']}) "
                f"dan {b.get('grade_letter')} ({b['score_min']}-{b['score_max']})."
            )
    return masalah


def _angka_sah(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validasi_matching(gid: int, m: dict) -> list[str]:
    """Masalah pada bagian `matching` satu grade (bentuk API)."""
    g = HURUF.get(gid, gid)
    masalah = []

    # Wajib angka: di rumus klasifikasi, NULL membuat AUTO/REVIEW tak pernah
    # terjadi tanpa satu pun pesan galat.
    for kunci in ("autoScoreMin", "reviewScoreMin", "reviewScoreMax"):
        v = m.get(kunci)
        if not _angka_sah(v) or not 0 <= v <= 100:
            masalah.append(f"Grade {g}: {kunci} = {v!r}, harus angka 0-100.")
    for kunci in ("autoMissingMax", "reviewMissingCount"):
        v = m.get(kunci)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            masalah.append(f"Grade {g}: {kunci} = {v!r}, harus bilangan bulat >= 0 "
                           f"atau null (tanpa syarat).")
    rmin, rmax = m.get("reviewScoreMin"), m.get("reviewScoreMax")
    if _angka_sah(rmin) and _angka_sah(rmax) and rmin > rmax:
        masalah.append(f"Grade {g}: reviewScoreMin ({rmin}) melebihi "
                       f"reviewScoreMax ({rmax}).")

    if gid not in GRADE_MATCHING:
        return masalah

    bobot = m.get("weights")
    if not isinstance(bobot, dict) or not bobot:
        masalah.append(f"Grade {g}: weights harus berisi minimal satu elemen.")
    else:
        for elemen, persen in bobot.items():
            if elemen not in ELEMEN_SKOR:
                masalah.append(
                    f"Grade {g}: elemen bobot '{elemen}' tidak dikenali. Yang "
                    f"tersedia: {ELEMEN_SKOR}" + (
                        " — NIK dipakai untuk blocking & Pass 1 (cocok persis), "
                        "bukan untuk skor kemiripan." if elemen == "nik" else "") + (
                        " — master tidak punya kolom alamat; pakai 'wilayah' "
                        "(rata-rata provinsi/kabupaten/kecamatan/kelurahan)."
                        if elemen == "alamat" else ""))
            elif not _angka_sah(persen) or not 0 <= persen <= 100:
                masalah.append(f"Grade {g}: bobot {elemen} = {persen!r}, harus "
                               f"angka 0-100 (persen).")
        if all(_angka_sah(p) for p in bobot.values()):
            jumlah = sum(bobot.values())
            if abs(jumlah - 100) > TOLERANSI_JUMLAH:
                masalah.append(f"Grade {g}: jumlah bobot {_angka(jumlah)}, harus 100.")

    kosong = m.get("missingElements")
    if not isinstance(kosong, list):
        masalah.append(f"Grade {g}: missingElements harus daftar elemen.")
    else:
        for elemen in kosong:
            if elemen not in ELEMEN_SKOR:
                masalah.append(f"Grade {g}: elemen kosong '{elemen}' tidak dikenali. "
                               f"Yang tersedia: {ELEMEN_SKOR}")
        if len(set(map(str, kosong))) != len(kosong):
            masalah.append(f"Grade {g}: missingElements memuat elemen berulang.")

    # Elemen yang tidak dikeluarkan kueri blocking tidak bisa dihitung sama sekali.
    kueri = (m.get("blocking") or {}).get("query")
    kolom = kolom_blocking(kueri) if kueri else None
    if (kolom is not None and isinstance(bobot, dict)
            and all(_angka_sah(p) for p in bobot.values())
            and isinstance(kosong, list)
            and all(e in ELEMEN_SKOR for e in list(bobot) + kosong)):
        for elemen, kol in kolom_kurang(kolom, list(bobot.items()), kosong).items():
            masalah.append(
                f"Grade {g}: elemen '{elemen}' tidak dikeluarkan kueri blocking grade "
                f"ini (kolom {', '.join(kol)} tidak ada), jadi tidak bisa diberi bobot "
                f"atau dihitung kosong. Kueri blocking diubah lewat basis data/migrasi "
                f"(tabel matching_queries).")

    bersih = m.get("nameCleaning")
    if not isinstance(bersih, dict):
        masalah.append(f"Grade {g}: nameCleaning harus objek.")
    else:
        for kunci, nilai in bersih.items():
            if kunci not in KUNCI_BERSIH:
                masalah.append(f"Grade {g}: nameCleaning.{kunci} tidak dikenali. "
                               f"Yang tersedia: {list(KUNCI_BERSIH)}")
            elif not isinstance(nilai, bool):
                masalah.append(f"Grade {g}: nameCleaning.{kunci} harus true/false.")
    return masalah


def validasi_global(nilai: dict) -> list[str]:
    masalah = []

    bs = nilai.get("grading.scoreWeights")
    if not isinstance(bs, dict) or set(bs) != set(BOBOT_SKOR):
        masalah.append(f"grading.scoreWeights harus objek dengan tepat kunci "
                       f"{sorted(BOBOT_SKOR)}.")
    elif not all(_angka_sah(v) and 0 <= v <= 1 for v in bs.values()):
        masalah.append("grading.scoreWeights: setiap bobot harus angka 0-1.")
    elif abs(sum(bs.values()) - 1) > TOLERANSI_JUMLAH:
        masalah.append(f"grading.scoreWeights: jumlah bobot {_angka(sum(bs.values()))}, "
                       f"harus 1.")

    ke = nilai.get("grading.gradeECombinations")
    if not isinstance(ke, list) or not ke:
        masalah.append("grading.gradeECombinations harus daftar berisi minimal satu "
                       "kombinasi.")
    else:
        for kombinasi in ke:
            if not isinstance(kombinasi, list) or not kombinasi:
                masalah.append(f"Kombinasi grade E {kombinasi!r} harus daftar elemen "
                               f"yang tidak kosong.")
                continue
            asing = [e for e in kombinasi if e not in ELEMEN_KOMBINASI_E]
            if asing:
                masalah.append(f"Kombinasi grade E {kombinasi}: elemen {asing} tidak "
                               f"dikenali. Yang tersedia: {ELEMEN_KOMBINASI_E}")
            if len(set(map(str, kombinasi))) != len(kombinasi):
                masalah.append(f"Kombinasi grade E {kombinasi} memuat elemen berulang.")

    eps = nilai.get("matching.conflictEpsilon")
    if not _angka_sah(eps) or not 0 <= eps <= 100:
        masalah.append(f"matching.conflictEpsilon = {eps!r}, harus angka 0-100 "
                       f"(poin skor).")
    jw = nilai.get("matching.contradictionJw")
    if not _angka_sah(jw) or not 0 <= jw <= 1:
        masalah.append(f"matching.contradictionJw = {jw!r}, harus angka 0-1.")
    return masalah


# ── Penulisan ──────────────────────────────────────────────────────────────

# Nama di muatan API -> nama kolom di tabel.
PETA_KRITERIA = {
    "order": "urutan",
    "active": "aktif",
    "nikColumn": "nik_kolom",
    "minNikTrusted": "min_nik_trusted",
}
PETA_SKOR = {
    "min": "score_min", "max": "score_max",
    "severityLabel": "severity_label", "canProceed": "can_proceed",
    "criteriaDescription": "criteria_description",
}
PETA_MATCHING = {
    "autoMissingMax": "auto_missing_max", "autoScoreMin": "auto_score_min",
    "reviewMissingCount": "review_missing_count",
    "reviewScoreMin": "review_score_min", "reviewScoreMax": "review_score_max",
}
# Bagian matching yang disimpan sebagai JSONB.
PETA_MATCHING_JSON = {
    "weights": "bobot",
    "missingElements": "elemen_kosong",
    "nameCleaning": "bersih_nama",
}
KOLOM_JSONB = set(PETA_MATCHING_JSON.values())


def _normal_bobot(nilai) -> dict | None:
    """
    Terima {elemen: persen} atau [[elemen, persen], ...]; kembalikan objek
    BERURUTAN. `null` = kembali ke bawaan grade.
    """
    if nilai is None:
        return None
    if isinstance(nilai, dict):
        return dict(nilai)
    if isinstance(nilai, list):
        hasil = {}
        for p in nilai:
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                raise ValueError("`weights` berbentuk daftar harus [[elemen, persen], ...].")
            if p[0] in hasil:
                raise ValueError(f"`weights` memuat elemen '{p[0]}' dua kali.")
            hasil[p[0]] = p[1]
        return hasil
    raise ValueError("`weights` harus objek {elemen: persen} atau "
                     "[[elemen, persen], ...].")


def perbarui(con, grade_id: int, patch: dict, oleh: str | None = None,
             dry_run: bool = False) -> dict:
    """
    Terapkan perubahan SEBAGIAN pada satu grade.

    Hanya field yang disebut yang berubah; sisanya dibiarkan. Pengecualiannya
    `matching.weights` dan `matching.missingElements`: keduanya MENGGANTI
    seluruh isinya, karena bobot harus berjumlah 100 — menggabungkan sebagian
    bobot dengan yang lama hampir selalu menghasilkan jumlah yang salah.
    `matching.nameCleaning` digabung per sakelar.

    Seluruh konfigurasi divalidasi SESUDAH perubahan digabung tapi SEBELUM
    ditulis, jadi konfigurasi yang merusak tidak pernah sempat tersimpan.
    """
    sebelum = baca_semua(con, grade_id)["grades"]
    if not sebelum:
        raise ValueError(f"Grade {grade_id} tidak ada.")
    sebelum = sebelum[0]

    set_kriteria = _kumpulkan_kriteria(patch.get("criteria"), grade_id, sebelum)
    set_skor = _kumpulkan(patch.get("score"), PETA_SKOR)
    set_matching, patch_matching = _kumpulkan_matching(
        patch.get("matching"), grade_id, sebelum)

    if not (set_kriteria or set_skor or set_matching):
        raise ValueError(
            "Tidak ada yang diubah. Sertakan minimal satu dari "
            "`criteria`, `score`, atau `matching`.")

    # Simulasikan hasilnya lebih dulu, lalu validasi seluruh konfigurasi.
    kriteria = baca_kriteria_penuh(con)
    for k in kriteria:
        if k["grade_id"] == grade_id:
            k.update({kol: nil for kol, nil in set_kriteria.items()})
    pita = _baris_jadi_dict(con.execute("""
        SELECT grade_id, grade_letter, score_min, score_max FROM pg.public.grade_bands
    """))
    for p in pita:
        if p["grade_id"] == grade_id:
            for kol in ("score_min", "score_max"):
                if kol in set_skor:
                    p[kol] = set_skor[kol]

    simulasi = _gabung(sebelum, {**patch, "matching": patch_matching})
    masalah = validasi(kriteria, pita)
    if simulasi.get("matching") is not None:
        masalah += validasi_matching(grade_id, simulasi["matching"])
    peringatan = ((simulasi.get("matching") or {}).get("analysis") or {}).get("warnings", [])

    if masalah:
        return {"applied": False, "dryRun": dry_run, "problems": masalah,
                "warnings": peringatan, "before": sebelum, "after": None}

    versi = None
    if dry_run:
        # Hasil simulasi, BUKAN pembacaan ulang basis data. Membaca ulang saat
        # dry run mengembalikan keadaan yang belum berubah, sehingga `changed`
        # selalu kosong — tombol "periksa" di UI jadi tidak memberi tahu
        # apa pun tentang apa yang akan terjadi.
        sesudah = simulasi
    else:
        if set_kriteria:
            set_kriteria["diubah_at"] = "now()"
            set_kriteria["diubah_oleh"] = oleh
            _tulis(con, "grade_criteria", "grade_id", grade_id, set_kriteria)
        if set_skor:
            _tulis(con, "grade_bands", "grade_id", grade_id, set_skor)
        if set_matching:
            set_matching["diubah_at"] = "now()"
            set_matching["diubah_oleh"] = oleh
            _tulis(con, "grade_rules", "grade_code", grade_id, set_matching)
        sesudah = baca_semua(con, grade_id)["grades"][0]

    ubah = _beda(sebelum, sesudah)
    if not dry_run and ubah:
        versi = rekam_versi(con)
        _catat_riwayat(con, "grade", grade_id, oleh, ubah, versi)
    return {"applied": not dry_run, "dryRun": dry_run, "problems": [],
            "warnings": peringatan, "before": sebelum, "after": sesudah,
            "changed": ubah, "configVersion": versi}


def perbarui_global(con, patch: dict, oleh: str | None = None,
                    dry_run: bool = False) -> dict:
    """
    Ubah nilai global: `{"grading": {...}, "matching": {...}}`.

    Nilai `null` menghapus setelan — kembali ke env/bawaan. `scoreWeights`
    boleh sebagian (digabung dengan yang berlaku); sisanya diganti utuh.
    """
    glob = baca_global(con)
    sebelum = _susun_global(glob)
    efektif = {k: v["value"] for k, v in glob.items()}

    ubah: dict = {}
    for bagian in ("grading", "matching"):
        isi = patch.get(bagian)
        if isi is None:
            continue
        if not isinstance(isi, dict):
            raise ValueError(f"`{bagian}` harus objek.")
        for field, nilai in isi.items():
            kunci = f"{bagian}.{field}"
            if kunci not in GLOBAL:
                raise ValueError(f"Field '{kunci}' tidak dikenali. Yang tersedia: "
                                 f"{sorted(GLOBAL)}")
            ubah[kunci] = nilai
    if not ubah:
        raise ValueError("Tidak ada yang diubah. Sertakan `grading` dan/atau "
                         "`matching`.")

    for kunci, nilai in ubah.items():
        if nilai is None:
            efektif[kunci] = GLOBAL[kunci]()[0]
        elif kunci == "grading.scoreWeights" and isinstance(nilai, dict):
            efektif[kunci] = {**efektif[kunci], **nilai}
            ubah[kunci] = efektif[kunci]
        else:
            efektif[kunci] = nilai

    masalah = validasi_global(efektif)
    peringatan = analisis_global(efektif)
    if masalah:
        return {"applied": False, "dryRun": dry_run, "problems": masalah,
                "warnings": peringatan, "before": sebelum, "after": None}

    versi = None
    if dry_run:
        simulasi = {}
        for k, isi in glob.items():
            if k in ubah:
                sumber = GLOBAL[k]()[1] if ubah[k] is None else "config"
                simulasi[k] = {**isi, "value": efektif[k], "source": sumber}
            else:
                simulasi[k] = isi
        sesudah = _susun_global(simulasi)
    else:
        for kunci, nilai in ubah.items():
            if nilai is None:
                jalankan_pg(con, f"DELETE FROM engine_config WHERE kunci = {q(kunci)}")
            else:
                jalankan_pg(con, f"""
                    INSERT INTO engine_config (kunci, nilai, diubah_at, diubah_oleh)
                    VALUES ({q(kunci)}, {q(json.dumps(nilai))}::jsonb, now(), {q(oleh)})
                    ON CONFLICT (kunci) DO UPDATE
                       SET nilai = EXCLUDED.nilai, diubah_at = now(),
                           diubah_oleh = EXCLUDED.diubah_oleh""")
        sesudah = _susun_global(baca_global(con))

    def ambil(susunan: dict, kunci: str) -> tuple:
        bagian, field = kunci.split(".", 1)
        return susunan[bagian][field], susunan["meta"][kunci]["source"]

    perubahan = []
    for k in ubah:
        (lama, sumber_lama), (baru, sumber_baru) = ambil(sebelum, k), ambil(sesudah, k)
        # Sumber ikut dihitung: menyimpan nilai yang kebetulan sama dengan
        # bawaan tetap mengubah perilaku saat bawaan/env berubah kelak.
        if lama != baru or sumber_lama != sumber_baru:
            perubahan.append({"section": "global", "field": k,
                              "from": lama, "to": baru})
    if not dry_run and perubahan:
        versi = rekam_versi(con)
        _catat_riwayat(con, "global", None, oleh, perubahan, versi)
    return {"applied": not dry_run, "dryRun": dry_run, "problems": [],
            "warnings": peringatan, "before": sebelum, "after": sesudah,
            "changed": perubahan, "configVersion": versi}


def _gabung(sebelum: dict, patch: dict) -> dict:
    """Salinan `sebelum` dengan patch diterapkan — untuk validasi & dry run."""
    hasil = json.loads(json.dumps(sebelum, default=str))
    for bagian in ("criteria", "score", "matching"):
        isi = patch.get(bagian)
        if not isi:
            continue
        tujuan = dict(hasil.get(bagian) or {})
        for kunci, nilai in isi.items():
            if kunci in ("minCompleteness", "nameCleaning") and isinstance(nilai, dict):
                gabungan = dict(tujuan.get(kunci) or {})
                gabungan.update(nilai)
                tujuan[kunci] = gabungan
            else:
                tujuan[kunci] = nilai
        hasil[bagian] = tujuan
    if hasil.get("matching") is not None and hasil["gradeId"] in GRADE_MATCHING:
        hasil["matching"]["analysis"] = analisis_matching(hasil["matching"])
    return hasil


def baca_kriteria_penuh(con) -> list[dict]:
    """Semua baris kriteria, termasuk yang nonaktif — untuk validasi."""
    return _baris_jadi_dict(con.execute(f"""
        SELECT grade_id, urutan, nik_kolom, {', '.join(KOLOM_MIN)}, aktif
          FROM pg.public.grade_criteria ORDER BY urutan
    """))


def _kumpulkan(bagian: dict | None, peta: dict) -> dict:
    if not bagian:
        return {}
    keluar = {}
    for kunci, nilai in bagian.items():
        if kunci not in peta:
            raise ValueError(
                f"Field '{kunci}' tidak dikenali. Yang tersedia: {sorted(peta)}")
        keluar[peta[kunci]] = nilai
    return keluar


def _kumpulkan_matching(bagian: dict | None, grade_id: int,
                        sebelum: dict) -> tuple[dict, dict]:
    """
    (kolom -> nilai untuk ditulis, patch berbentuk API untuk simulasi).

    `null` pada weights/missingElements/nameCleaning berarti kembali ke bawaan
    grade: kolomnya dikosongkan, dan simulasinya memakai nilai bawaan.
    """
    if not bagian:
        return {}, {}
    keluar, patch = {}, {}
    for kunci, nilai in bagian.items():
        if kunci == "blocking":
            raise ValueError(
                "`matching.blocking` hanya bisa dibaca lewat API. Kueri blocking "
                "diubah lewat basis data/migrasi (tabel matching_queries).")
        if kunci in ("analysis", "availableElements", "updatedAt", "updatedBy"):
            raise ValueError(f"`matching.{kunci}` dihitung sistem, tidak bisa diubah.")
        if kunci in PETA_MATCHING:
            keluar[PETA_MATCHING[kunci]] = nilai
            patch[kunci] = nilai
            continue
        if kunci not in PETA_MATCHING_JSON:
            raise ValueError(
                f"Field 'matching.{kunci}' tidak dikenali. Yang tersedia: "
                f"{sorted(list(PETA_MATCHING) + list(PETA_MATCHING_JSON))}")
        if grade_id not in GRADE_MATCHING:
            raise ValueError(
                f"Grade {HURUF.get(grade_id)} tidak dicocokkan, jadi tidak punya "
                f"`matching.{kunci}`.")

        kolom = PETA_MATCHING_JSON[kunci]
        if kunci == "weights":
            bobot = _normal_bobot(nilai)
            keluar[kolom] = None if bobot is None else [[f, b] for f, b in bobot.items()]
            patch[kunci] = (bobot if bobot is not None
                            else {f: b for f, b in BOBOT_BAWAAN[grade_id]})
        elif kunci == "missingElements":
            if nilai is not None and not isinstance(nilai, list):
                raise ValueError("`missingElements` harus daftar elemen.")
            keluar[kolom] = nilai
            patch[kunci] = nilai if nilai is not None else list(MISSING[grade_id])
        else:  # nameCleaning — digabung per sakelar
            if nilai is not None and not isinstance(nilai, dict):
                raise ValueError("`nameCleaning` harus objek {titles, patronym, "
                                 "abbreviations}.")
            if nilai is None:
                keluar[kolom] = None
                # Kembali ke bawaan = SEMUA sakelar bawaan. Karena _gabung
                # menggabung per sakelar, simulasinya memakai bawaan utuh.
                patch[kunci] = dict(BERSIH_NAMA_BAWAAN)
            else:
                lama = dict((sebelum.get("matching") or {}).get("nameCleaning") or {})
                keluar[kolom] = {**lama, **nilai}
                patch[kunci] = nilai
    return keluar, patch


def _kumpulkan_kriteria(bagian: dict | None, grade_id: int, sebelum: dict) -> dict:
    if not bagian:
        return {}
    if grade_id not in GRADE_KONFIGURABEL:
        raise ValueError(
            f"Grade {HURUF.get(grade_id)} tidak punya kriteria yang bisa "
            f"disetel. {CATATAN_TAK_KONFIGURABEL.get(grade_id, '')}")

    keluar = {}
    for kunci, nilai in bagian.items():
        if kunci == "minCompleteness":
            if not isinstance(nilai, dict):
                raise ValueError("`minCompleteness` harus objek {elemen: nilai}.")
            for el, v in nilai.items():
                if el not in ELEMEN:
                    raise ValueError(
                        f"Elemen '{el}' tidak dikenali. Yang tersedia: {ELEMEN}")
                keluar[f"min_{el}"] = v
        elif kunci in PETA_KRITERIA:
            keluar[PETA_KRITERIA[kunci]] = nilai
        else:
            raise ValueError(
                f"Field '{kunci}' tidak dikenali. Yang tersedia: "
                f"{sorted(list(PETA_KRITERIA) + ['minCompleteness'])}")
    return keluar


def _tulis(con, tabel: str, kol_kunci: str, kunci, nilai: dict) -> None:
    def ekspresi(k, v):
        # `now()` diteruskan sebagai ekspresi SQL, bukan literal string.
        if v == "now()" and k.endswith("_at"):
            return f"{k} = now()"
        if k in KOLOM_JSONB:
            return f"{k} = NULL" if v is None else f"{k} = {q(json.dumps(v))}::jsonb"
        return f"{k} = {q(v)}"

    bagian = ", ".join(ekspresi(k, v) for k, v in nilai.items())
    jalankan_pg(con, f"UPDATE {tabel} SET {bagian} "
                     f"WHERE {kol_kunci} = {q(kunci)}")


# Bagian turunan yang tidak dilaporkan sebagai perubahan.
_BUKAN_PERUBAHAN = ("updatedAt", "updatedBy", "analysis", "blocking",
                    "availableElements")


def _beda(sebelum: dict, sesudah: dict) -> list[dict]:
    """
    Daftar field yang benar-benar berubah — untuk jejak audit di UI.

    Objek (`minCompleteness`, `weights`, `nameCleaning`) ditelusuri sampai ke
    isinya. Melaporkannya sebagai satu objek utuh membuat catatan audit
    menampilkan seluruh isinya setiap kali satu di antaranya digeser, dan yang
    benar-benar berubah jadi tenggelam.
    """
    ubah = []
    for bagian in ("criteria", "score", "matching"):
        a, b = sebelum.get(bagian) or {}, sesudah.get(bagian) or {}
        for kunci in sorted(set(a) | set(b)):
            if kunci in _BUKAN_PERUBAHAN:
                continue

            va, vb = a.get(kunci), b.get(kunci)
            if isinstance(va, dict) or isinstance(vb, dict):
                ea, eb = va or {}, vb or {}
                for isi in sorted(set(ea) | set(eb)):
                    if ea.get(isi) != eb.get(isi):
                        ubah.append({"section": bagian, "field": f"{kunci}.{isi}",
                                     "from": ea.get(isi), "to": eb.get(isi)})
                if list(ea) != list(eb) and set(ea) == set(eb) and ea == eb:
                    # Isi sama, urutan berbeda — urutan bobot memengaruhi hasil.
                    ubah.append({"section": bagian, "field": f"{kunci}(urutan)",
                                 "from": list(ea), "to": list(eb)})
                continue

            if va != vb:
                ubah.append({"section": bagian, "field": kunci,
                             "from": va, "to": vb})
    return ubah


# ── Jejak: riwayat & versi ─────────────────────────────────────────────────

def _snapshot(kriteria: dict, pita: dict, aturan: dict, kueri: dict,
              glob: dict) -> dict:
    """Isi konfigurasi yang MENENTUKAN hasil — tanpa cap waktu & penyunting."""
    return {
        "criteria": {
            str(g): {"order": k["urutan"], "active": k["aktif"],
                     "nikColumn": k["nik_kolom"],
                     **{kol: k[kol] for kol in KOLOM_MIN}}
            for g, k in sorted(kriteria.items())
        },
        "bands": {
            str(g): {"min": p["score_min"], "max": p["score_max"],
                     "severityLabel": p["severity_label"],
                     "canProceed": p["can_proceed"],
                     "criteriaDescription": p["criteria_description"]}
            for g, p in sorted(pita.items())
        },
        "matching": {
            str(g): {**{k: a[k] for k in KOLOM_ATURAN},
                     # daftar pasangan: urutan bobot ikut menentukan hasil
                     "weights": a["bobot"],
                     "missingElements": a["elemen_kosong"],
                     "nameCleaning": a["bersih_nama"],
                     "blockingQuery": kueri.get(g)}
            for g, a in sorted(aturan.items())
        },
        "global": {k: v["value"] for k, v in sorted(glob.items())},
    }


def _versi(snapshot: dict) -> str:
    teks = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(teks.encode("utf-8")).hexdigest()[:12]


def susun_snapshot(con) -> dict:
    kriteria, pita = _baca_tabel_grade(con)
    return _snapshot(kriteria, pita, _baca_aturan(con), _baca_kueri(con),
                     baca_global(con))


def rekam_versi(con) -> str:
    """
    Sidik konfigurasi yang berlaku sekarang, dan simpan isinya bila baru.

    Dipanggil setiap job. Gagal MENYIMPAN tidak menggagalkan job — jejak audit
    tidak boleh menjadi alasan data tidak diproses; sidiknya tetap dikembalikan.
    """
    snap = susun_snapshot(con)
    versi = _versi(snap)
    try:
        jalankan_pg(con, f"""
            INSERT INTO config_versi (versi, isi)
            VALUES ({q(versi)}, {q(json.dumps(snap, ensure_ascii=False, default=str))}::jsonb)
            ON CONFLICT (versi) DO NOTHING""")
    except Exception as e:  # noqa: BLE001
        print(f"[config] versi {versi} gagal disimpan: {e}")
    return versi


def baca_versi(con, versi: str) -> dict | None:
    r = _baca_pg(con, f"""
        SELECT isi, CAST(pertama_dipakai AS VARCHAR)
          FROM pg.public.config_versi WHERE versi = {q(versi)}""").fetchone()
    if not r:
        return None
    return {"configVersion": versi, "firstUsedAt": r[1], "config": _json(r[0])}


def _catat_riwayat(con, cakupan: str, grade_id: int | None, oleh: str | None,
                   perubahan: list, versi: str | None) -> None:
    jalankan_pg(con, f"""
        INSERT INTO config_riwayat (oleh, cakupan, grade_id, perubahan, versi)
        VALUES ({q(oleh)}, {q(cakupan)}, {q(grade_id)},
                {q(json.dumps(perubahan, ensure_ascii=False, default=str))}::jsonb,
                {q(versi)})""")


def baca_riwayat(con, grade_id: int | None = None, batas: int = 50) -> list[dict]:
    """Perubahan konfigurasi terbaru lebih dulu."""
    syarat = f"WHERE grade_id = {int(grade_id)}" if grade_id is not None else ""
    kur = _baca_pg(con, f"""
        SELECT id, CAST(waktu AS VARCHAR) AS waktu, oleh, cakupan, grade_id,
               perubahan, versi
          FROM pg.public.config_riwayat {syarat}
         ORDER BY id DESC
         LIMIT {max(1, min(int(batas), 500))}""")
    return [{
        "id": r["id"], "at": r["waktu"], "by": r["oleh"], "scope": r["cakupan"],
        "gradeId": r["grade_id"],
        "gradeLetter": HURUF.get(r["grade_id"]) if r["grade_id"] else None,
        "changes": _json(r["perubahan"]), "configVersion": r["versi"],
    } for r in _baris_jadi_dict(kur)]
