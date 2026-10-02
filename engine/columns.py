import re

from . import llm, regions
from . import settings as cfg
from . import sr
from .sql import quote_ident, sq

CORE = ["nik", "nama", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"]
REGION = ["wilayah", "provinsi", "kabupaten", "kecamatan", "kelurahan"]
OTHER = ["id", "status_hidup"]
ALL = CORE + REGION + OTHER

GENDER_MALE = ["laki-laki", "laki laki", "pria", "male", "l"]
GENDER_FEMALE = ["perempuan", "wanita", "female", "p"]
ALIVE = ["hidup", "h"]
DEAD = ["meninggal", "mati", "wafat", "m"]

ALIASES = {
    "nik": ["nik", "no_nik", "nomor_nik", "nik_ktp", "no_ktp", "nomor_ktp",
            "no_identitas", "nomor_identitas", "no_kependudukan"],
    "nama": ["nama_lengkap", "nama", "nama_penduduk", "nama_warga", "fullname",
             "full_name", "nama_wp", "nama_lengkap_wp"],
    "tempat_lahir": ["tempat_lahir", "tmp_lahir", "tempatlahir", "birth_place",
                     "tempat_kelahiran", "kota_kelahiran", "kota_lahir"],
    "tanggal_lahir": ["tanggal_lahir", "tgl_lahir", "tgl_lhr", "tanggallahir",
                      "dob", "date_of_birth", "birth_date", "tanggal_lhr"],
    "jenis_kelamin": ["jenis_kelamin", "jeniskelamin", "jk", "gender", "sex", "l_p", "lp"],
    "nama_ibu": ["nama_ibu_kandung", "nama_ibu", "ibu_kandung", "nama_ibu_kdg",
                 "mother_name", "nama_ibunda"],
    "wilayah": ["wilayah", "daerah", "region", "alamat_wilayah"],
    "provinsi": ["provinsi", "prov", "prop", "province", "propinsi"],
    "kabupaten": ["kabupaten", "kab", "kab_kota", "kabkota", "kabupaten_kota",
                  "regency", "kota_kabupaten"],
    "kecamatan": ["kecamatan", "kec", "district"],
    "kelurahan": ["kelurahan", "kel", "desa", "desa_kelurahan", "kelurahan_desa",
                  "village", "kel_desa"],
    "status_hidup": ["status_hidup", "status_kematian", "hidup_mati", "status_penduduk"],
    "id": ["id", "id_incoming", "no_urut", "nomor_urut", "row_id", "no"],
}

DICTIONARY_THRESHOLD = 0.60
DICTIONARY_MARGIN = 0.15
FUZZY_THRESHOLD = 85
SAMPLE_SIZE = 300
VITAL_STATUS = {"hidup", "mati", "meninggal", "h", "m", "wafat", "almarhum"}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _layer_alias(columns: list[str]) -> tuple[dict, list]:
    available = {_key(c): c for c in columns}
    mapping, trace = {}, []
    for element, candidates in ALIASES.items():
        for alias in candidates:
            k = _key(alias)
            if k in available and available[k] not in mapping.values():
                mapping[element] = available[k]
                trace.append({"elemen": element, "kolom": available[k], "lapis": 1,
                              "dasar": f"alias '{alias}'"})
                break
    return mapping, trace


def _layer_fuzzy(columns: list[str], mapping: dict) -> tuple[dict, list]:
    try:
        from rapidfuzz import fuzz, process
    except ImportError:
        return {}, [{"lapis": 2, "dasar": "rapidfuzz tidak terpasang, dilewati"}]
    vocabulary = [(a, e) for e, aliases in ALIASES.items() for a in aliases]
    choices = [_key(a) for a, _ in vocabulary]
    found, trace = {}, []
    for column in columns:
        if column in mapping.values() or column in found.values():
            continue
        match = process.extractOne(_key(column), choices, scorer=fuzz.ratio)
        if not match or match[1] < FUZZY_THRESHOLD:
            continue
        alias, element = vocabulary[match[2]]
        if element in mapping or element in found:
            continue
        found[element] = column
        trace.append({"elemen": element, "kolom": column, "lapis": 2,
                      "dasar": f"mirip '{alias}' (skor {match[1]:.0f})"})
    return found, trace


def _signature(values: list, valid_provinces: set[str] | None = None) -> str | None:
    items = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if len(items) < 5:
        return None
    n = len(items)

    def ratio(pattern: str) -> float:
        return sum(1 for v in items if re.fullmatch(pattern, v)) / n

    if ratio(r"\d{16}") > 0.8:
        if not valid_provinces:
            return "nik"
        provinces = {v[:2] for v in items if re.fullmatch(r"\d{16}", v)}
        if provinces and len(provinces & valid_provinces) / len(provinces) > 0.8:
            return "nik"
    if ratio(r"\d{1,4}[-/. ]\w{1,9}[-/. ]\d{2,4}") > 0.7:
        return "tanggal_lahir"
    distinct = {v.lower() for v in items}
    if len(distinct) <= 8:
        if distinct <= set(GENDER_MALE) | set(GENDER_FEMALE):
            return "jenis_kelamin"
        if distinct <= VITAL_STATUS:
            return "status_hidup"
    return None


def _layer_values(sample: dict, mapping: dict, valid_provinces: set[str]) -> tuple[dict, list]:
    found, trace = {}, []
    for column, values in sample.items():
        if column in mapping.values() or column in found.values():
            continue
        guess = _signature(values, valid_provinces)
        if guess and guess not in mapping and guess not in found:
            found[guess] = column
            example = next((str(v) for v in values if v), "")
            trace.append({"elemen": guess, "kolom": column, "lapis": 3,
                          "dasar": f"bentuk nilai (contoh: {example[:24]})"})
    return found, trace


def _dictionary_scores(sample: dict, remaining: list[str]) -> dict[str, list]:
    values = {}
    for column in remaining:
        items = [str(v).strip().lower() for v in sample[column]
                 if v is not None and str(v).strip()]
        if len(items) >= 5:
            values[column] = items
    if not values:
        return {}
    distinct = sorted({v for items in values.values() for v in items})
    try:
        rows = sr.query(f"SELECT DISTINCT element, value FROM {cfg.DB_MASTER}.dictionary "
                        f"WHERE value IN ({', '.join(sq(v) for v in distinct)})")
    except Exception as e:  # noqa: BLE001
        print(f"[NORM] dictionary unavailable: {str(e).splitlines()[0][:90]}")
        return {}
    by_value: dict[str, set] = {}
    for row in rows:
        by_value.setdefault(row["value"], set()).add(row["element"])
    scores = {}
    for column, items in values.items():
        counts: dict[str, int] = {}
        for value in items:
            for element in by_value.get(value, ()):
                counts[element] = counts.get(element, 0) + 1
        scores[column] = sorted(((e, c / len(items)) for e, c in counts.items()),
                                key=lambda x: -x[1])
    return scores


def _dictionary_available() -> bool:
    try:
        return bool(sr.scalar(f"SELECT count(*) FROM {cfg.DB_MASTER}.dictionary"))
    except Exception:  # noqa: BLE001
        return False


def _layer_dictionary(sample: dict, mapping: dict) -> tuple[dict, list]:
    remaining = [c for c in sample if c not in mapping.values()]
    if not remaining or not _dictionary_available():
        return {}, []
    all_scores = _dictionary_scores(sample, remaining)
    found, trace = {}, []
    for column in remaining:
        scored = all_scores.get(column)
        if not scored:
            continue
        open_choices = [(e, s) for e, s in scored if e not in mapping and e not in found]
        if not open_choices:
            continue
        element, score = open_choices[0]
        runner_up = open_choices[1][1] if len(open_choices) > 1 else 0.0
        if score < DICTIONARY_THRESHOLD or (score - runner_up) < DICTIONARY_MARGIN:
            continue
        found[element] = column
        trace.append({"elemen": element, "kolom": column, "lapis": 4,
                      "dasar": f"cocok kamus master {score:.0%} "
                               f"(pesaing terdekat {runner_up:.0%})"})
    return found, trace


def _layer_ai(sample: dict, mapping: dict) -> tuple[dict, list]:
    remaining = [c for c in sample if c not in mapping.values()]
    if not remaining:
        return {}, []
    if not llm.column_ai_enabled():
        return {}, [{"lapis": 5, "dasar": f"AI tidak dipanggil: {llm.column_ai_off_reason()}",
                     "kolom_tersisa": remaining}]
    used = sorted(mapping)
    available = [e for e in ALL if e not in mapping]
    with_samples, sample_note = llm.samples_allowed()
    examples = ""
    if with_samples:
        lines = []
        for column in remaining:
            values = [str(v) for v in sample[column] if v][:cfg.COLUMN_AI_SAMPLES]
            lines.append(f'  "{column}": {values}')
        examples = "\n\nContoh nilai tiap kolom:\n" + "\n".join(lines)
    prompt = f"""Anda memetakan nama kolom berkas data kependudukan Indonesia ke
elemen baku. Jawab HANYA JSON, tanpa penjelasan apa pun.

Elemen yang MASIH tersedia: {available}
Elemen yang SUDAH terpakai (jangan dipakai lagi): {used}

Kolom yang belum dikenali: {remaining}{examples}

Pakai null untuk kolom yang tidak cocok dengan elemen mana pun.
Format: {{"nama_kolom": "elemen_atau_null"}}"""
    try:
        answer = llm.ask_columns(prompt)
        proposal = llm.parse_json(answer["text"])
    except Exception as e:  # noqa: BLE001
        return {}, [{"lapis": 5, "dasar": f"AI gagal: {type(e).__name__}: {e}",
                     "kolom_tersisa": remaining}]
    found, trace = {}, [{
        "lapis": 5, "dasar": "AI dipanggil", "model": answer["model"],
        "endpoint": answer["endpoint"], "detik": answer["seconds"], "token": answer["tokens"],
        "contoh_nilai_dikirim": with_samples, "catatan_sampel": sample_note,
    }]
    for column, element in (proposal or {}).items():
        if column not in remaining or element in (None, "null", ""):
            continue
        if element not in ALL:
            trace.append({"lapis": 5, "kolom": column,
                          "dasar": f"usul '{element}' ditolak: bukan elemen baku"})
            continue
        if element in mapping or element in found:
            trace.append({"lapis": 5, "kolom": column,
                          "dasar": f"usul '{element}' ditolak: sudah terpakai"})
            continue
        found[element] = column
        trace.append({"elemen": element, "kolom": column, "lapis": 5, "dasar": "usulan AI"})
    return found, trace


def sample_values(con, view: str, columns: list[str]) -> dict:
    rows = con.execute(f"SELECT * FROM {view} USING SAMPLE reservoir({SAMPLE_SIZE} ROWS) "
                       f"REPEATABLE (42)").fetchall()
    return {c: [r[i] for r in rows] for i, c in enumerate(columns)}


def map_columns(con, view: str, columns: list[str], allow_ai: bool = True) -> dict:
    mapping, trace = _layer_alias(columns)
    sample = None
    for layer in (2, 3, 4, 5):
        if not [c for c in columns if c not in mapping.values()]:
            break
        if layer == 2:
            found, t = _layer_fuzzy(columns, mapping)
        else:
            if sample is None:
                sample = sample_values(con, view, columns)
            if layer == 3:
                found, t = _layer_values(sample, mapping, regions.province_codes(con))
            elif layer == 4:
                found, t = _layer_dictionary(sample, mapping)
            elif not allow_ai:
                found, t = {}, [{"lapis": 5, "dasar": "AI dilarang untuk berkas ini"}]
            else:
                found, t = _layer_ai(sample, mapping)
        mapping.update(found)
        trace += t
    remaining = [c for c in columns if c not in mapping.values()]
    return {"mapping": mapping, "trace": trace, "remaining": remaining}


MONTHS_ID = [
    ("januari", "january"), ("februari", "february"), ("pebruari", "february"),
    ("maret", "march"), ("april", "april"), ("agustus", "august"),
    ("oktober", "october"), ("nopember", "november"), ("november", "november"),
    ("desember", "december"), ("juni", "june"), ("juli", "july"), ("mei", "may"),
    ("jan", "jan"), ("feb", "feb"), ("mar", "mar"), ("apr", "apr"),
    ("agt", "aug"), ("ags", "aug"), ("agu", "aug"), ("jun", "jun"),
    ("jul", "jul"), ("sep", "sep"), ("okt", "oct"), ("nov", "nov"),
    ("des", "dec"),
]
FORMAT_NEUTRAL = ["%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%d-%B-%Y", "%d-%b-%Y",
                  "%d/%B/%Y", "%d/%b/%Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"]
FORMAT_DMY = ["%d-%m-%Y", "%d/%m/%Y", "%d %m %Y", "%d.%m.%Y"]
FORMAT_MDY = ["%m-%d-%Y", "%m/%d/%Y", "%m %d %Y", "%m.%d.%Y"]
FORMAT_NEUTRAL_YY = ["%d %B %y", "%d %b %y", "%d-%B-%y", "%d-%b-%y", "%d/%B/%y", "%d/%b/%y"]
FORMAT_DMY_YY = ["%d-%m-%y", "%d/%m/%y", "%d %m %y", "%d.%m.%y"]
FORMAT_MDY_YY = ["%m-%d-%y", "%m/%d/%y", "%m %d %y", "%m.%d.%y"]
ENDS_TWO_DIGITS = r"(^|[^0-9])[0-9]{2}$"
STARTS_FOUR_DIGITS = r"^[0-9]{4}"
TIME_SUFFIX = r"[ t]+[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?([.,][0-9]+)?\s*([ap]m)?$"
EXCEL_EPOCH = "DATE '1899-12-30'"
EXCEL_SERIAL_MAX = 60000
NUMERIC_DATE = r"^\s*(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})\s*$"


def detect_convention(con, view: str, column: str) -> dict:
    r = con.execute(f"""
        SELECT count(*) FILTER (WHERE a > 12 AND b <= 12),
               count(*) FILTER (WHERE b > 12 AND a <= 12),
               count(*) FILTER (WHERE a <= 12 AND b <= 12),
               count(*)
        FROM (
            SELECT TRY_CAST(regexp_extract(v, '{NUMERIC_DATE}', 1) AS INTEGER) AS a,
                   TRY_CAST(regexp_extract(v, '{NUMERIC_DATE}', 2) AS INTEGER) AS b
              FROM (SELECT trim(CAST({quote_ident(column)} AS VARCHAR)) AS v FROM {view})
             WHERE regexp_matches(v, '{NUMERIC_DATE}')
        )""").fetchone()
    dmy, mdy, ambiguous, numeric = r or (0, 0, 0, 0)
    convention = "MDY" if mdy > dmy else "DMY"
    return {"konvensi": convention, "bukti_dmy": dmy, "bukti_mdy": mdy,
            "ambigu": ambiguous, "numerik": numeric,
            "dasar": (f"{dmy:,} baris hanya masuk akal sebagai DD-MM, "
                      f"{mdy:,} hanya sebagai MM-DD; {ambiguous:,} baris ambigu "
                      f"dibaca sebagai {convention}")}


def _months_sql(expr: str) -> str:
    for local, english in MONTHS_ID:
        expr = f"regexp_replace({expr}, '\\b{local}\\b', '{english}', 'g')"
    return expr


def detect_excel_serial(con, view: str, column: str) -> bool:
    r = con.execute(f"""
        SELECT count(*) FILTER (WHERE regexp_matches(v, '^[0-9]{{4,5}}$')
                                  AND TRY_CAST(v AS INTEGER) BETWEEN 1 AND {EXCEL_SERIAL_MAX}),
               count(*)
          FROM (SELECT trim(CAST({quote_ident(column)} AS VARCHAR)) AS v FROM {view})
         WHERE nullif(v, '') IS NOT NULL""").fetchone()
    serial, total = r or (0, 0)
    return bool(total) and serial / total > 0.5


def normalised_date_sql(column: str, convention: str = "DMY", has_letters: bool = True,
                        excel_serial: bool = False) -> str:
    raw = f"lower(trim(CAST({quote_ident(column)} AS VARCHAR)))"
    no_time = f"trim(regexp_replace({raw}, '{TIME_SUFFIX}', ''))"
    text = _months_sql(no_time) if has_letters else no_time

    def chain(neutral, primary, fallback):
        parts = [f"try_strptime({text}, '{f}')" for f in neutral + primary + fallback]
        return "COALESCE(" + ", ".join(parts) + ")"

    if convention == "MDY":
        four = chain(FORMAT_NEUTRAL, FORMAT_MDY, FORMAT_DMY)
        two = chain(FORMAT_NEUTRAL_YY, FORMAT_MDY_YY, FORMAT_DMY_YY)
    else:
        four = chain(FORMAT_NEUTRAL, FORMAT_DMY, FORMAT_MDY)
        two = chain(FORMAT_NEUTRAL_YY, FORMAT_DMY_YY, FORMAT_MDY_YY)
    width = "{4,5}" if excel_serial else "{5}"
    serial = (f"CASE WHEN regexp_matches({text}, '^[0-9]{width}$') "
              f"AND TRY_CAST({text} AS INTEGER) BETWEEN 1 AND {EXCEL_SERIAL_MAX} "
              f"THEN CAST({EXCEL_EPOCH} + to_days(TRY_CAST({text} AS INTEGER)) AS TIMESTAMP) END")
    ts = (f"COALESCE({serial}, CASE WHEN regexp_matches({text}, '{ENDS_TWO_DIGITS}') "
          f"AND NOT regexp_matches({text}, '{STARTS_FOUR_DIGITS}') THEN {two} ELSE {four} END)")
    plausible = (f"CASE WHEN ({ts}) > current_date THEN ({ts}) - INTERVAL 100 YEAR "
                 f"ELSE ({ts}) END")
    return (f"CASE WHEN {plausible} IS NULL OR year({plausible}) < 1900 THEN NULL "
            f"ELSE strftime({plausible}, '%d-%m-%Y') END")


def has_letters(con, view: str, column: str) -> bool:
    return bool(con.execute(
        f"SELECT count(*) > 0 FROM {view} "
        f"WHERE regexp_matches(CAST({quote_ident(column)} AS VARCHAR), '[A-Za-z]')"
    ).fetchone()[0])


def build_view(con, source: str, target: str, mapping: dict, original: list[str],
               extra: list[str] | None = None) -> dict:
    select, date_info = [], None
    for element in ALL:
        column = mapping.get(element)
        if not column:
            continue
        if element == "tanggal_lahir":
            date_info = detect_convention(con, source, column)
            letters = has_letters(con, source, column)
            serial = detect_excel_serial(con, source, column)
            date_info["ada_nama_bulan"] = letters
            date_info["serial_excel"] = serial
            select.append(normalised_date_sql(column, date_info["konvensi"], letters, serial)
                          + f" AS {element}")
        else:
            select.append(f"{quote_ident(column)} AS {element}")
    used = set(mapping.values())
    remaining = [c for c in original if c not in used]
    select += [quote_ident(c) for c in remaining] + (extra or [])
    con.execute(f"CREATE OR REPLACE VIEW {target} AS SELECT {', '.join(select)} FROM {source}")
    return {"kolom_baku": [e for e in ALL if e in mapping],
            "kolom_dibawa_apa_adanya": remaining, "tanggal": date_info}
