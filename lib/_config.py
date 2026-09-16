"""
Konfigurasi aturan grading & matching — baca, validasi, tulis.

Dipakai dua pihak yang berbeda kebutuhan:

  * mesin grading  -> `baca_kriteria()`, saat menilai berkas
  * API konfigurasi -> `baca_semua()` dan `perbarui()`, untuk menu di UI

Tiga tabel yang ditampilkan sebagai satu kesatuan, karena itulah yang dilihat
pengguna sebagai "aturan grade":

    grade_criteria  ambang kelengkapan & mutu NIK   (A-D, bisa diedit)
    grade_bands     pita skor, label, kelayakan     (A-F)
    grade_rules     ambang similarity saat matching (1-6)

VALIDASI BUKAN HIASAN

Tabel yang bisa diedit dari UI berarti angkanya bisa dibuat saling
bertentangan. Yang paling berbahaya bukan nilai di luar rentang — itu sudah
ditolak CHECK constraint — melainkan aturan yang membuat sebuah grade TIDAK
PERNAH TERCAPAI. Kalau ambang B dibuat sama ketat atau lebih ketat dari A,
setiap berkas yang lolos B pasti sudah lolos A lebih dulu, dan B mati diam-diam
tanpa satu pun pesan galat. `validasi()` menangkap keadaan itu.
"""

from __future__ import annotations

import json

from _jobs import jalankan_pg, q

# Urutan ini dipakai di seluruh muatan API.
ELEMEN = ["nik", "nama", "tempat_lahir", "tanggal_lahir",
          "jenis_kelamin", "nama_ibu"]

KOLOM_MIN = [f"min_{e}" for e in ELEMEN] + ["min_nik_trusted"]

NIK_KOLOM_SAH = ("wajib", "terlarang", "abaikan")

HURUF = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}

# Grade yang kriterianya berbentuk tabel. E dan F sengaja di luar — lihat
# komentar di infra/schema_config.sql.
GRADE_KONFIGURABEL = (1, 2, 3, 4)

CATATAN_TAK_KONFIGURABEL = {
    5: ("Grade E memakai KOMBINASI kolom yang harus ada, bukan ambang "
        "persentase, sehingga bentuknya tidak muat di tabel ini. Masih di kode."),
    6: ("Grade F bukan aturan melainkan hasil: tidak satu pun kriteria di atas "
        "terpenuhi. Tidak ada yang bisa disetel."),
}

# Kombinasi grade E, untuk DITAMPILKAN saja di UI (belum bisa diedit).
KOMBINASI_E = [
    ["nama", "tanggal_lahir", "jenis_kelamin"],
    ["nama", "tempat_lahir", "tanggal_lahir"],
    ["nama", "tempat_lahir", "nama_ibu"],
    ["nama", "tanggal_lahir", "wilayah"],
]

# Bobot rumus skor. Ditampilkan API tapi belum bisa diedit — mengubahnya
# menggeser SEMUA skor sekaligus, jadi diputuskan terpisah.
BOBOT_SKOR = {"kelengkapan": 0.6, "nik_tepercaya": 0.4}


# ── Pembacaan ──────────────────────────────────────────────────────────────

def _baris_jadi_dict(kur) -> list[dict]:
    nama = [d[0] for d in kur.description]
    return [dict(zip(nama, b)) for b in kur.fetchall()]


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


def baca_semua(con, grade_id: int | None = None) -> dict:
    """Gambaran utuh per grade — kriteria, pita skor, dan ambang matching."""
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

    aturan = {r["grade_code"]: r for r in _baris_jadi_dict(con.execute("""
        SELECT grade_code, auto_missing_max, auto_score_min,
               review_missing_count, review_score_min, review_score_max
          FROM pg.public.grade_rules
    """))}

    daftar = []
    for gid in sorted(set(pita) | set(kriteria) | set(aturan)):
        if grade_id is not None and gid != grade_id:
            continue
        daftar.append(_susun_grade(gid, kriteria.get(gid), pita.get(gid),
                                   aturan.get(gid)))

    return {
        "grades": daftar,
        "elements": ELEMEN,
        "scoreWeights": {**BOBOT_SKOR, "editable": False,
                         "note": "Mengubah bobot menggeser seluruh skor "
                                 "sekaligus; belum dibuka lewat API."},
        "gradeECombinations": KOMBINASI_E,
    }


def _susun_grade(gid: int, kri: dict | None, pit: dict | None,
                 atr: dict | None) -> dict:
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

    hasil["matching"] = None if not atr else {
        "autoMissingMax": atr["auto_missing_max"],
        "autoScoreMin": atr["auto_score_min"],
        "reviewMissingCount": atr["review_missing_count"],
        "reviewScoreMin": atr["review_score_min"],
        "reviewScoreMax": atr["review_score_max"],
    }
    return hasil


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


def perbarui(con, grade_id: int, patch: dict, oleh: str | None = None,
             dry_run: bool = False) -> dict:
    """
    Terapkan perubahan SEBAGIAN pada satu grade.

    Hanya field yang disebut yang berubah; sisanya dibiarkan. Seluruh
    konfigurasi divalidasi SESUDAH perubahan digabung tapi SEBELUM ditulis,
    jadi konfigurasi yang merusak tidak pernah sempat tersimpan.
    """
    sebelum = baca_semua(con, grade_id)["grades"]
    if not sebelum:
        raise ValueError(f"Grade {grade_id} tidak ada.")
    sebelum = sebelum[0]

    set_kriteria = _kumpulkan_kriteria(patch.get("criteria"), grade_id, sebelum)
    set_skor = _kumpulkan(patch.get("score"), PETA_SKOR)
    set_matching = _kumpulkan(patch.get("matching"), PETA_MATCHING)

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

    masalah = validasi(kriteria, pita)
    if masalah:
        return {"applied": False, "dryRun": dry_run, "problems": masalah,
                "before": sebelum, "after": None}

    if dry_run:
        # Hasil simulasi, BUKAN pembacaan ulang basis data. Membaca ulang saat
        # dry run mengembalikan keadaan yang belum berubah, sehingga `changed`
        # selalu kosong — tombol "periksa" di UI jadi tidak memberi tahu
        # apa pun tentang apa yang akan terjadi.
        sesudah = _gabung(sebelum, patch)
    else:
        if set_kriteria:
            set_kriteria["diubah_at"] = "now()"
            set_kriteria["diubah_oleh"] = oleh
            _tulis(con, "grade_criteria", "grade_id", grade_id, set_kriteria)
        if set_skor:
            _tulis(con, "grade_bands", "grade_id", grade_id, set_skor)
        if set_matching:
            _tulis(con, "grade_rules", "grade_code", grade_id, set_matching)
        sesudah = baca_semua(con, grade_id)["grades"][0]
    return {"applied": not dry_run, "dryRun": dry_run, "problems": [],
            "before": sebelum, "after": sesudah,
            "changed": _beda(sebelum, sesudah)}


def _gabung(sebelum: dict, patch: dict) -> dict:
    """Salinan `sebelum` dengan patch diterapkan — untuk pratinjau dry run."""
    hasil = json.loads(json.dumps(sebelum, default=str))
    for bagian in ("criteria", "score", "matching"):
        isi = patch.get(bagian)
        if not isi:
            continue
        tujuan = dict(hasil.get(bagian) or {})
        for kunci, nilai in isi.items():
            if kunci == "minCompleteness" and isinstance(nilai, dict):
                gabungan = dict(tujuan.get("minCompleteness") or {})
                gabungan.update(nilai)
                tujuan["minCompleteness"] = gabungan
            else:
                tujuan[kunci] = nilai
        hasil[bagian] = tujuan
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
    # `now()` diteruskan sebagai ekspresi SQL, bukan literal string.
    bagian = ", ".join(
        f"{k} = now()" if v == "now()" and k.endswith("_at") else f"{k} = {q(v)}"
        for k, v in nilai.items()
    )
    jalankan_pg(con, f"UPDATE {tabel} SET {bagian} "
                     f"WHERE {kol_kunci} = {q(kunci)}")


def _beda(sebelum: dict, sesudah: dict) -> list[dict]:
    """
    Daftar field yang benar-benar berubah — untuk jejak audit di UI.

    `minCompleteness` ditelusuri sampai ke ELEMEN-nya. Melaporkannya sebagai
    satu objek utuh membuat catatan audit menampilkan keenam elemen setiap kali
    satu di antaranya digeser, dan yang benar-benar berubah jadi tenggelam.
    """
    ubah = []
    for bagian in ("criteria", "score", "matching"):
        a, b = sebelum.get(bagian) or {}, sesudah.get(bagian) or {}
        for kunci in sorted(set(a) | set(b)):
            if kunci in ("updatedAt", "updatedBy"):
                continue

            if kunci == "minCompleteness":
                ea, eb = a.get(kunci) or {}, b.get(kunci) or {}
                for elemen in sorted(set(ea) | set(eb)):
                    if ea.get(elemen) != eb.get(elemen):
                        ubah.append({"section": bagian,
                                     "field": f"minCompleteness.{elemen}",
                                     "from": ea.get(elemen), "to": eb.get(elemen)})
                continue

            if a.get(kunci) != b.get(kunci):
                ubah.append({"section": bagian, "field": kunci,
                             "from": a.get(kunci), "to": b.get(kunci)})
    return ubah
