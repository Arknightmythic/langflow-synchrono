import re
import time

from . import columns as cols
from . import duck, grading, names, sr
from . import settings as cfg
from .sql import now_text, sjson, sq

MASTER_COLUMNS = ["master_id", "nik", "nama_lengkap", "tempat_lahir", "tanggal_lahir",
                  "jenis_kelamin", "nama_ibu", "status_kematian", "provinsi", "kabupaten",
                  "kecamatan", "kelurahan",
                  *names.variant_columns("name"), *names.variant_columns("mother"),
                  "pob_c", "dob_md", "sex_c", "alive_c", "prov_c", "kab_c", "kec_c", "kel_c"]
DICTIONARY_SOURCES = {"tempat_lahir": "tempat_lahir", "nama": "nama_lengkap",
                      "nama_ibu": "nama_ibu", "provinsi": "provinsi", "kabupaten": "kabupaten",
                      "kecamatan": "kecamatan", "kelurahan": "kelurahan"}


def _mark(master_id: str, status: str, **values) -> None:
    current = sr.query(f"SELECT master_id FROM {cfg.DB_SERVICE}.masters "
                       f"WHERE master_id = {sq(master_id)}")
    detail = values.get("detail")
    row = {"source": values.get("source"), "row_count": values.get("row_count"),
           "load_ms": values.get("load_ms"), "error": values.get("error")}
    if current:
        parts = [f"status = {sq(status)}", f"updated_at = {sq(now_text())}"]
        parts += [f"{k} = {sq(v)}" for k, v in row.items() if v is not None]
        if detail is not None:
            parts.append(f"detail = {sjson(detail)}")
        sr.execute(f"UPDATE {cfg.DB_SERVICE}.masters SET {', '.join(parts)} "
                   f"WHERE master_id = {sq(master_id)}")
    else:
        sr.execute(f"INSERT INTO {cfg.DB_SERVICE}.masters VALUES ({sq(master_id)}, "
                   f"{sq(row['source'])}, {sq(status)}, {sq(row['row_count'])}, "
                   f"{sq(row['load_ms'])}, {sjson(detail)}, {sq(row['error'])}, "
                   f"{sq(now_text())})")


def status(master_id: str) -> dict | None:
    rows = sr.query(f"SELECT master_id, source, status, row_count, load_ms, error, "
                    f"CAST(detail AS VARCHAR) AS detail FROM {cfg.DB_SERVICE}.masters "
                    f"WHERE master_id = {sq(master_id)}")
    return rows[0] if rows else None


def _value_list(column: str, values: list[str], result: str) -> str:
    items = ", ".join(f"'{v}'" for v in values)
    return f"WHEN lower(trim(CAST({column} AS VARCHAR))) IN ({items}) THEN '{result}'"


def master_select(source: str, master_id: str) -> str:
    def text(expr: str) -> str:
        return sr.clean_text(expr)

    sex = (f"CASE {_value_list('jenis_kelamin', cols.GENDER_MALE, 'l')} "
           f"{_value_list('jenis_kelamin', cols.GENDER_FEMALE, 'p')} ELSE NULL END")
    alive = (f"CASE {_value_list('status_kematian', cols.ALIVE, 'h')} "
             f"{_value_list('status_kematian', cols.DEAD, 'm')} ELSE NULL END")
    raw = ["nik", "nama_lengkap", "tempat_lahir"]
    tail = ["jenis_kelamin", "nama_ibu", "status_kematian", "provinsi", "kabupaten",
            "kecamatan", "kelurahan"]
    return f"""
        SELECT '{master_id}' AS master_id,
               {', '.join(f'{text(c)} AS {c}' for c in raw)},
               CAST(tanggal_lahir AS DATE) AS tanggal_lahir,
               {', '.join(f'{text(c)} AS {c}' for c in tail)},
               {', '.join(f"{text(names.variant_sql('nama_lengkap', v))} AS name_{v}"
                          for v in names.VARIANTS)},
               {', '.join(f"{text(names.variant_sql('nama_ibu', v))} AS mother_{v}"
                          for v in names.VARIANTS)},
               {text('lower(trim(CAST(tempat_lahir AS VARCHAR)))')} AS pob_c,
               CAST(month(CAST(tanggal_lahir AS DATE)) * 100
                    + day(CAST(tanggal_lahir AS DATE)) AS SMALLINT) AS dob_md,
               {sex} AS sex_c, {alive} AS alive_c,
               {text('lower(trim(CAST(provinsi AS VARCHAR)))')} AS prov_c,
               {text('lower(trim(CAST(kabupaten AS VARCHAR)))')} AS kab_c,
               {text('lower(trim(CAST(kecamatan AS VARCHAR)))')} AS kec_c,
               {text('lower(trim(CAST(kelurahan AS VARCHAR)))')} AS kel_c
          FROM read_parquet('{source}')"""


def rebuild_dictionary() -> None:
    parts = " UNION ALL ".join(
        f"SELECT '{element}' AS element, lower(trim({column})) AS value "
        f"FROM {cfg.DB_MASTER}.persons WHERE {column} IS NOT NULL AND trim({column}) <> ''"
        for element, column in DICTIONARY_SOURCES.items())
    sr.execute(f"TRUNCATE TABLE {cfg.DB_MASTER}.dictionary")
    sr.execute(f"INSERT INTO {cfg.DB_MASTER}.dictionary "
               f"SELECT DISTINCT element, value FROM ({parts}) t")


def load(master_id: str, source: str, report=lambda stage: None, job: dict | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{1,128}", master_id):
        raise ValueError(f"master_id {master_id!r} must be 1-128 of [A-Za-z0-9_.-]")
    started = time.perf_counter()
    _mark(master_id, "LOADING", source=source)
    con = duck.connect()
    if job:
        grading.apply_s3_endpoint(con, job)
    try:
        report("master: clearing previous rows")
        sr.execute(f"DELETE FROM {cfg.DB_MASTER}.persons WHERE master_id = {sq(master_id)}")
        report("master: cleaning and stream loading")
        loaded = sr.stream_load_query(con, master_select(source, master_id), cfg.DB_MASTER,
                                      "persons", MASTER_COLUMNS,
                                      label_prefix=f"master_{re.sub(r'[^A-Za-z0-9_]', '_', master_id)}"
                                                   f"_{int(time.time())}")
        report("master: dictionary")
        dict_started = time.perf_counter()
        rebuild_dictionary()
        loaded["dictionaryMs"] = int((time.perf_counter() - dict_started) * 1000)
        loaded["totalMs"] = int((time.perf_counter() - started) * 1000)
        _mark(master_id, "READY", source=source, row_count=loaded["rows"],
              load_ms=loaded["totalMs"], detail=loaded)
        return {"masterId": master_id, **loaded}
    except Exception as e:
        _mark(master_id, "FAILED", source=source, error=f"{type(e).__name__}: {e}"[:4000])
        raise
    finally:
        con.close()
