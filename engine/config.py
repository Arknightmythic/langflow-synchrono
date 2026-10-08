import itertools
import json
import time

from . import rules as R
from . import settings as cfg
from . import sr
from .sql import now_text, sjson, sq

LETTERS = R.LETTERS
NIK_VALUES = tuple(R.NIK_FROM_API)
SUM_TOLERANCE = 1e-6
NOT_CONFIGURABLE_NOTE = {
    5: ("Grade E memakai KOMBINASI kolom yang harus ada, bukan ambang "
        "persentase, sehingga bentuknya tidak muat di tabel ini. Kombinasinya "
        "diatur di bagian global: `grading.gradeECombinations`."),
    6: ("Grade F bukan aturan melainkan hasil: tidak satu pun kriteria di atas "
        "terpenuhi. Tidak ada yang bisa disetel."),
}
GRADE_E_ELEMENTS = ["nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu",
                    "wilayah"]

CRITERIA_FIELDS = {"order": "eval_order", "active": "active", "nikColumn": "nik_column",
                   "minNikTrusted": "min_nik_trusted"}
SCORE_FIELDS = {"min": "score_min", "max": "score_max", "severityLabel": "severity_label",
                "canProceed": "can_proceed", "criteriaDescription": "criteria_description"}
MATCHING_FIELDS = {"autoMissingMax": "auto_missing_max", "autoScoreMin": "auto_score_min",
                   "reviewMissingCount": "review_missing_count",
                   "reviewScoreMin": "review_score_min", "reviewScoreMax": "review_score_max"}
MATCHING_JSON_FIELDS = {"weights": "weights", "missingElements": "missing_elements",
                        "nameCleaning": "name_cleaning"}
JSON_COLUMNS = set(MATCHING_JSON_FIELDS.values())
NOT_A_CHANGE = ("updatedAt", "updatedBy", "analysis", "blocking", "availableElements")


def blocking_text(spec: dict | None) -> str | None:
    if not spec:
        return None
    join = "LEFT JOIN" if spec.get("join") == "left" else "JOIN"
    return "\nUNION ALL\n".join(f"incoming i {join} master m ON {c}" for c in spec["branches"])


def available_elements() -> dict:
    return {"weights": list(R.SCORE_ELEMENTS), "missingElements": list(R.SCORE_ELEMENTS)}


def _number(x) -> str:
    if x is None:
        return "-"
    x = float(x)
    return f"{x:.16g}" if x != round(x, 2) else f"{round(x, 2):g}"


def _valid_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _best_score(weights: dict, empty: tuple) -> float:
    total = 0.0
    for element, percent in weights.items():
        if not percent:
            continue
        total = total + (0.0 if element in empty else 1.0) * (float(percent) / 100)
    return total * 100


def analyse_matching(m: dict) -> dict:
    weights = m.get("weights") or {}
    missing = list(m.get("missingElements") or [])
    amax, amin = m.get("autoMissingMax"), m.get("autoScoreMin")
    rcnt = m.get("reviewMissingCount")
    rmin, rmax = m.get("reviewScoreMin"), m.get("reviewScoreMax")
    per_n = {n: [(_best_score(weights, s), s) for s in itertools.combinations(missing, n)]
             for n in range(len(missing) + 1)}
    best = {n: max(x for x, _ in items) for n, items in per_n.items()}

    def can_auto(n, score):
        return amin is not None and (amax is None or n <= amax) and score >= amin

    def can_review(n, score):
        return ((rcnt is None or n == rcnt) and rmin is not None and rmax is not None
                and rmin <= score < rmax)

    notes = []
    if amin is not None and best[0] < amin:
        notes.append(f"AUTO tidak mungkin tercapai lewat skor: skor tertinggi "
                     f"{_number(best[0])} di bawah autoScoreMin {_number(amin)}.")
    if rcnt is not None:
        if rcnt > len(missing):
            listed = f" ({', '.join(missing)})" if missing else ""
            notes.append(f"REVIEW tidak pernah terjadi: butuh tepat {rcnt} elemen kosong, "
                         f"padahal elemen yang dihitung kosong hanya {len(missing)}{listed}.")
        elif rmin is not None and best[rcnt] < rmin:
            notes.append(f"REVIEW tidak mungkin tercapai: dengan {rcnt} elemen kosong, skor "
                         f"tertinggi {_number(best[rcnt])} di bawah reviewScoreMin "
                         f"{_number(rmin)}.")
    floor = min((x for x in (amin, rmin) if x is not None), default=None)
    if floor is not None:
        for n in range(len(missing) + 1):
            fallen = [(score, s) for score, s in per_n[n]
                      if score >= floor and not can_auto(n, score) and not can_review(n, score)]
            if not fallen:
                continue
            if amax is not None and n > amax:
                auto_reason = f"tidak bisa AUTO (kosong {n} > autoMissingMax {amax})"
            else:
                auto_reason = f"tidak bisa AUTO (skor di bawah autoScoreMin {_number(amin)})"
            if rcnt is not None and n != rcnt:
                review_reason = f"REVIEW hanya untuk tepat {rcnt} elemen kosong"
            else:
                review_reason = (f"skornya di luar pita REVIEW "
                                 f"{_number(rmin)}–<{_number(rmax)}")
            shapes = "; ".join(f"{' + '.join(s)} kosong -> {_number(x)}" for x, s in fallen[:4])
            if len(fallen) > 4:
                shapes += f"; dan {len(fallen) - 4} pola lain"
            notes.append(f"Baris dengan {n} elemen kosong yang selebihnya cocok sempurna "
                         f"jatuh ke UNMATCH: {auto_reason}, dan {review_reason}. ({shapes})")
    return {"maxScoreByMissingCount": {str(n): best[n] for n in sorted(best)},
            "warnings": notes}


def analyse_global(values: dict) -> list[str]:
    notes = []
    for combination in values.get("grading.gradeECombinations") or []:
        if not isinstance(combination, list):
            continue
        if "nama" not in combination or "tanggal_lahir" not in combination:
            notes.append(
                f"Kombinasi grade E {combination}: blocking grade E mensyaratkan nama "
                f"DAN tanggal lahir (3 huruf awal nama, hari & bulan lahir). Berkas "
                f"yang hanya memenuhi kombinasi ini tidak akan mendapat kandidat di "
                f"Pass 3.")
    eps = values.get("matching.conflictEpsilon")
    if isinstance(eps, (int, float)) and eps > 5:
        notes.append(f"conflictEpsilon {_number(eps)} poin cukup lebar: kandidat yang skornya "
                     f"berbeda sebesar itu pun dianggap seri, sehingga CONFLICT akan banyak.")
    return notes


def _shape_global(glob: dict) -> dict:
    result = {"grading": {}, "matching": {}, "meta": {}}
    for key, item in glob.items():
        section, field = key.split(".", 1)
        result[section][field] = item["value"]
        result["meta"][key] = {"source": item["source"], "updatedAt": item["updatedAt"],
                               "updatedBy": item["updatedBy"]}
    result["analysis"] = {"warnings": analyse_global({k: v["value"] for k, v in glob.items()})}
    return result


def _shape_matching(gid: int, rule: dict, blocking: dict | None) -> dict:
    m = {"autoMissingMax": rule["auto_missing_max"], "autoScoreMin": rule["auto_score_min"],
         "reviewMissingCount": rule["review_missing_count"],
         "reviewScoreMin": rule["review_score_min"], "reviewScoreMax": rule["review_score_max"]}
    if gid in R.MATCHING_GRADES:
        m["weights"] = {f: b for f, b in rule["weights"]}
        m["missingElements"] = list(rule["missing_elements"])
        m["nameCleaning"] = dict(rule["name_cleaning"])
        m["dateMatch"] = rule["date_match"]
    else:
        m["weights"] = m["missingElements"] = m["nameCleaning"] = m["dateMatch"] = None
    m["blocking"] = {"query": blocking_text(blocking), "spec": blocking, "editable": False,
                     "note": "Kueri blocking hanya bisa dibaca lewat API; diubah lewat "
                             "basis data/migrasi."}
    m["availableElements"] = available_elements() if gid in R.MATCHING_GRADES else None
    m["analysis"] = analyse_matching(m) if gid in R.MATCHING_GRADES else None
    m["updatedAt"] = rule.get("updated_at")
    m["updatedBy"] = rule.get("updated_by")
    return m


def _shape_grade(gid: int, crit: dict | None, band: dict | None, rule: dict | None,
                 blocking: dict | None) -> dict:
    return {
        "gradeId": gid,
        "gradeLetter": (band or {}).get("grade_letter") or LETTERS.get(gid),
        "criteriaEditable": gid in R.CONFIGURABLE_GRADES,
        "note": NOT_CONFIGURABLE_NOTE.get(gid),
        "criteria": None if not crit else {
            "order": crit["eval_order"], "active": crit["active"],
            "nikColumn": crit["nik_column"],
            "minCompleteness": {e: crit[f"min_{e}"] for e in R.ELEMENTS},
            "minNikTrusted": crit["min_nik_trusted"],
            "updatedAt": crit.get("updated_at"), "updatedBy": crit.get("updated_by"),
        },
        "score": None if not band else {
            "min": band["score_min"], "max": band["score_max"],
            "severityLabel": band["severity_label"], "canProceed": band["can_proceed"],
            "criteriaDescription": band["criteria_description"],
        },
        "matching": None if not rule else _shape_matching(gid, rule, blocking),
    }


def read_all(grade_id: int | None = None) -> dict:
    crit_rows, band_rows = R.all_criteria(), R.all_bands()
    crit = {r["grade_id"]: r for r in crit_rows}
    bands = {r["grade_id"]: r for r in band_rows}
    rule_map, blocking, glob = R.read_rules(), R.read_blocking(), R.read_global()
    grades = [_shape_grade(gid, crit.get(gid), bands.get(gid), rule_map.get(gid),
                           blocking.get(gid))
              for gid in sorted(set(bands) | set(crit) | set(rule_map))
              if grade_id is None or gid == grade_id]
    weights = glob["grading.scoreWeights"]
    return {
        "grades": grades,
        "elements": R.ELEMENTS,
        "matchingElements": R.SCORE_ELEMENTS,
        "scoreWeights": {**weights["value"], "editable": True, "source": weights["source"],
                         "note": "Bobot skor mutu grading. Diubah lewat "
                                 "`global.grading.scoreWeights`."},
        "gradeECombinations": glob["grading.gradeECombinations"]["value"],
        "global": _shape_global(glob),
        "configVersion": R.version_of(R.build_snapshot(crit_rows, band_rows, rule_map,
                                                       blocking, glob)),
    }


def validate(criteria: list[dict], bands: list[dict] | None = None) -> list[str]:
    problems = []
    orders = [k["eval_order"] for k in criteria]
    if len(set(orders)) != len(orders):
        problems.append(f"Nilai `urutan` berulang: {sorted(orders)}. "
                        "Urutan evaluasi jadi tidak tentu.")
    for k in criteria:
        g = LETTERS.get(k["grade_id"], k["grade_id"])
        if k["nik_column"] not in NIK_VALUES:
            problems.append(f"Grade {g}: nik_kolom '{k['nik_column']}' tidak sah "
                            f"(pilih salah satu dari {NIK_VALUES}).")
        for column in R.MIN_COLUMNS:
            v = k.get(column)
            if v is not None and not 0.0 <= v <= 1.0:
                problems.append(f"Grade {g}: {column} = {v}, harus antara 0 dan 1.")
        if k["nik_column"] == "terlarang":
            if k.get("min_nik") is not None:
                problems.append(f"Grade {g}: kolom NIK dinyatakan terlarang, tapi min_nik "
                                f"diisi {k['min_nik']}. Keduanya tidak bisa benar bersamaan.")
            if k.get("min_nik_trusted") is not None:
                problems.append(
                    f"Grade {g}: kolom NIK dinyatakan terlarang, tapi "
                    f"min_nik_trusted diisi {k['min_nik_trusted']}. Tidak ada "
                    "NIK untuk dipercaya, jadi grade ini tak akan pernah cocok.")
    problems += _check_reachable(criteria)
    if bands:
        problems += _check_bands(bands)
    return problems


def _check_reachable(criteria: list[dict]) -> list[str]:
    problems = []
    ordered = sorted(criteria, key=lambda k: k["eval_order"])
    for i, later in enumerate(ordered):
        for earlier in ordered[:i]:
            if later["nik_column"] != earlier["nik_column"]:
                continue
            if all((later.get(c) or 0.0) >= (earlier.get(c) or 0.0) for c in R.MIN_COLUMNS):
                problems.append(
                    f"Grade {LETTERS.get(later['grade_id'])} tidak akan pernah "
                    f"tercapai: seluruh ambangnya sama ketat atau lebih ketat "
                    f"dari grade {LETTERS.get(earlier['grade_id'])} yang "
                    f"dievaluasi lebih dulu, sehingga berkas selalu tertangkap "
                    f"di sana.")
                break
    return problems


def _check_bands(bands: list[dict]) -> list[str]:
    problems = []
    for p in bands:
        g = p.get("grade_letter") or LETTERS.get(p["grade_id"])
        if p["score_min"] > p["score_max"]:
            problems.append(f"Grade {g}: score_min ({p['score_min']}) melebihi "
                            f"score_max ({p['score_max']}).")
        if not (0 <= p["score_min"] <= 100 and 0 <= p["score_max"] <= 100):
            problems.append(f"Grade {g}: pita skor di luar 0-100.")
    ordered = sorted(bands, key=lambda p: p["score_min"])
    for a, b in zip(ordered, ordered[1:]):
        if b["score_min"] <= a["score_max"]:
            problems.append(
                f"Pita skor tumpang tindih: grade "
                f"{a.get('grade_letter')} ({a['score_min']}-{a['score_max']}) "
                f"dan {b.get('grade_letter')} ({b['score_min']}-{b['score_max']}).")
    return problems


def validate_matching(gid: int, m: dict) -> list[str]:
    g = LETTERS.get(gid, gid)
    problems = []
    for key in ("autoScoreMin", "reviewScoreMin", "reviewScoreMax"):
        v = m.get(key)
        if not _valid_number(v) or not 0 <= v <= 100:
            problems.append(f"Grade {g}: {key} = {v!r}, harus angka 0-100.")
    for key in ("autoMissingMax", "reviewMissingCount"):
        v = m.get(key)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            problems.append(f"Grade {g}: {key} = {v!r}, harus bilangan bulat >= 0 "
                            f"atau null (tanpa syarat).")
    rmin, rmax = m.get("reviewScoreMin"), m.get("reviewScoreMax")
    if _valid_number(rmin) and _valid_number(rmax) and rmin > rmax:
        problems.append(f"Grade {g}: reviewScoreMin ({rmin}) melebihi "
                        f"reviewScoreMax ({rmax}).")
    if gid not in R.MATCHING_GRADES:
        return problems

    weights = m.get("weights")
    if not isinstance(weights, dict) or not weights:
        problems.append(f"Grade {g}: weights harus berisi minimal satu elemen.")
    else:
        for element, percent in weights.items():
            if element not in R.SCORE_ELEMENTS:
                problems.append(
                    f"Grade {g}: elemen bobot '{element}' tidak dikenali. Yang "
                    f"tersedia: {R.SCORE_ELEMENTS}" + (
                        " — NIK dipakai untuk blocking & Pass 1 (cocok persis), "
                        "bukan untuk skor kemiripan." if element == "nik" else "") + (
                        " — master tidak punya kolom alamat; pakai 'wilayah' "
                        "(rata-rata provinsi/kabupaten/kecamatan/kelurahan)."
                        if element == "alamat" else ""))
            elif not _valid_number(percent) or not 0 <= percent <= 100:
                problems.append(f"Grade {g}: bobot {element} = {percent!r}, harus "
                                f"angka 0-100 (persen).")
        if all(_valid_number(p) for p in weights.values()):
            total = sum(weights.values())
            if abs(total - 100) > SUM_TOLERANCE:
                problems.append(f"Grade {g}: jumlah bobot {_number(total)}, harus 100.")

    missing = m.get("missingElements")
    if not isinstance(missing, list):
        problems.append(f"Grade {g}: missingElements harus daftar elemen.")
    else:
        for element in missing:
            if element not in R.SCORE_ELEMENTS:
                problems.append(f"Grade {g}: elemen kosong '{element}' tidak dikenali. "
                                f"Yang tersedia: {R.SCORE_ELEMENTS}")
        if len(set(map(str, missing))) != len(missing):
            problems.append(f"Grade {g}: missingElements memuat elemen berulang.")

    if m.get("dateMatch") not in R.DATE_MATCH:
        problems.append(f"Grade {g}: dateMatch = {m.get('dateMatch')!r}, pilih salah satu "
                        f"dari {list(R.DATE_MATCH)}.")
    cleaning = m.get("nameCleaning")
    if not isinstance(cleaning, dict):
        problems.append(f"Grade {g}: nameCleaning harus objek.")
    else:
        for key, value in cleaning.items():
            if key not in R.CLEANING_KEYS:
                problems.append(f"Grade {g}: nameCleaning.{key} tidak dikenali. "
                                f"Yang tersedia: {list(R.CLEANING_KEYS)}")
            elif not isinstance(value, bool):
                problems.append(f"Grade {g}: nameCleaning.{key} harus true/false.")
    return problems


def validate_global(values: dict) -> list[str]:
    problems = []
    weights = values.get("grading.scoreWeights")
    if not isinstance(weights, dict) or set(weights) != set(R.SCORE_WEIGHTS):
        problems.append(f"grading.scoreWeights harus objek dengan tepat kunci "
                        f"{sorted(R.SCORE_WEIGHTS)}.")
    elif not all(_valid_number(v) and 0 <= v <= 1 for v in weights.values()):
        problems.append("grading.scoreWeights: setiap bobot harus angka 0-1.")
    elif abs(sum(weights.values()) - 1) > SUM_TOLERANCE:
        problems.append(f"grading.scoreWeights: jumlah bobot {_number(sum(weights.values()))}, "
                        f"harus 1.")
    combos = values.get("grading.gradeECombinations")
    if not isinstance(combos, list) or not combos:
        problems.append("grading.gradeECombinations harus daftar berisi minimal satu "
                        "kombinasi.")
    else:
        for combination in combos:
            if not isinstance(combination, list) or not combination:
                problems.append(f"Kombinasi grade E {combination!r} harus daftar elemen "
                                f"yang tidak kosong.")
                continue
            foreign = [e for e in combination if e not in GRADE_E_ELEMENTS]
            if foreign:
                problems.append(f"Kombinasi grade E {combination}: elemen {foreign} tidak "
                                f"dikenali. Yang tersedia: {GRADE_E_ELEMENTS}")
            if len(set(map(str, combination))) != len(combination):
                problems.append(f"Kombinasi grade E {combination} memuat elemen berulang.")
    eps = values.get("matching.conflictEpsilon")
    if not _valid_number(eps) or not 0 <= eps <= 100:
        problems.append(f"matching.conflictEpsilon = {eps!r}, harus angka 0-100 "
                        f"(poin skor).")
    jw = values.get("matching.contradictionJw")
    if not _valid_number(jw) or not 0 <= jw <= 1:
        problems.append(f"matching.contradictionJw = {jw!r}, harus angka 0-1.")
    return problems


def _normal_weights(value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        result = {}
        for pair in value:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise ValueError("`weights` berbentuk daftar harus [[elemen, persen], ...].")
            if pair[0] in result:
                raise ValueError(f"`weights` memuat elemen '{pair[0]}' dua kali.")
            result[pair[0]] = pair[1]
        return result
    raise ValueError("`weights` harus objek {elemen: persen} atau "
                     "[[elemen, persen], ...].")


def _collect(section: dict | None, fields: dict) -> dict:
    if not section:
        return {}
    out = {}
    for key, value in section.items():
        if key not in fields:
            raise ValueError(f"Field '{key}' tidak dikenali. Yang tersedia: {sorted(fields)}")
        out[fields[key]] = value
    return out


def _collect_matching(section: dict | None, grade_id: int, before: dict) -> tuple[dict, dict]:
    if not section:
        return {}, {}
    out, patch = {}, {}
    for key, value in section.items():
        if key == "blocking":
            raise ValueError("`matching.blocking` hanya bisa dibaca lewat API. Kueri blocking "
                             "diubah lewat basis data/migrasi (tabel matching_queries).")
        if key in ("analysis", "availableElements", "updatedAt", "updatedBy"):
            raise ValueError(f"`matching.{key}` dihitung sistem, tidak bisa diubah.")
        if key in MATCHING_FIELDS:
            out[MATCHING_FIELDS[key]] = value
            patch[key] = value
            continue
        if key == "dateMatch":
            if grade_id not in R.MATCHING_GRADES:
                raise ValueError(f"Grade {LETTERS.get(grade_id)} tidak dicocokkan, jadi tidak "
                                 f"punya `matching.dateMatch`.")
            out["date_match"] = value
            patch[key] = "similarity" if value is None else value
            continue
        if key not in MATCHING_JSON_FIELDS:
            raise ValueError(
                f"Field 'matching.{key}' tidak dikenali. Yang tersedia: "
                f"{sorted(list(MATCHING_FIELDS) + list(MATCHING_JSON_FIELDS) + ['dateMatch'])}")
        if grade_id not in R.MATCHING_GRADES:
            raise ValueError(f"Grade {LETTERS.get(grade_id)} tidak dicocokkan, jadi tidak punya "
                             f"`matching.{key}`.")
        column = MATCHING_JSON_FIELDS[key]
        if key == "weights":
            weights = _normal_weights(value)
            out[column] = None if weights is None else [[f, b] for f, b in weights.items()]
            patch[key] = (weights if weights is not None
                          else {f: b for f, b in R.DEFAULT_WEIGHTS[grade_id]})
        elif key == "missingElements":
            if value is not None and not isinstance(value, list):
                raise ValueError("`missingElements` harus daftar elemen.")
            out[column] = value
            patch[key] = value if value is not None else list(R.DEFAULT_MISSING[grade_id])
        else:
            if value is not None and not isinstance(value, dict):
                raise ValueError("`nameCleaning` harus objek {titles, patronym, "
                                 "abbreviations}.")
            if value is None:
                out[column] = None
                patch[key] = dict(R.DEFAULT_CLEANING)
            else:
                previous = dict((before.get("matching") or {}).get("nameCleaning") or {})
                out[column] = {**previous, **value}
                patch[key] = value
    return out, patch


def _collect_criteria(section: dict | None, grade_id: int) -> dict:
    if not section:
        return {}
    if grade_id not in R.CONFIGURABLE_GRADES:
        raise ValueError(f"Grade {LETTERS.get(grade_id)} tidak punya kriteria yang bisa "
                         f"disetel. {NOT_CONFIGURABLE_NOTE.get(grade_id, '')}")
    out = {}
    for key, value in section.items():
        if key == "minCompleteness":
            if not isinstance(value, dict):
                raise ValueError("`minCompleteness` harus objek {elemen: nilai}.")
            for element, v in value.items():
                if element not in R.ELEMENTS:
                    raise ValueError(f"Elemen '{element}' tidak dikenali. Yang tersedia: "
                                     f"{R.ELEMENTS}")
                out[f"min_{element}"] = v
        elif key in CRITERIA_FIELDS:
            out[CRITERIA_FIELDS[key]] = value
        else:
            raise ValueError(f"Field '{key}' tidak dikenali. Yang tersedia: "
                             f"{sorted(list(CRITERIA_FIELDS) + ['minCompleteness'])}")
    return out


def _merge(before: dict, patch: dict) -> dict:
    result = json.loads(json.dumps(before, default=str))
    for section in ("criteria", "score", "matching"):
        items = patch.get(section)
        if not items:
            continue
        target = dict(result.get(section) or {})
        for key, value in items.items():
            if key in ("minCompleteness", "nameCleaning") and isinstance(value, dict):
                merged = dict(target.get(key) or {})
                merged.update(value)
                target[key] = merged
            else:
                target[key] = value
        result[section] = target
    if result.get("matching") is not None and result["gradeId"] in R.MATCHING_GRADES:
        result["matching"]["analysis"] = analyse_matching(result["matching"])
    return result


def _write(table: str, key_column: str, key, values: dict) -> None:
    def expression(column, value):
        if column in JSON_COLUMNS:
            return f"{column} = {sjson(value)}"
        if column == "nik_column":
            return f"{column} = {sq(R.NIK_FROM_API.get(value, value))}"
        return f"{column} = {sq(value)}"
    parts = ", ".join(expression(c, v) for c, v in values.items())
    sr.execute(f"UPDATE {cfg.T_SERVICE}{table} SET {parts} WHERE {key_column} = {sq(key)}")


def _diff(before: dict, after: dict) -> list[dict]:
    changes = []
    for section in ("criteria", "score", "matching"):
        a, b = before.get(section) or {}, after.get(section) or {}
        for key in sorted(set(a) | set(b)):
            if key in NOT_A_CHANGE:
                continue
            va, vb = a.get(key), b.get(key)
            if isinstance(va, dict) or isinstance(vb, dict):
                ea, eb = va or {}, vb or {}
                for item in sorted(set(ea) | set(eb)):
                    if ea.get(item) != eb.get(item):
                        changes.append({"section": section, "field": f"{key}.{item}",
                                        "from": ea.get(item), "to": eb.get(item)})
                if list(ea) != list(eb) and set(ea) == set(eb) and ea == eb:
                    changes.append({"section": section, "field": f"{key}(urutan)",
                                    "from": list(ea), "to": list(eb)})
                continue
            if va != vb:
                changes.append({"section": section, "field": key, "from": va, "to": vb})
    return changes


def _record_history(scope: str, grade_id: int | None, by: str | None, changes: list,
                    version: str | None) -> None:
    sr.execute(f"INSERT INTO {cfg.T_SERVICE}config_history VALUES ("
               f"{time.time_ns() // 1000}, {sq(now_text())}, {sq(by)}, {sq(scope)}, "
               f"{sq(grade_id)}, {sjson(changes)}, {sq(version)})")


def update(grade_id: int, patch: dict, by: str | None = None, dry_run: bool = False) -> dict:
    before = read_all(grade_id)["grades"]
    if not before:
        raise ValueError(f"Grade {grade_id} tidak ada.")
    before = before[0]
    set_criteria = _collect_criteria(patch.get("criteria"), grade_id)
    set_score = _collect(patch.get("score"), SCORE_FIELDS)
    set_matching, matching_patch = _collect_matching(patch.get("matching"), grade_id, before)
    if not (set_criteria or set_score or set_matching):
        raise ValueError("Tidak ada yang diubah. Sertakan minimal satu dari "
                         "`criteria`, `score`, atau `matching`.")

    criteria = [{k: r[k] for k in ("grade_id", "eval_order", "nik_column", *R.MIN_COLUMNS,
                                   "active")} for r in R.all_criteria()]
    for k in criteria:
        if k["grade_id"] == grade_id:
            k.update(set_criteria)
    bands = [{k: r[k] for k in ("grade_id", "grade_letter", "score_min", "score_max")}
             for r in R.all_bands()]
    for p in bands:
        if p["grade_id"] == grade_id:
            for column in ("score_min", "score_max"):
                if column in set_score:
                    p[column] = set_score[column]

    simulated = _merge(before, {**patch, "matching": matching_patch})
    problems = validate(criteria, bands)
    if simulated.get("matching") is not None:
        problems += validate_matching(grade_id, simulated["matching"])
    warnings = ((simulated.get("matching") or {}).get("analysis") or {}).get("warnings", [])
    if problems:
        return {"applied": False, "dryRun": dry_run, "problems": problems,
                "warnings": warnings, "before": before, "after": None}

    version = None
    if dry_run:
        after = simulated
    else:
        stamp = now_text()
        if set_criteria:
            _write("grade_criteria", "grade_id", grade_id,
                   {**set_criteria, "updated_at": stamp, "updated_by": by})
        if set_score:
            _write("grade_bands", "grade_id", grade_id, set_score)
        if set_matching:
            _write("grade_rules", "grade_code", grade_id,
                   {**set_matching, "updated_at": stamp, "updated_by": by})
        after = read_all(grade_id)["grades"][0]
    changes = _diff(before, after)
    if not dry_run and changes:
        version = R.record_version()
        _record_history("grade", grade_id, by, changes, version)
    return {"applied": not dry_run, "dryRun": dry_run, "problems": [], "warnings": warnings,
            "before": before, "after": after, "changed": changes, "configVersion": version}


def update_global(patch: dict, by: str | None = None, dry_run: bool = False) -> dict:
    glob = R.read_global()
    before = _shape_global(glob)
    effective = {k: v["value"] for k, v in glob.items()}
    changes_in: dict = {}
    for section in ("grading", "matching"):
        items = patch.get(section)
        if items is None:
            continue
        if not isinstance(items, dict):
            raise ValueError(f"`{section}` harus objek.")
        for field, value in items.items():
            key = f"{section}.{field}"
            if key not in R.GLOBAL_DEFAULTS:
                raise ValueError(f"Field '{key}' tidak dikenali. Yang tersedia: "
                                 f"{sorted(R.GLOBAL_DEFAULTS)}")
            changes_in[key] = value
    if not changes_in:
        raise ValueError("Tidak ada yang diubah. Sertakan `grading` dan/atau `matching`.")
    for key, value in changes_in.items():
        if value is None:
            effective[key] = R.GLOBAL_DEFAULTS[key]()[0]
        elif key == "grading.scoreWeights" and isinstance(value, dict):
            effective[key] = {**effective[key], **value}
            changes_in[key] = effective[key]
        else:
            effective[key] = value

    problems = validate_global(effective)
    warnings = analyse_global(effective)
    if problems:
        return {"applied": False, "dryRun": dry_run, "problems": problems,
                "warnings": warnings, "before": before, "after": None}

    version = None
    if dry_run:
        simulated = {}
        for k, item in glob.items():
            if k in changes_in:
                source = R.GLOBAL_DEFAULTS[k]()[1] if changes_in[k] is None else "config"
                simulated[k] = {**item, "value": effective[k], "source": source}
            else:
                simulated[k] = item
        after = _shape_global(simulated)
    else:
        for key, value in changes_in.items():
            if value is None:
                sr.execute(f"DELETE FROM {cfg.T_SERVICE}engine_config "
                           f"WHERE config_key = {sq(key)}")
            else:
                sr.execute(f"INSERT INTO {cfg.T_SERVICE}engine_config VALUES ({sq(key)}, "
                           f"{sjson(value)}, {sq(now_text())}, {sq(by)})")
        after = _shape_global(R.read_global())

    def pick(shape: dict, key: str) -> tuple:
        section, field = key.split(".", 1)
        return shape[section][field], shape["meta"][key]["source"]

    changes = []
    for k in changes_in:
        (old, old_source), (new, new_source) = pick(before, k), pick(after, k)
        if old != new or old_source != new_source:
            changes.append({"section": "global", "field": k, "from": old, "to": new})
    if not dry_run and changes:
        version = R.record_version()
        _record_history("global", None, by, changes, version)
    return {"applied": not dry_run, "dryRun": dry_run, "problems": [], "warnings": warnings,
            "before": before, "after": after, "changed": changes, "configVersion": version}


def read_version(version: str) -> dict | None:
    rows = sr.query(f"SELECT CAST(content AS VARCHAR) AS content, first_used "
                    f"FROM {cfg.T_SERVICE}config_versions WHERE version = {sq(version)}")
    if not rows:
        return None
    return {"configVersion": version, "firstUsedAt": R._stamp(rows[0]["first_used"]),
            "config": R._json(rows[0]["content"])}


def history(grade_id: int | None = None, limit: int = 50) -> list[dict]:
    where = f"WHERE grade_id = {int(grade_id)}" if grade_id is not None else ""
    rows = sr.query(f"SELECT id, changed_at, changed_by, scope, grade_id, "
                    f"CAST(changes AS VARCHAR) AS changes, version "
                    f"FROM {cfg.T_SERVICE}config_history {where} "
                    f"ORDER BY id DESC LIMIT {max(1, min(int(limit), 500))}")
    return [{"id": r["id"], "at": R._stamp(r["changed_at"]), "by": r["changed_by"],
             "scope": r["scope"], "gradeId": r["grade_id"],
             "gradeLetter": LETTERS.get(r["grade_id"]) if r["grade_id"] else None,
             "changes": R._json(r["changes"]), "configVersion": r["version"]}
            for r in rows]
