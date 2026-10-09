import re
import socket
import time

from . import columns as cols
from . import duck, names, regions, rules, sr
from . import settings as cfg
from .sql import now_text, q, quote_ident

CORE = cols.CORE
DERIVED = ["nik_clean", "nik_prov", "nik_hari", "nik_bulan", "nik_tahun", "nik_trusted",
           "is_anomaly", "anomaly_type", "anomaly_notes", "nama_clean"]
OUTPUT_NAMES = {"nama": "nama_lengkap", "nama_ibu": "nama_ibu_kandung"}
LETTERS = rules.LETTERS
DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%d %m %Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d"]

TEXT_FILE = re.compile(r"\.(csv|tsv|txt)$", re.I)
EXCEL_FILE = re.compile(r"\.xlsx$", re.I)
LEGACY_EXCEL = re.compile(r"\.xls$", re.I)
EXECUTABLE_DUMP = re.compile(r"\.(sql|dmp|mdf)$", re.I)

XLS_MESSAGE = (
    "Format .xls (Excel 97-2003) tidak didukung. Ia bukan OOXML melainkan wadah "
    "biner OLE2, dan tidak ada pembaca .xls di DuckDB — baik extension `excel` "
    "maupun `spatial`. Selain itu formatnya hanya memuat 65.535 baris data per "
    "lembar, jauh di bawah ukuran berkas kependudukan yang biasa dikirim. "
    "Simpan ulang sebagai .xlsx atau ekspor ke CSV. Berkas: {path}")
DUMP_MISPLACED_MESSAGE = (
    "Berkas {ext} sampai ke pembaca grading, padahal seharusnya sudah diubah "
    "jadi parquet oleh layanan konversi lebih dulu. Ini bukan masalah berkasnya "
    "melainkan pembelokan jalur yang terlewat — periksa `convert_first()` di "
    "engine/conversion.py. Berkas: {path}")


def rejected_format(path: str, read_directly: bool = False) -> str | None:
    if LEGACY_EXCEL.search(path):
        return XLS_MESSAGE.format(path=path)
    match = EXECUTABLE_DUMP.search(path)
    if match and read_directly:
        return DUMP_MISPLACED_MESSAGE.format(ext=match.group(0).lower(), path=path)
    return None


def filled(column: str | None) -> str:
    if not column:
        return "FALSE"
    return f"nullif(trim(CAST({quote_ident(column)} AS VARCHAR)), '') IS NOT NULL"


def _unusable_endpoint(endpoint: str) -> bool:
    host = re.sub(r"^https?://", "", endpoint).split(":")[0].strip().lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "")


def _reachable(endpoint: str) -> bool:
    address = re.sub(r"^https?://", "", endpoint).rstrip("/")
    host, _, port = address.partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=3):
            return True
    except (OSError, ValueError):
        return False


def apply_s3_endpoint(con, job: dict) -> None:
    endpoint = str(job.get("s3_endpoint") or "").strip()
    if not endpoint or _unusable_endpoint(endpoint) or not _reachable(endpoint):
        return
    duck.set_s3(con, endpoint, cfg.S3_KEY, cfg.S3_SECRET,
                endpoint.lower().startswith("https://"))


def choose_source(job: dict, target: str) -> str:
    def pick(*keys) -> str:
        for key in keys:
            value = str(job.get(key) or "").strip()
            if value:
                return value
        return ""

    parquet = pick("parquet_key")
    raw = pick("raw_source_key", "csv_key")
    if parquet and parquet.strip("/") != target.strip("/"):
        return parquet
    if raw:
        return raw
    if parquet:
        raise ValueError(f"parquetKey menunjuk berkas keluaran ({parquet}) dan tidak ada "
                         f"csvKey/rawSourceKey sebagai gantinya. parquetKey harus berkas "
                         f"UNGGAHAN; kalau sama dengan enrichedParquetKey, hasil grading "
                         f"akan menimpa data aslinya.")
    raise ValueError("parquetKey, csvKey, maupun rawSourceKey kosong — "
                     "tidak ada berkas yang bisa dibaca")


def open_session(job: dict) -> dict:
    file_id = str(job.get("file_id") or "").strip()
    bucket = str(job.get("s3_bucket") or "").strip()
    if not file_id:
        raise ValueError("fileId kosong")
    if not bucket:
        raise ValueError("s3Bucket kosong")
    target = str(job.get("enriched_key") or f"uploads/{file_id}/enriched.parquet").strip()
    key = choose_source(job, target)
    con = duck.connect()
    apply_s3_endpoint(con, job)
    return {"con": con, "regions": regions.load(con), "file_id": file_id, "bucket": bucket,
            "source": f"s3://{bucket}/{key}", "enriched_key": target,
            "job_id": job.get("job_id"), "started": time.perf_counter()}


def _load_excel(con) -> None:
    try:
        con.execute("LOAD excel")
    except Exception:  # noqa: BLE001
        con.execute("INSTALL excel")
        con.execute("LOAD excel")


def _excel_source(con, path: str) -> str:
    _load_excel(con)
    types = {r[0]: r[1] for r in con.execute(
        f"DESCRIBE SELECT * FROM read_xlsx('{path}', header = true)").fetchall()}
    select = []
    for column, kind in types.items():
        ident = quote_ident(column)
        if kind.startswith(("DATE", "TIMESTAMP")):
            select.append(rf"regexp_replace(trim({ident}), '\.0+$', '') AS {ident}")
        else:
            select.append(ident)
    return (f"(SELECT {', '.join(select)} FROM "
            f"read_xlsx('{path}', header = true, all_varchar = true))")


def load_raw(s: dict) -> dict:
    con, path = s["con"], s["source"]
    reason = rejected_format(path, read_directly=True)
    if reason:
        raise ValueError(reason)
    if TEXT_FILE.search(path) or EXCEL_FILE.search(path):
        reader = (_excel_source(con, path) if EXCEL_FILE.search(path)
                  else f"read_csv_auto('{path}', all_varchar = true, sample_size = -1)")
        con.execute(f"CREATE OR REPLACE TEMP TABLE raw_src AS SELECT * FROM {reader}")
        con.execute("CREATE OR REPLACE TEMP VIEW raw_df AS SELECT *, rowid + 1 AS __row_no "
                    "FROM raw_src")
        kind = "xlsx" if EXCEL_FILE.search(path) else "CSV"
    else:
        con.execute(f"CREATE OR REPLACE VIEW raw_df AS SELECT * EXCLUDE (file_row_number), "
                    f"file_row_number + 1 AS __row_no "
                    f"FROM read_parquet('{path}', file_row_number = true)")
        kind = "parquet"
    original = [r[0] for r in con.execute("DESCRIBE raw_df").fetchall() if r[0] != "__row_no"]
    total = con.execute("SELECT count(*) FROM raw_df").fetchone()[0]
    if total == 0:
        raise ValueError(f"Berkas sumber kosong: {path}")
    print(f"[G2] {total:,} rows, {len(original)} columns ({kind})")
    mapped = cols.map_columns(con, "raw_df", original, allow_ai=s.get("allow_ai", True))
    mapping = mapped["mapping"]
    info = cols.build_view(con, "raw_df", "norm_df", mapping, original, extra=["__row_no"])
    normalised = [r[0] for r in con.execute("DESCRIBE norm_df").fetchall() if r[0] != "__row_no"]
    return {**s, "columns": normalised, "original_columns": original,
            "map": {e: e for e in mapping}, "original_map": mapping,
            "normalisation": {**info, "jejak": mapped["trace"]}, "row_count": total,
            "region_columns": [w for w in cols.REGION if w in mapping]}


def nik_sql(nik_column: str | None, regions_ready: bool) -> dict[str, str]:
    if not nik_column:
        return {"present": "FALSE", "clean": "CAST(NULL AS VARCHAR)",
                "prov": "CAST(NULL AS VARCHAR)", "day": "CAST(NULL AS INTEGER)",
                "month": "CAST(NULL AS INTEGER)", "year": "CAST(NULL AS INTEGER)",
                "raw_day": "CAST(NULL AS INTEGER)", "sex": "CAST(NULL AS VARCHAR)",
                "kec": "CAST(NULL AS VARCHAR)", "len_ok": "FALSE", "prov_ok": "FALSE",
                "kec_ok": "FALSE", "excel": "FALSE", "precision": "FALSE",
                "non_numeric": "FALSE"}
    raw = f"trim(CAST({quote_ident(nik_column)} AS VARCHAR))"
    excel = f"regexp_matches(upper({raw}), '^[0-9](\\.[0-9]+)?E[+-]?[0-9]+$')"
    precision = (rf"regexp_matches({raw}, '^[0-9]+\.[0-9]*$') "
                 rf"AND TRY_CAST(regexp_replace({raw}, '\..*$', '') AS DOUBLE) "
                 rf"> 9007199254740992")
    clean = f"""CASE
        WHEN {excel} THEN CAST(CAST(TRY_CAST({raw} AS DOUBLE) AS DECIMAL(20,0)) AS VARCHAR)
        WHEN regexp_matches({raw}, '^[0-9]+\\.0*$') THEN regexp_replace({raw}, '\\..*$', '')
        ELSE regexp_replace({raw}, '[^0-9]', '', 'g')
    END"""
    non_numeric = (f"nullif({raw}, '') IS NOT NULL AND {raw} <> '-' "
                   f"AND NOT regexp_matches({raw}, '^[0-9]+$') "
                   rf"AND NOT regexp_matches({raw}, '^[0-9]+\.0*$')")
    return {
        "present": "TRUE", "clean": clean, "excel": excel, "precision": precision,
        "non_numeric": non_numeric, "len_ok": "length(__nik_clean) = 16",
        "prov": "substr(__nik_clean, 1, 2)", "kec": "substr(__nik_clean, 1, 6)",
        "prov_ok": regions.province_valid_sql("__nik_clean") if regions_ready else "TRUE",
        "kec_ok": regions.kecamatan_valid_sql("__nik_clean") if regions_ready else "TRUE",
        "raw_day": "TRY_CAST(substr(__nik_clean, 7, 2) AS INTEGER)",
        "day": "CASE WHEN __nik_raw_day > 40 THEN __nik_raw_day - 40 ELSE __nik_raw_day END",
        "month": "TRY_CAST(substr(__nik_clean, 9, 2) AS INTEGER)",
        "year": "TRY_CAST(substr(__nik_clean, 11, 2) AS INTEGER)",
        "sex": "CASE WHEN __nik_raw_day > 40 THEN 'p' ELSE 'l' END",
    }


def name_sql(name_column: str | None) -> dict[str, str]:
    if not name_column:
        return {"clean": "CAST(NULL AS VARCHAR)", "title": "FALSE", "patronym": "FALSE"}
    ident = quote_ident(name_column)
    return {"clean": names.full_clean_sql(ident), "title": names.has_title_sql(ident),
            "patronym": names.has_patronym_sql(ident)}


def gender_sql(column: str | None) -> str:
    if not column:
        return "NULL"
    value = f"lower(trim(CAST({quote_ident(column)} AS VARCHAR)))"
    male = ", ".join(f"'{v}'" for v in cols.GENDER_MALE)
    female = ", ".join(f"'{v}'" for v in cols.GENDER_FEMALE)
    return f"CASE WHEN {value} IN ({male}) THEN 'l' WHEN {value} IN ({female}) THEN 'p' END"


def anomaly_rules(has_nik: bool, date_column: str | None, sex_column: str | None,
                  has_name: bool = False) -> list[tuple[str, str, str]]:
    empty_nik = "list_contains(__empty_elements, 'nik')"
    items = [("EMPTY_NIK", empty_nik, "'Kolom NIK tidak terisi'")]
    if has_nik:
        items += [
            ("NON_NUMERIC_NIK", "__nik_non_numeric",
             "CASE WHEN __nik_excel THEN 'NIK rusak akibat notasi ilmiah Excel "
             "(digit belakang tidak dapat dipulihkan)' "
             "ELSE 'NIK memuat karakter selain angka' END"),
            ("EXCEL_PRECISION_NIK", "__nik_precision",
             "'NIK tersimpan sebagai angka pecahan di atas batas presisi "
             "(2^53), sehingga digit belakangnya mungkin sudah bergeser'"),
            ("INVALID_NIK_LENGTH", f"NOT {empty_nik} AND NOT __nik_len_ok",
             "'Panjang NIK ' || CAST(length(__nik_clean) AS VARCHAR) || ' digit, seharusnya 16'"),
            ("NIK_PROVINCE_INVALID", "__nik_len_ok AND NOT __nik_prov_ok",
             "'Kode provinsi NIK tidak dikenali: ' || __nik_prov"),
            ("NIK_KECAMATAN_INVALID", "__nik_len_ok AND NOT __nik_kec_ok",
             "'Kode wilayah 6 digit NIK tidak dikenali: ' || __nik_kec"),
            ("NIK_DOB_INVALID", "__nik_len_ok AND __nik_bad_date",
             "'Digit tanggal lahir pada NIK di luar rentang wajar'"),
            ("DUPLICATE_NIK", "__nik_duplicate",
             "'NIK duplikat di dalam berkas (muncul ' || CAST(__nik_count AS VARCHAR) || ' kali)'"),
        ]
    if date_column and has_nik:
        items.append(("NIK_DOB_MISMATCH", "__dob_mismatch",
                      "'Beda Tanggal Lahir: digit NIK menunjuk ' || CAST(__nik_day AS VARCHAR) "
                      "|| '/' || CAST(__nik_month AS VARCHAR) || ', kolom tanggal lahir terisi ' "
                      "|| strftime(__dob, '%d/%m/%Y')"))
    if sex_column and has_nik:
        items.append(("NIK_GENDER_MISMATCH", "__sex_mismatch",
                      "'Beda Jenis Kelamin: digit hari NIK mengindikasikan ' || "
                      "CASE WHEN __nik_sex = 'p' THEN 'wanita' ELSE 'pria' END || "
                      "' (' || CAST(__nik_raw_day AS VARCHAR) || "
                      "'), namun kolom jenis kelamin terisi ' || upper(__sex)"))
    if has_name:
        items += [
            ("NAME_HAS_TITLE", "__name_title",
             "'Nama memuat gelar akademik atau sebutan kehormatan; dibersihkan ke kolom nama_clean'"),
            ("NAME_HAS_PATRONYM", "__name_patronym",
             "'Nama memuat patronimik bin/binti; dibersihkan ke kolom nama_clean'"),
        ]
    items += [
        ("EMPTY_NAME", "list_contains(__empty_elements, 'nama')", "'Kolom nama lengkap kosong'"),
        ("MISSING_CORE_ELEMENT",
         "length(list_filter(__empty_elements, x -> x NOT IN ('nik','nama'))) > 0",
         "'Elemen kosong: ' || array_to_string("
         "list_filter(__empty_elements, x -> x NOT IN ('nik','nama')), ', ')"),
    ]
    return items


def _join(parts: list[str]) -> str:
    return f"array_to_string(list_filter([{', '.join(parts)}], x -> x IS NOT NULL), '; ')"


def notes_sql(*args) -> str:
    return _join([f"CASE WHEN {cond} THEN {text} END" for _, cond, text in anomaly_rules(*args)])


def types_sql(*args) -> str:
    joined = _join([f"CASE WHEN {cond} THEN '{code}' END" for code, cond, _ in anomaly_rules(*args)])
    return f"CASE WHEN {joined} = '' THEN 'CLEAN' ELSE {joined} END"


def clean_and_flag(s: dict) -> dict:
    con, mapping = s["con"], s["map"]
    ready = bool((s.get("regions") or {}).get("available"))
    nik = nik_sql(mapping.get("nik"), ready)
    name = name_sql(mapping.get("nama"))
    date_column = mapping.get("tanggal_lahir")
    dob = f"try_strptime({quote_ident(date_column)}, '%d-%m-%Y')" if date_column else "NULL"
    sex = gender_sql(mapping.get("jenis_kelamin"))
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _step1 AS
        SELECT r.*, {nik['clean']} AS __nik_clean, {nik['excel']} AS __nik_excel,
               {nik['precision']} AS __nik_precision, {nik['non_numeric']} AS __nik_non_numeric,
               CAST({dob} AS DATE) AS __dob, {sex} AS __sex, {name['clean']} AS __name_clean,
               {name['title']} AS __name_title, {name['patronym']} AS __name_patronym
          FROM norm_df r""")
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW _step2 AS
        SELECT *, {nik['len_ok']} AS __nik_len_ok, {nik['prov']} AS __nik_prov,
               {nik['prov_ok']} AS __nik_prov_ok, {nik['kec']} AS __nik_kec,
               {nik['kec_ok']} AS __nik_kec_ok, {nik['raw_day']} AS __nik_raw_day,
               {nik['month']} AS __nik_month, {nik['year']} AS __nik_year
          FROM _step1""")
    con.execute(f"CREATE OR REPLACE TEMP VIEW _step3 AS SELECT *, {nik['day']} AS __nik_day, "
                f"{nik['sex']} AS __nik_sex FROM _step2")
    con.execute("""
        CREATE OR REPLACE TEMP VIEW _step4 AS
        SELECT *, CASE WHEN __nik_len_ok THEN count(*) OVER (PARTITION BY __nik_clean)
                       ELSE 1 END AS __nik_count
          FROM _step3""")
    dob_mismatch = ("__dob IS NOT NULL AND __nik_len_ok AND (day(__dob) <> __nik_day "
                    "OR month(__dob) <> __nik_month OR (year(__dob) % 100) <> __nik_year)"
                    ) if date_column else "FALSE"
    sex_mismatch = ("__sex IS NOT NULL AND __nik_len_ok AND __sex <> __nik_sex"
                    if mapping.get("jenis_kelamin") else "FALSE")
    empty_checks = [f"CASE WHEN {filled(mapping.get(e))} THEN NULL ELSE '{e}' END" for e in CORE]
    args = (bool(mapping.get("nik")), date_column, mapping.get("jenis_kelamin"),
            bool(mapping.get("nama")))
    con.execute(f"""
        CREATE OR REPLACE TABLE verdict_df AS
        SELECT *, ({dob_mismatch}) AS __dob_mismatch, ({sex_mismatch}) AS __sex_mismatch,
               (__nik_count > 1) AS __nik_duplicate,
               (__nik_day NOT BETWEEN 1 AND 31 OR __nik_month NOT BETWEEN 1 AND 12)
                   AS __nik_bad_date,
               list_filter([{', '.join(empty_checks)}], x -> x IS NOT NULL) AS __empty_elements
          FROM _step4""")
    has_nik = "TRUE" if mapping.get("nik") else "FALSE"
    kec_required = "__nik_kec_ok" if cfg.REGION_STRICT_KECAMATAN else "TRUE"
    trusted_out = "__trusted" if mapping.get("nik") else "CAST(NULL AS BOOLEAN)"
    con.execute(f"""
        CREATE OR REPLACE VIEW anomaly_df AS
        SELECT *, {trusted_out} AS nik_trusted,
               ((NOT __trusted AND {has_nik})
                OR ({has_nik} AND __nik_len_ok AND NOT __nik_kec_ok)
                OR length(__empty_elements) > 0
                OR __name_title OR __name_patronym) AS is_anomaly,
               __name_clean AS nama_clean, __notes AS anomaly_notes, __types AS anomaly_type
          FROM (
            SELECT *, ({has_nik} AND __nik_len_ok AND __nik_prov_ok AND {kec_required}
                       AND NOT __nik_excel AND NOT __nik_precision
                       AND NOT COALESCE(__nik_bad_date, TRUE) AND NOT __dob_mismatch
                       AND NOT __sex_mismatch AND NOT __nik_duplicate) AS __trusted,
                   {notes_sql(*args)} AS __notes, {types_sql(*args)} AS __types
              FROM verdict_df)""")
    anomalies = con.execute("SELECT count(*) FROM anomaly_df WHERE is_anomaly").fetchone()[0]
    print(f"[G3] {anomalies:,} of {s['row_count']:,} rows flagged")
    return {**s, "anomaly_count": anomalies}


def _matches_criteria(c: dict, present: dict, rate: dict, trusted_rate: float) -> bool:
    if c["nik_column"] == "required" and not present["nik"]:
        return False
    if c["nik_column"] == "forbidden" and present["nik"]:
        return False
    for element in CORE:
        minimum = c.get(f"min_{element}")
        if minimum is not None and rate[element] < minimum:
            return False
    minimum = c.get("min_nik_trusted")
    return not (minimum is not None and trusted_rate < minimum)


def decide_grade(criteria: list[dict], present, rate, metrics, total, region_columns,
                 combinations: list[list[str]]) -> int:
    if not criteria:
        raise RuntimeError("Tabel grade_criteria kosong — tidak ada satu pun kriteria aktif, "
                           "sehingga setiap berkas akan jatuh ke E atau F. "
                           "Jalankan: python schema/apply.py")
    trusted_rate = (metrics["trusted"] / total) if total else 0.0
    for c in criteria:
        if _matches_criteria(c, present, rate, trusted_rate):
            return int(c["grade_id"])

    def has(element: str) -> bool:
        return bool(region_columns) if element == "wilayah" else bool(present.get(element))

    for combination in combinations:
        if combination and all(has(e) for e in combination):
            return 5
    return 6


def _ambiguous_dates(con, column: str | None, info: dict | None) -> bool:
    if not column:
        return False
    if info and info.get("bukti_dmy") and info.get("bukti_mdy"):
        return True
    top = con.execute("SELECT max(day(__dob)) FROM anomaly_df WHERE __dob IS NOT NULL").fetchone()[0]
    return top is not None and top <= 12


def score_and_grade(s: dict) -> dict:
    con, mapping, total = s["con"], s["map"], s["row_count"]
    present_sql = {e: filled(mapping.get(e)) for e in CORE}
    select = [f"count(*) FILTER (WHERE {present_sql[e]}) AS filled_{e}" for e in CORE]
    select += [
        "count(*) FILTER (WHERE nik_trusted) AS trusted",
        "count(*) FILTER (WHERE __nik_len_ok) AS len16",
        "count(*) FILTER (WHERE __nik_duplicate) AS duplicate",
        "count(*) FILTER (WHERE __dob_mismatch) AS dob_mismatch",
        "count(*) FILTER (WHERE __sex_mismatch) AS sex_mismatch",
        "count(*) FILTER (WHERE __nik_len_ok AND NOT __nik_prov_ok) AS bad_province",
        "count(*) FILTER (WHERE __nik_len_ok AND NOT __nik_kec_ok) AS bad_kecamatan",
        "count(*) FILTER (WHERE __name_title) AS name_title",
        "count(*) FILTER (WHERE __name_patronym) AS name_patronym",
        "count(*) FILTER (WHERE __nik_excel) AS excel",
        "count(*) FILTER (WHERE __nik_precision) AS precision_loss",
        "count(DISTINCT __nik_clean) FILTER (WHERE __nik_duplicate) AS duplicate_groups",
        "count(*) FILTER (WHERE is_anomaly) AS anomalies",
    ]
    row = con.execute(f"SELECT {', '.join(select)} FROM anomaly_df").fetchone()
    metrics = dict(zip([d[0] for d in con.description], row))
    present = {e: e in mapping for e in CORE}
    rate = {e: (metrics[f"filled_{e}"] / total if present[e] else 0.0) for e in CORE}
    glob = rules.global_values()
    version = rules.record_version()
    grade = decide_grade(rules.criteria(), present, rate, metrics, total, s["region_columns"],
                         glob["grading.gradeECombinations"])
    grade_band = rules.band(grade)
    listed = [e for e in CORE if present[e]]
    completeness = sum(rate[e] for e in listed) / len(listed) if listed else 0.0
    if present["nik"]:
        weights = glob["grading.scoreWeights"]
        quality = (weights["kelengkapan"] * completeness
                   + weights["nik_tepercaya"] * (metrics["trusted"] / total))
    else:
        quality = completeness
    score = round(grade_band["score_min"]
                  + (grade_band["score_max"] - grade_band["score_min"]) * quality)
    ambiguous = _ambiguous_dates(con, mapping.get("tanggal_lahir"),
                                 (s.get("normalisation") or {}).get("tanggal"))
    print(f"[G4] grade {LETTERS[grade]} score {score} quality {quality:.3f} config {version}")
    return {**s, "grade": grade, "quality_score": score, "config_version": version,
            "band": grade_band, "metrics": metrics, "rate": rate, "present": present,
            "case_flags": {"hasExcelScientificNik": bool(metrics["excel"]),
                           "hasExcelPrecisionNik": bool(metrics["precision_loss"]),
                           "hasAmbiguousDateFormats": ambiguous}}


def write_enriched(s: dict) -> dict:
    con, mapping = s["con"], s["map"]
    derived = {d.lower() for d in DERIVED}
    original = [c for c in s["columns"] if c.lower() not in derived]
    has_nik = bool(mapping.get("nik"))
    select = [f"{quote_ident(c)} AS {quote_ident(OUTPUT_NAMES.get(c, c))}" for c in original] + [
        "__nik_clean AS nik_clean" if has_nik else "CAST(NULL AS VARCHAR) AS nik_clean",
        "__nik_prov AS nik_prov" if has_nik else "CAST(NULL AS VARCHAR) AS nik_prov",
        "CASE WHEN __nik_len_ok THEN __nik_day END AS nik_hari",
        "CASE WHEN __nik_len_ok THEN __nik_month END AS nik_bulan",
        "CASE WHEN __nik_len_ok THEN __nik_year END AS nik_tahun",
        "nik_trusted", "is_anomaly", "anomaly_type", "anomaly_notes", "nama_clean",
    ]
    target = f"s3://{s['bucket']}/{s['enriched_key']}"
    con.execute(f"COPY (SELECT {', '.join(select)} FROM anomaly_df) TO '{target}' "
                f"(FORMAT PARQUET)")
    try:
        size = con.execute(f"SELECT sum(total_compressed_size) FROM parquet_metadata('{target}')"
                           ).fetchone()[0]
        size = int(size) if size is not None else None
    except Exception:  # noqa: BLE001
        size = None
    return {**s, "enriched_path": target, "parquet_size_bytes": size}


def date_sql(column: str) -> str:
    raw = f"lower(trim(CAST({column} AS VARCHAR)))"
    attempts = ", ".join(f"try_strptime({raw}, '{f}')" for f in DATE_FORMATS)
    return (f"CASE WHEN year(COALESCE({attempts})) < 1900 THEN NULL "
            f"ELSE CAST(COALESCE({attempts}) AS DATE) END")


def _value_list(column: str, values: list[str], result: str) -> str:
    items = ", ".join(f"'{v}'" for v in values)
    return f"WHEN lower(trim(CAST({column} AS VARCHAR))) IN ({items}) THEN '{result}'"


REGION_SHORT = [("provinsi", "prov"), ("kabupaten", "kab"), ("kecamatan", "kec"),
                ("kelurahan", "kel")]

KL_COLUMNS = ["file_id", "row_id", "nik", "nama", "tempat_lahir", "tanggal_lahir",
              "jenis_kelamin", "nama_ibu", "provinsi", "kabupaten", "kecamatan", "kelurahan",
              "status_hidup", "nik_trusted", "is_anomaly", "anomaly_type", "anomaly_notes",
              *names.variant_columns("name"), *names.variant_columns("mother"),
              "pob_c", "dob", "dob_md", "sex_c", "alive_c", "prov_c", "kab_c", "kec_c", "kel_c",
              "grading_job_id", "created_at", "created_by"]


def kl_select(columns: list[str], file_id: str, job_id: str | None, source: str,
              actor: str | None = None) -> str:
    present = set(columns)

    def col(name: str) -> str:
        return name if name in present else "CAST(NULL AS VARCHAR)"

    def text(expr: str) -> str:
        return sr.clean_text(expr)

    row_id = ("trim(CAST(id AS VARCHAR))" if "id" in present
              else "CAST(__row_no AS VARCHAR)")
    name, mother = col("nama"), col("nama_ibu")
    sex = (f"CASE {_value_list(col('jenis_kelamin'), cols.GENDER_MALE, 'l')} "
           f"{_value_list(col('jenis_kelamin'), cols.GENDER_FEMALE, 'p')} ELSE NULL END")
    alive = (f"CASE {_value_list(col('status_hidup'), cols.ALIVE, 'h')} "
             f"{_value_list(col('status_hidup'), cols.DEAD, 'm')} ELSE NULL END")
    trusted = "CAST(nik_trusted AS INTEGER)" if "nik_trusted" in present else "NULL"
    anomaly = "CAST(is_anomaly AS INTEGER)" if "is_anomaly" in present else "NULL"
    return f"""
        SELECT {q(file_id)} AS file_id,
               {text(row_id)} AS row_id,
               {text(f"trim(CAST({col('nik')} AS VARCHAR))")} AS nik,
               {text(name)} AS nama, {text(col('tempat_lahir'))} AS tempat_lahir,
               {text(col('tanggal_lahir'))} AS tanggal_lahir,
               {text(col('jenis_kelamin'))} AS jenis_kelamin, {text(mother)} AS nama_ibu,
               {text(col('provinsi'))} AS provinsi, {text(col('kabupaten'))} AS kabupaten,
               {text(col('kecamatan'))} AS kecamatan, {text(col('kelurahan'))} AS kelurahan,
               {text(col('status_hidup'))} AS status_hidup,
               {trusted} AS nik_trusted, {anomaly} AS is_anomaly,
               {text(col('anomaly_type'))} AS anomaly_type,
               {text(col('anomaly_notes'))} AS anomaly_notes,
               {', '.join(f"{text(names.variant_sql(name, v))} AS name_{v}" for v in names.VARIANTS)},
               {', '.join(f"{text(names.variant_sql(mother, v))} AS mother_{v}" for v in names.VARIANTS)},
               {text(f"lower(trim(CAST({col('tempat_lahir')} AS VARCHAR)))")} AS pob_c,
               __kl_dob AS dob,
               CAST(month(__kl_dob) * 100 + day(__kl_dob) AS SMALLINT) AS dob_md,
               {sex} AS sex_c, {alive} AS alive_c,
               {', '.join(f"{text(f'lower(trim(CAST({col(w)} AS VARCHAR)))')} AS {short}_c"
                          for w, short in REGION_SHORT)},
               {q(job_id or '')} AS grading_job_id,
               {q(now_text())} AS created_at, {q(actor or cfg.ENGINE_ACTOR)} AS created_by
          FROM (SELECT *, {date_sql(col('tanggal_lahir'))} AS __kl_dob FROM {source})"""


def load_kl(s: dict) -> dict:
    con = s["con"]
    started = time.perf_counter()
    sr.drop_file(f"{cfg.T_KL}records", s["file_id"])
    columns = [r[0] for r in con.execute("DESCRIBE anomaly_df").fetchall()]
    select = kl_select(columns, s["file_id"], s.get("job_id"), "anomaly_df")
    loaded = sr.stream_load_query(con, select, cfg.DB, cfg.P_KL + "records", KL_COLUMNS,
                                  label_prefix=f"kl_{re.sub(r'[^A-Za-z0-9_]', '_', s['file_id'])}"
                                               f"_{int(time.time())}")
    loaded["totalMs"] = int((time.perf_counter() - started) * 1000)
    print(f"[G6] {loaded['rows']:,} rows loaded into {cfg.T_KL}records in "
          f"{loaded['totalMs']:,} ms ({loaded['files']} files, {loaded['bytes']:,} bytes)")
    return {**s, "kl_load": loaded}


ENRICHED_COLUMNS = ["file_id", "row_no", "nik_trusted", "is_anomaly", "anomaly_type", "data",
                    "grading_job_id", "created_at", "created_by"]


def _json_value(column: str, kind: str) -> str:
    ident = quote_ident(column)
    # NaN and infinity have no JSON form, and StarRocks would reject the whole load.
    if kind.upper() in ("FLOAT", "DOUBLE", "REAL"):
        return f"CASE WHEN isfinite({ident}) THEN {ident} END"
    return ident


def load_enriched(s: dict) -> dict:
    """enriched.parquet as rows of syncrono_kl_enriched, so the portal can page through the
    grading result in StarRocks. Read back from the written file: same rows, order and values.
    StarRocks sorts JSON keys, so the parquet's column order goes into the result
    (enrichedStorage.columns)."""
    con = s["con"]
    started = time.perf_counter()
    con.execute(f"CREATE OR REPLACE VIEW enriched_out AS SELECT * FROM "
                f"read_parquet('{s['enriched_path']}', file_row_number = true)")
    described = [(r[0], r[1]) for r in con.execute("DESCRIBE enriched_out").fetchall()
                 if r[0] != "file_row_number"]
    fields = ", ".join(f"{q(name)}: {_json_value(name, kind)}" for name, kind in described)
    row_json = sr.clean_text("to_json({" + fields + "})")
    select = f"""
        SELECT {q(s['file_id'])} AS file_id, file_row_number + 1 AS row_no,
               CAST(nik_trusted AS INTEGER) AS nik_trusted,
               CAST(is_anomaly AS INTEGER) AS is_anomaly,
               {sr.clean_text('anomaly_type')} AS anomaly_type, {row_json} AS data,
               {q(s.get('job_id') or '')} AS grading_job_id,
               {q(now_text())} AS created_at, {q(cfg.ENGINE_ACTOR)} AS created_by
          FROM enriched_out"""
    sr.drop_file(f"{cfg.T_KL}enriched", s["file_id"])
    loaded = sr.stream_load_query(con, select, cfg.DB, cfg.P_KL + "enriched", ENRICHED_COLUMNS,
                                  label_prefix=f"en_{re.sub(r'[^A-Za-z0-9_]', '_', s['file_id'])}"
                                               f"_{int(time.time())}")
    loaded["totalMs"] = int((time.perf_counter() - started) * 1000)
    print(f"[G7] {loaded['rows']:,} rows loaded into {cfg.T_KL}enriched in "
          f"{loaded['totalMs']:,} ms")
    return {**s, "enriched_load": loaded, "enriched_columns": [name for name, _ in described]}


def _normalisation_summary(s: dict) -> dict:
    n = s.get("normalisation") or {}
    date = n.get("tanggal") or {}
    trace = n.get("jejak") or []
    return {
        "byLayer": {f"layer{layer}": sum(1 for t in trace
                                         if t.get("lapis") == layer and t.get("elemen"))
                    for layer in (1, 2, 3, 4, 5)},
        "renamed": [{"from": t["kolom"], "to": t["elemen"], "layer": t["lapis"],
                     "reason": t["dasar"]}
                    for t in trace if t.get("elemen") and t["kolom"] != t["elemen"]],
        "unrecognisedColumns": n.get("kolom_dibawa_apa_adanya", []),
        "dateNormalisation": {
            "convention": date.get("konvensi"), "outputFormat": "dd-mm-yyyy",
            "evidenceDayMonth": date.get("bukti_dmy"), "evidenceMonthDay": date.get("bukti_mdy"),
            "ambiguousRows": date.get("ambigu"), "hasMonthNames": date.get("ada_nama_bulan"),
            "note": date.get("dasar"),
        } if date else None,
        "aiUsed": any(t.get("lapis") == 5 and t.get("model") for t in trace),
        "trace": trace,
    }


def build_result(s: dict) -> dict:
    m, mapping, total = s["metrics"], s["map"], s["row_count"]
    grade, grade_band = s["grade"], s["band"]
    blocked = None
    if not grade_band["can_proceed"]:
        known = [e for e in CORE if e in mapping]
        blocked = (f"Grade {LETTERS[grade]}: kolom berkas tidak dapat dipetakan ke elemen "
                   f"kependudukan yang dibutuhkan ({len(known)} dari 6 elemen dikenali"
                   + (f": {', '.join(known)}" if known else "")
                   + "). Berkas memerlukan pemetaan kolom kustom sebelum dapat dicocokkan.")
    region = s.get("regions") or {}
    return {
        "fileId": s["file_id"], "status": "COMPLETED", "enrichedParquetKey": s["enriched_key"],
        "parquetSizeBytes": s.get("parquet_size_bytes"),
        "gradingDurationMs": int((time.perf_counter() - s["started"]) * 1000),
        "summary": {
            "grade": grade, "gradeLetter": LETTERS[grade], "qualityScore": s["quality_score"],
            "severityLabel": grade_band["severity_label"],
            "canProceedToSync": bool(grade_band["can_proceed"]), "blockedReason": blocked,
            "recordCount": total, "anomalyCount": m["anomalies"],
            "criteriaDescription": grade_band["criteria_description"],
        },
        "anomalyMetrics": {
            "totalAnomalies": m["anomalies"], "trustedNikCount": m["trusted"],
            "untrustedNikCount": (total - m["trusted"]) if mapping.get("nik") else 0,
            "duplicateNikCount": m["duplicate"], "duplicateNikGroupsCount": m["duplicate_groups"],
            "nikDobMismatchCount": m["dob_mismatch"], "nikGenderMismatchCount": m["sex_mismatch"],
            "nikProvinceInvalidCount": m["bad_province"],
            "nikKecamatanInvalidCount": m["bad_kecamatan"],
            "nameWithTitleCount": m["name_title"], "nameWithPatronymCount": m["name_patronym"],
        },
        "referenceData": {"wilayahSource": region.get("source"),
                          "wilayahAvailable": region.get("available"),
                          "wilayahKecamatanCount": region.get("kecamatan")},
        "elementDetails": {
            "columns": s["columns"], "columnCount": len(s["columns"]),
            "originalColumns": s.get("original_columns", s["columns"]),
            "recognisedElements": {e: (s.get("original_map") or {}).get(e) for e in CORE},
            "completeness": {e: round(s["rate"][e], 4) for e in CORE},
        },
        "normalization": _normalisation_summary(s),
        "caseFlags": s["case_flags"],
        "configVersion": s.get("config_version"),
        "klStorage": {"table": f"{cfg.T_KL}records", **(s.get("kl_load") or {})},
        "enrichedStorage": {"table": f"{cfg.T_KL}enriched", "columns": s.get("enriched_columns"),
                            **(s.get("enriched_load") or {})},
    }


def run(job: dict, report=None) -> dict:
    def stage(name: str) -> None:
        print(f"--- {name} ---")
        if report:
            report(name)

    stage("G1 open session")
    s = open_session(job)
    try:
        stage("G2 load raw parquet")
        s = load_raw(s)
        stage("G3 clean NIK & flag anomalies")
        s = clean_and_flag(s)
        stage("G4 score & grade")
        s = score_and_grade(s)
        stage("G5 write enriched parquet")
        s = write_enriched(s)
        stage("G6 load into StarRocks K/L")
        s = load_kl(s)
        stage("G7 load enriched rows into StarRocks")
        s = load_enriched(s)
        stage("G8 build callback payload")
        return {"session": s, "result": build_result(s)}
    finally:
        try:
            s["con"].close()
        except Exception:  # noqa: BLE001
            pass
