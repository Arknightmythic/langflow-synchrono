import hashlib
import json

from . import settings as cfg
from . import sr
from .sql import now_text, sjson, sq

ELEMENTS = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"]
SCORE_ELEMENTS = ["nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu", "wilayah"]
MIN_COLUMNS = [f"min_{e}" for e in ELEMENTS] + ["min_nik_trusted"]
LETTERS = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}
CONFIGURABLE_GRADES = (1, 2, 3, 4)
MATCHING_GRADES = (1, 2, 3, 4, 5)
RULE_COLUMNS = ["auto_missing_max", "auto_score_min", "review_missing_count",
                "review_score_min", "review_score_max"]
DATE_MATCH = ("similarity", "exact")
CLEANING_KEYS = ("titles", "patronym", "abbreviations")

NIK_TO_API = {"required": "wajib", "forbidden": "terlarang", "ignore": "abaikan"}
NIK_FROM_API = {v: k for k, v in NIK_TO_API.items()}

GRADE_E_COMBINATIONS = [
    ["nama", "tanggal_lahir", "jenis_kelamin"],
    ["nama", "tempat_lahir", "tanggal_lahir"],
    ["nama", "tempat_lahir", "nama_ibu"],
    ["nama", "tanggal_lahir", "wilayah"],
]
SCORE_WEIGHTS = {"kelengkapan": 0.6, "nik_tepercaya": 0.4}

DEFAULT_WEIGHTS = {
    1: [("nama", 100)],
    2: [("nama", 80), ("tempat_lahir", 10), ("nama_ibu", 10)],
    3: [("nama", 60), ("tempat_lahir", 20), ("tanggal_lahir", 20)],
    4: [("nama", 60), ("tanggal_lahir", 30), ("tempat_lahir", 5), ("nama_ibu", 5)],
    5: [("nama", 50), ("wilayah", 30), ("nama_ibu", 10), ("tanggal_lahir", 10)],
}
DEFAULT_MISSING = {
    1: [], 2: ["nama", "tempat_lahir", "nama_ibu"], 3: [],
    4: ["tanggal_lahir", "tempat_lahir", "nama_ibu"],
    5: ["nama", "tanggal_lahir", "wilayah", "nama_ibu"],
}
DEFAULT_CLEANING = {"titles": False, "patronym": False, "abbreviations": False}


def _env_number(names: tuple[str, ...], default: float) -> tuple[float, str]:
    raw = cfg.env(names[0], "", *names[1:])
    if raw:
        try:
            return float(raw), "env"
        except ValueError:
            pass
    return default, "default"


GLOBAL_DEFAULTS = {
    "grading.scoreWeights": lambda: (dict(SCORE_WEIGHTS), "default"),
    "grading.gradeECombinations": lambda: ([list(c) for c in GRADE_E_COMBINATIONS], "default"),
    "matching.conflictEpsilon": lambda: _env_number(("MATCHING_CONFLICT_EPSILON",), 0.0),
    "matching.contradictionJw": lambda: _env_number(
        ("MATCHING_KONTRA_JW", "MATCHING_CONTRADICTION_JW"), 0.80),
}


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _stamp(value) -> str | None:
    return None if value is None else str(value)


def criteria() -> list[dict]:
    return sr.query(f"SELECT grade_id, eval_order, nik_column, {', '.join(MIN_COLUMNS)} "
                    f"FROM {cfg.T_SERVICE}grade_criteria WHERE active ORDER BY eval_order")


def all_criteria() -> list[dict]:
    rows = sr.query(f"SELECT grade_id, eval_order, nik_column, {', '.join(MIN_COLUMNS)}, active, "
                    f"updated_at, updated_by FROM {cfg.T_SERVICE}grade_criteria "
                    f"ORDER BY eval_order")
    for r in rows:
        r["active"] = bool(r["active"])
        r["nik_column"] = NIK_TO_API.get(r["nik_column"], r["nik_column"])
        r["updated_at"] = _stamp(r["updated_at"])
    return rows


def all_bands() -> list[dict]:
    rows = sr.query(f"SELECT grade_id, grade_letter, score_min, score_max, severity_label, "
                    f"can_proceed, criteria_description FROM {cfg.T_SERVICE}grade_bands")
    for r in rows:
        r["can_proceed"] = bool(r["can_proceed"])
    return rows


def band(grade: int) -> dict:
    rows = [r for r in all_bands() if r["grade_id"] == grade]
    if not rows:
        raise RuntimeError(f"grade_bands belum terisi untuk grade {grade}. "
                           f"Jalankan: python schema/apply.py")
    return rows[0]


def read_global() -> dict:
    stored = {r["config_key"]: (_json(r["value"]), _stamp(r["updated_at"]), r["updated_by"])
              for r in sr.query(f"SELECT config_key, CAST(value AS VARCHAR) AS value, "
                                f"updated_at, updated_by FROM {cfg.T_SERVICE}engine_config")}
    result = {}
    for key, default in GLOBAL_DEFAULTS.items():
        if key in stored and stored[key][0] is not None:
            value, at, by = stored[key]
            result[key] = {"value": value, "source": "config", "updatedAt": at, "updatedBy": by}
        else:
            value, source = default()
            result[key] = {"value": value, "source": source, "updatedAt": None, "updatedBy": None}
    return result


def global_values() -> dict:
    return {k: v["value"] for k, v in read_global().items()}


def read_rules() -> dict[int, dict]:
    rows = sr.query(f"""
        SELECT grade_code, {', '.join(RULE_COLUMNS)},
               CAST(weights AS VARCHAR) AS weights,
               CAST(missing_elements AS VARCHAR) AS missing_elements,
               CAST(name_cleaning AS VARCHAR) AS name_cleaning, date_match,
               updated_at, updated_by
          FROM {cfg.T_SERVICE}grade_rules""")
    result = {}
    for r in rows:
        g = int(r["grade_code"])
        weights = _json(r["weights"])
        r["weights"] = ([[f, b] for f, b in weights] if weights is not None
                        else [list(p) for p in DEFAULT_WEIGHTS[g]] if g in DEFAULT_WEIGHTS
                        else None)
        missing = _json(r["missing_elements"])
        r["missing_elements"] = (missing if missing is not None
                                 else list(DEFAULT_MISSING[g]) if g in DEFAULT_MISSING else None)
        r["name_cleaning"] = ({**DEFAULT_CLEANING, **(_json(r["name_cleaning"]) or {})}
                              if g in MATCHING_GRADES else None)
        r["date_match"] = (r["date_match"] or "similarity") if g in MATCHING_GRADES else None
        r["updated_at"] = _stamp(r["updated_at"])
        result[g] = r
    return result


def read_blocking() -> dict[int, dict]:
    return {int(r["grade_code"]): _json(r["blocking"]) for r in sr.query(
        f"SELECT grade_code, CAST(blocking AS VARCHAR) AS blocking "
        f"FROM {cfg.T_SERVICE}matching_queries")}


def matching_rules(grade: int) -> dict:
    rules = read_rules().get(grade)
    if not rules:
        raise ValueError(f"grade_rules untuk grade {grade} tidak ada")
    blocking = read_blocking().get(grade)
    if not blocking:
        raise ValueError(f"matching_queries untuk grade {grade} tidak ada")
    glob = global_values()
    return {
        **{k: rules[k] for k in RULE_COLUMNS},
        "weights": rules["weights"],
        "missing_elements": rules["missing_elements"],
        "name_cleaning": rules["name_cleaning"],
        "date_match": rules["date_match"],
        "blocking": blocking,
        "epsilon": float(glob["matching.conflictEpsilon"]),
        "contradiction_jw": float(glob["matching.contradictionJw"]),
    }


def build_snapshot(crit: list[dict], bands: list[dict], rules: dict, blocking: dict,
                   glob: dict) -> dict:
    return {
        "criteria": {str(k["grade_id"]): {"order": k["eval_order"], "active": k["active"],
                                          "nikColumn": k["nik_column"],
                                          **{c: k[c] for c in MIN_COLUMNS}}
                     for k in sorted(crit, key=lambda k: k["grade_id"])},
        "bands": {str(p["grade_id"]): {"min": p["score_min"], "max": p["score_max"],
                                       "severityLabel": p["severity_label"],
                                       "canProceed": p["can_proceed"],
                                       "criteriaDescription": p["criteria_description"]}
                  for p in sorted(bands, key=lambda p: p["grade_id"])},
        "matching": {str(g): {**{k: a[k] for k in RULE_COLUMNS},
                              "weights": a["weights"],
                              "missingElements": a["missing_elements"],
                              "nameCleaning": a["name_cleaning"],
                              **({"dateMatch": a["date_match"]}
                                 if a.get("date_match") not in (None, "similarity") else {}),
                              "blocking": blocking.get(g)}
                     for g, a in sorted(rules.items())},
        "global": {k: v["value"] for k, v in sorted(glob.items())},
    }


def version_of(snapshot: dict) -> str:
    text = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def snapshot() -> dict:
    return build_snapshot(all_criteria(), all_bands(), read_rules(), read_blocking(),
                          read_global())


def record_version() -> str:
    content = snapshot()
    version = version_of(content)
    try:
        if not sr.scalar(f"SELECT count(*) FROM {cfg.T_SERVICE}config_versions "
                         f"WHERE version = {sq(version)}"):
            sr.execute(f"INSERT INTO {cfg.T_SERVICE}config_versions VALUES "
                       f"({sq(version)}, {sjson(content)}, {sq(now_text())})")
    except Exception as e:  # noqa: BLE001
        print(f"[config] version {version} not stored: {e}")
    return version


def rules_summary(grade: int, rules: dict, version: str) -> dict:
    return {
        "configVersion": version, "grade": grade, "gradeLetter": LETTERS.get(grade),
        "thresholds": {"autoMissingMax": rules["auto_missing_max"],
                       "autoScoreMin": rules["auto_score_min"],
                       "reviewMissingCount": rules["review_missing_count"],
                       "reviewScoreMin": rules["review_score_min"],
                       "reviewScoreMax": rules["review_score_max"]},
        "weights": {f: b for f, b in rules["weights"]},
        "missingElements": rules["missing_elements"],
        "nameCleaning": rules["name_cleaning"],
        "dateMatch": rules["date_match"],
        "conflictEpsilon": rules["epsilon"],
        "contradictionJw": rules["contradiction_jw"],
    }
