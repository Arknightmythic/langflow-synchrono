import hashlib
import resource
import time

from . import duck, grading, master, names, reasoning, rules, sr, udf
from . import settings as cfg
from .jobs import post_json
from .sql import now_text, sjson, sq

SVC, KL, PORTAL, MASTER = cfg.DB_SERVICE, cfg.DB_KL, cfg.DB_PORTAL, cfg.DB_MASTER
MAX_CANDIDATES = reasoning.MAX_CANDIDATES
NEAR_NIK_DIGITS = 2
NAME_TOTAL_MISMATCH = 0.70
SPELLING_THRESHOLD = 0.85
REGIONS = ["provinsi", "kabupaten", "kecamatan", "kelurahan"]
SHORT = {"provinsi": "prov", "kabupaten": "kab", "kecamatan": "kec", "kelurahan": "kel"}
PROPS = f'PROPERTIES ("replication_num" = "{cfg.SR_REPLICATION}")'


class Cancelled(Exception):
    pass


def build_job(payload: dict) -> dict:
    incoming = payload.get("incomingFile") or {}
    master_file = payload.get("masterDataFile") or {}
    job = {
        "job_id": str(payload.get("jobId") or "").strip(),
        "file_id": str(payload.get("fileId") or "").strip(),
        "master_file_id": str(payload.get("masterFileId") or "").strip(),
        "actor": str(payload.get("actor") or "").strip(),
        "callback_url": str(payload.get("callbackUrl") or "").strip(),
        "s3_bucket": str(payload.get("s3Bucket") or "").strip(),
        "s3_endpoint": str(payload.get("s3Endpoint") or "").strip(),
        "incoming_key": str(incoming.get("s3Key") or "").strip(),
        "master_key": str(master_file.get("s3Key") or "").strip(),
        "rule_preset": str(payload.get("rulePreset") or "").strip() or None,
        "grade": payload.get("grade"),
    }
    required = {"jobId": "job_id", "fileId": "file_id", "masterFileId": "master_file_id",
                "actor": "actor", "callbackUrl": "callback_url", "s3Bucket": "s3_bucket",
                "incomingFile.s3Key": "incoming_key", "masterDataFile.s3Key": "master_key"}
    missing = [name for name, key in required.items() if not job[key]]
    if missing:
        raise ValueError(f"Field wajib tidak ada: {', '.join(missing)}")
    return job


def register(job: dict) -> None:
    existing = sr.query(f"SELECT status FROM {PORTAL}.matching_jobs WHERE id = {sq(job['job_id'])}")
    if existing:
        sr.execute(f"UPDATE {PORTAL}.matching_jobs SET status = 'PENDING', current_stage = NULL, "
                   f"last_error = NULL, updated_at = {sq(now_text())} "
                   f"WHERE id = {sq(job['job_id'])}")
        return
    sr.execute(f"INSERT INTO {PORTAL}.matching_jobs (id, file_id, master_file_id, status, "
               f"created_at, created_by) VALUES ({sq(job['job_id'])}, {sq(job['file_id'])}, "
               f"{sq(job['master_file_id'])}, 'PENDING', {sq(now_text())}, {sq(job['actor'])})")


def portal_status(job_id: str) -> str | None:
    rows = sr.query(f"SELECT status FROM {PORTAL}.matching_jobs WHERE id = {sq(job_id)}")
    return rows[0]["status"] if rows else None


def mark(job_id: str, **columns) -> None:
    parts = []
    for key, value in columns.items():
        if value == "now()":
            parts.append(f"{key} = {sq(now_text())}")
        elif key in ("stage_durations", "blocking_metrics"):
            parts.append(f"{key} = {sjson(value)}")
        else:
            parts.append(f"{key} = {sq(value)}")
    parts += [f"updated_at = {sq(now_text())}", f"updated_by = {sq(cfg.ENGINE_ACTOR)}"]
    sr.execute(f"UPDATE {PORTAL}.matching_jobs SET {', '.join(parts)} WHERE id = {sq(job_id)}")


def check_cancelled(job: dict) -> None:
    if portal_status(job["job_id"]) == "CANCELLED":
        raise Cancelled(job["job_id"])


def find_grade(job: dict) -> int:
    if job.get("grade") not in (None, ""):
        return int(job["grade"])
    rows = sr.query(f"SELECT get_json_int(CAST(result AS VARCHAR), '$.summary.grade') AS grade "
                    f"FROM {SVC}.grading_jobs WHERE file_id = {sq(job['file_id'])} "
                    f"AND status = 'COMPLETED' ORDER BY created_at DESC LIMIT 1")
    if not rows or rows[0]["grade"] is None:
        raise ValueError(f"Berkas '{job['file_id']}' belum pernah digrading oleh engine ini, "
                         f"jadi grade-nya tidak diketahui. Jalankan grading lebih dulu.")
    grade = int(rows[0]["grade"])
    if grade not in rules.MATCHING_GRADES:
        raise ValueError(f"Berkas '{job['file_id']}' ber-grade {grade}; matching hanya "
                         f"tersedia untuk grade 1-5.")
    return grade


class Work:
    def __init__(self, job_id: str):
        self.prefix = "w_" + hashlib.sha1(job_id.encode()).hexdigest()[:10]

    def t(self, name: str) -> str:
        return f"{SVC}.{self.prefix}_{name}"

    def name(self, name: str) -> str:
        return f"{self.prefix}_{name}"

    def drop(self) -> None:
        sr.drop_tables(SVC, self.prefix + "_")


def _lacks_variant(table: str, where: str, variant: str) -> bool:
    if variant not in names.LATE_VARIANTS:
        return False
    return bool(sr.scalar(f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {where} "
                          f"AND ({names.missing_variant_sql(variant)}) LIMIT 1) t"))


def ensure_master(job: dict, report, variant: str = "v0") -> str:
    master_id = job["master_file_id"]
    state = master.status(master_id)
    if state and state["status"] == "READY" and not _lacks_variant(
            f"{MASTER}.persons", f"master_id = {sq(master_id)}", variant):
        return master_id
    report(f"loading master {master_id}")
    master.load(master_id, f"s3://{job['s3_bucket']}/{job['master_key']}", report, job)
    return master_id


def ensure_incoming(job: dict, con, variant: str = "v0") -> int:
    where = f"file_id = {sq(job['file_id'])}"
    n = sr.scalar(f"SELECT count(*) FROM {KL}.records WHERE {where}")
    if n and _lacks_variant(f"{KL}.records", where, variant):
        sr.execute(f"DELETE FROM {KL}.records WHERE {where}")
        n = 0
    if not n:
        source = f"s3://{job['s3_bucket']}/{job['incoming_key']}"
        con.execute(f"CREATE OR REPLACE VIEW enriched_src AS SELECT * FROM read_parquet("
                    f"'{source}', file_row_number = true)")
        present = [r[0] for r in con.execute("DESCRIBE enriched_src").fetchall()]
        aliases = {"nama_lengkap": "nama", "nama_ibu_kandung": "nama_ibu"}
        select = ", ".join(f'"{c}" AS "{aliases.get(c, c)}"' for c in present
                           if c != "file_row_number")
        con.execute(f"CREATE OR REPLACE VIEW enriched_df AS SELECT {select}, "
                    f"file_row_number + 1 AS __row_no FROM enriched_src")
        columns = [r[0] for r in con.execute("DESCRIBE enriched_df").fetchall()]
        sr.stream_load_query(con, grading.kl_select(columns, job["file_id"], None, "enriched_df"),
                             KL, "records", grading.KL_COLUMNS)
        n = sr.scalar(f"SELECT count(*) FROM {KL}.records WHERE file_id = {sq(job['file_id'])}")
    dup = sr.scalar(f"SELECT count(*) - count(DISTINCT row_id) FROM {KL}.records "
                    f"WHERE file_id = {sq(job['file_id'])}")
    if dup:
        raise ValueError(f"Kolom id pada berkas incoming tidak unik ({dup:,} duplikat). "
                         f"id_incoming harus menunjuk tepat satu baris.")
    return int(n)


def _create(table: str, columns: str, key: str = "id") -> None:
    sr.execute(f"DROP TABLE IF EXISTS {table} FORCE")
    sr.execute(f"CREATE TABLE {table} ({columns}) DUPLICATE KEY ({key}) "
               f"DISTRIBUTED BY HASH({key}) BUCKETS {cfg.SR_BUCKETS} {PROPS}")


DECISION_COLUMNS = ["id", "master_nik", "score", "status", "method", "rank_conflict",
                    "n_candidates", "owned", "n_tie", "pattern_group", "jw_name"]
DECISION_DDL = ("id VARCHAR(255), master_nik VARCHAR(64), score DOUBLE, status VARCHAR(16), "
                "method VARCHAR(32), rank_conflict BOOLEAN, n_candidates BIGINT, owned BOOLEAN, "
                "n_tie INT, pattern_group VARCHAR(32), jw_name DOUBLE")
CANDIDATE_COLUMNS = ["id", "rk", "nik", "score"]
CANDIDATE_DDL = "id VARCHAR(255), rk INT, nik VARCHAR(64), score DOUBLE"


def pass1(w: Work, con, file_id: str, master_id: str, nv: str, mv: str, contradiction: float) -> dict:
    sr.execute(f"DROP TABLE IF EXISTS {w.t('nik')} FORCE")
    sr.execute(f"""
        CREATE TABLE {w.t('nik')} {PROPS} AS
        SELECT i.row_id AS id, m.nik AS nik,
               (i.{nv} = m.{nv}) AS name_exact,
               ((i.dob IS NOT NULL AND m.tanggal_lahir IS NOT NULL AND i.dob <> m.tanggal_lahir)
                OR (i.sex_c IS NOT NULL AND m.sex_c IS NOT NULL AND i.sex_c <> m.sex_c))
                   AS hard_conflict,
               (nullif(i.{mv}, '') IS NOT NULL AND nullif(m.{mv}, '') IS NOT NULL
                AND i.{mv} <> m.{mv}) AS mother_check,
               i.{mv} AS mother_i, m.{mv} AS mother_m
          FROM {KL}.records i
          JOIN {MASTER}.persons m ON i.nik = m.nik
         WHERE i.file_id = {sq(file_id)} AND m.master_id = {sq(master_id)} AND i.nik_trusted""")
    pulled = sr.pull(con, "mother_pairs", f"SELECT id, nik, mother_i, mother_m FROM {w.t('nik')} "
                                          f"WHERE name_exact AND NOT hard_conflict AND mother_check",
                     key="id")
    con.execute(f"CREATE OR REPLACE TABLE mother_bad AS SELECT id, nik FROM mother_pairs "
                f"WHERE j(mother_i, mother_m) < {float(contradiction)}")
    bad = con.execute("SELECT count(*) FROM mother_bad").fetchone()[0]
    exclude = ""
    if bad:
        _create(w.t("mother_bad"), "id VARCHAR(255), nik VARCHAR(64)")
        sr.stream_load_query(con, "SELECT id, nik FROM mother_bad", SVC, w.name("mother_bad"),
                             ["id", "nik"])
        exclude = (f"LEFT ANTI JOIN {w.t('mother_bad')} b ON b.id = n.id AND b.nik = n.nik")
    sr.execute(f"DROP TABLE IF EXISTS {w.t('p1')} FORCE")
    sr.execute(f"""
        CREATE TABLE {w.t('p1')} {PROPS} AS
        SELECT n.id, count(DISTINCT n.nik) AS n_candidates, min(n.nik) AS nik,
               array_slice(array_sort(array_distinct(array_agg(n.nik))), 1, {MAX_CANDIDATES})
                   AS candidates
          FROM {w.t('nik')} n {exclude}
         WHERE n.name_exact AND NOT n.hard_conflict
         GROUP BY n.id""")
    return {"pairs": sr.scalar(f"SELECT count(*) FROM {w.t('nik')}"), "motherPulled": pulled,
            "motherContradictions": bad,
            "matched": sr.scalar(f"SELECT count(*) FROM {w.t('p1')}")}


def pass2(w: Work, con, file_id: str, master_id: str, nv: str, mv: str) -> list:
    sr.execute(f"DROP TABLE IF EXISTS {w.t('p2c')} FORCE")
    sr.execute(f"""
        CREATE TABLE {w.t('p2c')} {PROPS} AS
        SELECT i.row_id AS id, m.nik AS nik, i.nik AS nik_i,
               (o.id IS NOT NULL AND m.nik <> i.nik) AS owned,
               i.pob_c AS pob_i, m.pob_c AS pob_m
          FROM (SELECT * FROM {KL}.records
                 WHERE file_id = {sq(file_id)} AND nullif({nv}, '') IS NOT NULL
                   AND dob IS NOT NULL AND nullif({mv}, '') IS NOT NULL
                   AND row_id NOT IN (SELECT id FROM {w.t('p1')})) i
          JOIN (SELECT * FROM {MASTER}.persons WHERE master_id = {sq(master_id)}) m
            ON i.{nv} = m.{nv} AND i.dob = m.tanggal_lahir AND i.{mv} = m.{mv}
          LEFT JOIN (SELECT DISTINCT id FROM {w.t('nik')}) o ON o.id = i.row_id
         WHERE NOT (i.sex_c IS NOT NULL AND m.sex_c IS NOT NULL AND i.sex_c <> m.sex_c)""")
    pulled = sr.pull(con, "p2c", f"SELECT id, nik, nik_i, owned, pob_i, pob_m FROM {w.t('p2c')}",
                     key="id")
    words = ("list_distinct(list_filter(string_split(regexp_replace(COALESCE({c}, ''), "
             "'[^a-z0-9]+', ' ', 'g'), ' '), w -> w <> ''))")
    con.execute(f"""
        CREATE OR REPLACE TABLE p2 AS
        WITH pairs AS (
            SELECT id, nik, CAST(owned AS BOOLEAN) AS owned,
                   CASE WHEN length(nik_i) = length(nik)
                        THEN hamming(nik_i, nik) <= {NEAR_NIK_DIGITS} ELSE FALSE END AS near,
                   {words.format(c='pob_i')} AS words_i, {words.format(c='pob_m')} AS words_m,
                   j(pob_i, pob_m) AS pob_jw
              FROM p2c),
        per_nik AS (
            SELECT id, nik, bool_or(near) AS near,
                   max(CASE WHEN least(len(words_i), len(words_m)) = 0 THEN 0.0
                            ELSE len(list_intersect(words_i, words_m))
                                 / least(len(words_i), len(words_m)) END) AS pob_words,
                   max(pob_jw) AS pob_jw, bool_or(owned) AS owned
              FROM pairs GROUP BY id, nik),
        ranked AS (
            SELECT id, count(*) AS n_candidates,
                   list(nik ORDER BY near DESC, pob_words DESC, pob_jw DESC, nik) AS candidates,
                   bool_or(owned) AS owned
              FROM per_nik GROUP BY id)
        SELECT id, n_candidates, candidates[1] AS nik, candidates[1:{MAX_CANDIDATES}] AS candidates,
               owned
          FROM ranked""")
    con.execute("""
        CREATE OR REPLACE TABLE p2_decisions AS
        SELECT id, nik AS master_nik, 100.0 AS score,
               CASE WHEN n_candidates > 1 THEN 'CONFLICT' WHEN owned THEN 'REVIEW' ELSE 'AUTO' END
                   AS status,
               'PASS2_NAMA_TGL_IBU' AS method, n_candidates > 1 AS rank_conflict,
               n_candidates, owned, CASE WHEN n_candidates > 1 THEN n_candidates END AS n_tie,
               CASE WHEN n_candidates > 1 OR owned
                    THEN CASE WHEN owned THEN 'NIK_CONFLICT' ELSE 'GENERAL_REVIEW' END END
                   AS pattern_group,
               100.0 AS jw_name
          FROM p2""")
    con.execute("""
        CREATE OR REPLACE TABLE p2_candidates AS
        SELECT id, rk, candidates[rk] AS nik, 100.0 AS score
          FROM p2, range(1, len(candidates) + 1) t(rk)
         WHERE n_candidates > 1""")
    return [pulled, con.execute("SELECT count(*) FROM p2").fetchone()[0]]


def _term(field: str, date_match: str) -> str:
    if field == "wilayah":
        branches = ", ".join(
            f"CASE WHEN nullif({w}_clean, '') IS NOT NULL AND nullif({w}_master_clean, '') "
            f"IS NOT NULL THEN j({w}_clean, {w}_master_clean) END" for w in REGIONS)
        return f"COALESCE(list_avg(list_filter([{branches}], x -> x IS NOT NULL)), 0.0)"
    left = f"{field}_clean"
    right = "nama_master_clean" if field == "nama" else f"{field}_master_clean"
    if field == "tanggal_lahir":
        if date_match == "exact":
            return (f"CAST(CASE WHEN CAST({left} AS DATE) = CAST({right} AS DATE) "
                    f"THEN 1.0 ELSE 0.0 END AS DOUBLE)")
        left, right = f"CAST({left} AS VARCHAR)", f"CAST({right} AS VARCHAR)"
    return f"j({left}, {right})"


def score_sql(weights: list, date_match: str) -> str:
    terms = [f"{_term(f, date_match)} * {repr(float(b) / 100)}" for f, b in weights if b]
    return "(" + " + ".join(terms) + ") * 100" if terms else "0.0"


def missing_sql(elements: list) -> str:
    if not elements:
        return "0"
    terms = []
    for f in elements:
        if f == "wilayah":
            any_region = " OR ".join(f"nullif({w}_clean, '') IS NOT NULL" for w in REGIONS)
            terms.append(f"CASE WHEN {any_region} THEN 0 ELSE 1 END")
        else:
            terms.append(f"CASE WHEN nullif(CAST({f}_clean AS VARCHAR), '') IS NOT NULL "
                         f"THEN 0 ELSE 1 END")
    return " + ".join(terms)


CLASSIFY = """
    CASE
        WHEN (r.auto_missing_max IS NULL OR s.missing_count <= r.auto_missing_max)
             AND s.skor >= r.auto_score_min THEN 1
        WHEN (r.review_missing_count IS NULL OR s.missing_count = r.review_missing_count)
             AND s.skor >= r.review_score_min AND s.skor < r.review_score_max THEN 2
        ELSE 3
    END"""


def _rules_row(r: dict) -> str:
    def value(v, kind):
        return f"CAST(NULL AS {kind})" if v is None else f"{v}::{kind}"
    return (f"SELECT {value(r['auto_missing_max'], 'INTEGER')} AS auto_missing_max, "
            f"{value(r['auto_score_min'], 'DOUBLE')} AS auto_score_min, "
            f"{value(r['review_missing_count'], 'INTEGER')} AS review_missing_count, "
            f"{value(r['review_score_min'], 'DOUBLE')} AS review_score_min, "
            f"{value(r['review_score_max'], 'DOUBLE')} AS review_score_max")


def blocking_sql(w: Work, blocking: dict, file_id: str, master_id: str, nv: str, mv: str) -> str:
    remaining = (f"(SELECT * FROM {KL}.records WHERE file_id = {sq(file_id)} "
                 f"AND row_id NOT IN (SELECT id FROM {w.t('p1')}) "
                 f"AND row_id NOT IN (SELECT id FROM {w.t('dec')}))")
    persons = f"(SELECT * FROM {MASTER}.persons WHERE master_id = {sq(master_id)})"
    select = (f"i.row_id AS incoming_row_id, m.nik AS nik_master, i.nik AS nik_incoming, "
              f"i.{nv} AS nama_clean, m.{nv} AS nama_master_clean, "
              f"i.name_v3 AS nama_full, m.name_v3 AS nama_master_full, "
              f"i.pob_c AS tempat_lahir_clean, m.pob_c AS tempat_lahir_master_clean, "
              f"i.dob AS tanggal_lahir_clean, m.tanggal_lahir AS tanggal_lahir_master_clean, "
              f"i.sex_c AS jenis_kelamin_clean, m.sex_c AS jenis_kelamin_master_clean, "
              f"i.{mv} AS nama_ibu_clean, m.{mv} AS nama_ibu_master_clean, "
              + ", ".join(f"i.{SHORT[r]}_c AS {r}_clean, m.{SHORT[r]}_c AS {r}_master_clean"
                          for r in REGIONS))
    join = "LEFT JOIN" if blocking.get("join") == "left" else "JOIN"
    branches = [f"SELECT {select} FROM {remaining} i {join} {persons} m ON "
                f"{cond.replace('{name}', nv).replace('{mother}', mv)}"
                for cond in blocking["branches"]]
    union = " UNION ALL ".join(branches)
    return f"SELECT DISTINCT * FROM ({union}) t" if blocking.get("distinct") else union


def pulled_columns(rules_: dict) -> list[str]:
    needed = set(f for f, b in rules_["weights"] if b) | set(rules_["missing_elements"])
    columns = ["incoming_row_id", "nik_master", "nik_incoming", "nama_clean",
               "nama_master_clean", "nama_full", "nama_master_full", "tanggal_lahir_clean",
               "tanggal_lahir_master_clean"]
    for element in ("tempat_lahir", "jenis_kelamin", "nama_ibu"):
        if element in needed:
            columns += [f"{element}_clean", f"{element}_master_clean"]
    if "wilayah" in needed:
        for region in REGIONS:
            columns += [f"{region}_clean", f"{region}_master_clean"]
    return columns


def pass3(w: Work, con, rules_: dict, file_id: str, master_id: str, nv: str, mv: str,
          timings: dict) -> dict:
    t = time.perf_counter()
    sr.execute(f"DROP TABLE IF EXISTS {w.t('blocked')} FORCE")
    sr.execute(f"CREATE TABLE {w.t('blocked')} {PROPS} AS "
               f"{blocking_sql(w, rules_['blocking'], file_id, master_id, nv, mv)}")
    timings["blockingMs"] = int((time.perf_counter() - t) * 1000)
    t = time.perf_counter()
    pairs = sr.pull(con, "joined_df", f"SELECT {', '.join(pulled_columns(rules_))} "
                                      f"FROM {w.t('blocked')}", key="incoming_row_id")
    timings["candidatePullMs"] = int((time.perf_counter() - t) * 1000)
    t = time.perf_counter()
    eps = float(rules_["epsilon"])
    con.execute(f"""
        CREATE OR REPLACE TABLE p3 AS
        WITH scored AS (
            SELECT incoming_row_id AS id, nik_master,
                   CASE WHEN nik_master IS NULL THEN 0.0
                        ELSE {score_sql(rules_['weights'], rules_['date_match'])} END AS skor,
                   CASE WHEN nik_master IS NULL THEN 0
                        ELSE {missing_sql(rules_['missing_elements'])} END AS missing_count
              FROM joined_df),
        agg AS (
            SELECT id, arg_min({{'nik': nik_master, 'skor': skor, 'miss': missing_count}},
                               {{'a': -skor, 'b': nik_master}}, {2 * MAX_CANDIDATES}) AS top,
                   count(nik_master) AS n_candidates
              FROM scored GROUP BY id),
        unique_top AS (
            SELECT id, n_candidates,
                   list_filter(top, (x, i) -> x.nik IS NULL OR NOT list_contains(
                       list_transform(top[1:i - 1], y -> y.nik), x.nik)) AS top
              FROM agg),
        s AS (
            SELECT id, n_candidates, top[1].nik AS nik, top[1].skor AS skor,
                   top[1].miss AS missing_count,
                   list_filter(top, x -> x.nik IS NOT NULL AND top[1].skor - x.skor <= {eps})
                       AS tied
              FROM unique_top)
        SELECT s.id, s.n_candidates, s.nik, s.skor, len(s.tied) > 1 AS tie, len(s.tied) AS n_tie,
               s.tied[1:{MAX_CANDIDATES}] AS candidates,
               CASE WHEN s.nik IS NULL THEN 3 ELSE {CLASSIFY} END AS class
          FROM s CROSS JOIN ({_rules_row(rules_)}) r""")
    con.execute(f"""
        CREATE OR REPLACE TABLE p3_decisions AS
        WITH d AS (SELECT *, nik IS NULL OR class = 3 AS rejected FROM p3),
        chosen AS (
            SELECT d.id, j.nik_incoming, j.nama_clean, j.nama_master_clean, j.nama_full,
                   j.nama_master_full, j.tanggal_lahir_clean AS dob_i,
                   j.tanggal_lahir_master_clean AS dob_m
              FROM d JOIN joined_df j ON j.incoming_row_id = d.id AND j.nik_master = d.nik
             WHERE NOT d.rejected
            QUALIFY row_number() OVER (PARTITION BY d.id ORDER BY j.nama_master_clean,
                                       j.nama_master_full, j.tanggal_lahir_master_clean) = 1)
        SELECT d.id, CASE WHEN d.rejected THEN NULL ELSE d.nik END AS master_nik,
               round(d.skor, 2) AS score,
               CASE WHEN d.rejected THEN 'UNMATCH' WHEN d.tie THEN 'CONFLICT'
                    WHEN d.class = 1 THEN 'AUTO' ELSE 'REVIEW' END AS status,
               'SCORING' AS method, d.tie AND NOT d.rejected AS rank_conflict,
               d.n_candidates, FALSE AS owned,
               CASE WHEN d.tie AND NOT d.rejected THEN d.n_tie END AS n_tie,
               CASE WHEN NOT d.rejected AND (d.tie OR d.class = 2) THEN
                   CASE WHEN c.nik_incoming = d.nik
                             AND j(c.nama_clean, c.nama_master_clean) < {NAME_TOTAL_MISMATCH}
                            THEN 'NIK_CONFLICT'
                        WHEN c.nama_clean <> c.nama_master_clean
                             AND c.nama_full = c.nama_master_full THEN 'TITLE_DEGREE'
                        WHEN c.dob_i IS NOT NULL AND c.dob_m IS NOT NULL
                             AND CAST(c.dob_i AS DATE) <> CAST(c.dob_m AS DATE)
                             AND day(c.dob_i) = month(c.dob_m) AND month(c.dob_i) = day(c.dob_m)
                            THEN 'SWAPPED_DOB'
                        WHEN c.nama_clean <> c.nama_master_clean
                             AND j(c.nama_clean, c.nama_master_clean) >= {SPELLING_THRESHOLD}
                            THEN 'SPELLING_NAME'
                        ELSE 'GENERAL_REVIEW' END END AS pattern_group,
               CASE WHEN NOT d.rejected THEN round(j(c.nama_clean, c.nama_master_clean) * 100, 1) END
                   AS jw_name
          FROM d LEFT JOIN chosen c ON c.id = d.id""")
    con.execute("""
        CREATE OR REPLACE TABLE p3_candidates AS
        SELECT p.id, t.rk, p.candidates[t.rk].nik AS nik, round(p.candidates[t.rk].skor, 2) AS score
          FROM p3 p, range(1, len(p.candidates) + 1) t(rk)
         WHERE p.tie AND NOT (p.nik IS NULL OR p.class = 3)""")
    timings["scoringMs"] = int((time.perf_counter() - t) * 1000)
    return {"pairs": pairs, "rows": con.execute("SELECT count(*) FROM p3").fetchone()[0]}


MASTER_SIDE = {"nik_master": "nik", "nama_master_clean": "{nv}", "nama_master_full": "name_v3",
               "tempat_lahir_master_clean": "pob_c", "tanggal_lahir_master_clean": "tanggal_lahir",
               "jenis_kelamin_master_clean": "sex_c", "nama_ibu_master_clean": "{mv}",
               "provinsi_master_clean": "prov_c", "kabupaten_master_clean": "kab_c",
               "kecamatan_master_clean": "kec_c", "kelurahan_master_clean": "kel_c"}
INCOMING_SIDE = {"nik_incoming": "nik", "nama_clean": "{nv}", "nama_full": "name_v3",
                 "tempat_lahir_clean": "pob_c", "tanggal_lahir_clean": "dob",
                 "jenis_kelamin_clean": "sex_c", "nama_ibu_clean": "{mv}", "provinsi_clean": "prov_c",
                 "kabupaten_clean": "kab_c", "kecamatan_clean": "kec_c", "kelurahan_clean": "kel_c"}


def _double(value) -> str:
    return "CAST(NULL AS DOUBLE)" if value is None else f"CAST({float(value)!r} AS DOUBLE)"


def _jw(fn: dict, a: str, b: str) -> str:
    return (f"(CASE WHEN {a} IS NULL OR {b} IS NULL OR {a} = '' OR {b} = '' "
            f"THEN CAST(0.0 AS DOUBLE) ELSE {fn['jw']}({a}, {b}) END)")


def _term_columns(fn: dict, weights: list, date_match: str) -> dict[str, str]:
    terms = {}
    for field, share in weights:
        if not share:
            continue
        if field == "wilayah":
            for w in REGIONS:
                terms[f"t_{w}"] = (f"CASE WHEN nullif({w}_clean, '') IS NOT NULL AND "
                                   f"nullif({w}_master_clean, '') IS NOT NULL THEN "
                                   f"{_jw(fn, f'{w}_clean', f'{w}_master_clean')} END")
            continue
        left = f"{field}_clean"
        right = "nama_master_clean" if field == "nama" else f"{field}_master_clean"
        if field == "tanggal_lahir" and date_match == "exact":
            terms[f"t_{field}"] = (f"CASE WHEN CAST({left} AS DATE) = CAST({right} AS DATE) "
                                   f"THEN CAST(1.0 AS DOUBLE) ELSE CAST(0.0 AS DOUBLE) END")
        elif field == "tanggal_lahir":
            terms[f"t_{field}"] = _jw(fn, f"CAST({left} AS VARCHAR)", f"CAST({right} AS VARCHAR)")
        else:
            terms[f"t_{field}"] = _jw(fn, left, right)
    return terms


def _score_expr(weights: list) -> str:
    parts = []
    for field, share in weights:
        if not share:
            continue
        if field == "wilayah":
            values = [f"t_{w}" for w in REGIONS]
            total = " + ".join(f"COALESCE({v}, CAST(0.0 AS DOUBLE))" for v in values)
            count = " + ".join(f"(CASE WHEN {v} IS NULL THEN 0 ELSE 1 END)" for v in values)
            term = (f"COALESCE(({total}) / CAST(nullif({count}, 0) AS DOUBLE), "
                    f"CAST(0.0 AS DOUBLE))")
        else:
            term = f"t_{field}"
        parts.append(f"{term} * {_double(float(share) / 100)}")
    return f"({' + '.join(parts)}) * CAST(100 AS DOUBLE)" if parts else "CAST(0.0 AS DOUBLE)"


def _classify(rules_: dict) -> str:
    amax, rcnt = rules_["auto_missing_max"], rules_["review_missing_count"]
    auto_n = "TRUE" if amax is None else f"miss <= {int(amax)}"
    review_n = "TRUE" if rcnt is None else f"miss = {int(rcnt)}"
    return (f"CASE WHEN ({auto_n}) AND skor >= {_double(rules_['auto_score_min'])} THEN 1 "
            f"WHEN ({review_n}) AND skor >= {_double(rules_['review_score_min'])} "
            f"AND skor < {_double(rules_['review_score_max'])} THEN 2 ELSE 3 END")


MAX_PASS3_PARTS = 64


def _sr_branches(w: Work, blocking: dict, file_id: str, master_id: str, nv: str, mv: str,
                 select: str, part: tuple[int, int] | None = None) -> list[str]:
    where = (f"file_id = {sq(file_id)} AND row_id NOT IN (SELECT id FROM {w.t('p1')}) "
             f"AND row_id NOT IN (SELECT id FROM {w.t('dec')})")
    if part:
        index, parts = part
        where += f" AND (murmur_hash3_32(row_id) % {parts} + {parts}) % {parts} = {index}"
    remaining = f"(SELECT * FROM {KL}.records WHERE {where})"
    persons = f"(SELECT * FROM {MASTER}.persons WHERE master_id = {sq(master_id)})"
    join = "LEFT JOIN" if blocking.get("join") == "left" else "JOIN"
    return [f"SELECT {select} FROM {remaining} i {join} {persons} m ON "
            f"{cond.replace('{name}', nv).replace('{mother}', mv)}"
            for cond in blocking["branches"]]


def _pass3_parts(w: Work, blocking: dict, file_id: str, master_id: str, nv: str, mv: str,
                 timings: dict) -> tuple[int, int | None]:
    """Parts for Pass 3, split by incoming row: ranking is per row, so the result is the same."""
    if cfg.SR_PASS3_TARGET_PAIRS <= 0:
        return 1, None
    if int((master.status(master_id) or {}).get("row_count") or 0) < cfg.SR_PASS3_MIN_MASTER_ROWS:
        return 1, None
    t = time.perf_counter()
    union = " UNION ALL ".join(_sr_branches(w, blocking, file_id, master_id, nv, mv, "i.row_id"))
    pairs = int(sr.scalar(f"SELECT count(*) FROM ({union}) u") or 0)
    timings["pairCountMs"] = int((time.perf_counter() - t) * 1000)
    if pairs <= cfg.SR_PASS3_SPLIT_ABOVE_PAIRS:
        return 1, pairs
    return min(MAX_PASS3_PARTS, max(1, -(-pairs // cfg.SR_PASS3_TARGET_PAIRS))), pairs


def blocking_sr_sql(w: Work, blocking: dict, file_id: str, master_id: str, nv: str, mv: str,
                    part: tuple[int, int] | None = None) -> str:
    master_cols = ", ".join(f"m.{c.format(nv=nv, mv=mv)} AS {alias}"
                            for alias, c in MASTER_SIDE.items())
    union = " UNION ALL ".join(_sr_branches(w, blocking, file_id, master_id, nv, mv,
                                            f"i.row_id AS incoming_row_id, {master_cols}", part))
    core = f"SELECT DISTINCT * FROM ({union}) u" if blocking.get("distinct") else union
    incoming_cols = ", ".join(f"i.{c.format(nv=nv, mv=mv)} AS {alias}"
                              for alias, c in INCOMING_SIDE.items())
    return (f"SELECT b.*, {incoming_cols} FROM ({core}) b "
            f"JOIN (SELECT * FROM {KL}.records WHERE file_id = {sq(file_id)}) i "
            f"ON i.row_id = b.incoming_row_id")


def pass3_starrocks(w: Work, fn: dict, rules_: dict, file_id: str, master_id: str, nv: str,
                    mv: str, timings: dict) -> dict:
    terms = _term_columns(fn, rules_["weights"], rules_["date_match"])
    term_select = "".join(f", {expr} AS {name}" for name, expr in terms.items())
    eps = _double(rules_["epsilon"])
    rnd = fn["round"]
    k_pivot = ", ".join(f"max(CASE WHEN kk = {k} THEN nik_master END) AS k{k}_nik, "
                        f"max(CASE WHEN kk = {k} THEN skor END) AS k{k}_skor"
                        for k in range(1, MAX_CANDIDATES + 1))
    name_jw = _jw(fn, "c.nama_clean", "c.nama_master_clean")
    pattern = f"""CASE WHEN NOT f.rejected AND (f.tie OR f.class = 2) THEN
            CASE WHEN c.nik_incoming = f.nik AND {name_jw} < {_double(NAME_TOTAL_MISMATCH)}
                     THEN 'NIK_CONFLICT'
                 WHEN c.nama_clean <> c.nama_master_clean AND c.nama_full = c.nama_master_full
                     THEN 'TITLE_DEGREE'
                 WHEN c.dob_i IS NOT NULL AND c.dob_m IS NOT NULL
                      AND CAST(c.dob_i AS DATE) <> CAST(c.dob_m AS DATE)
                      AND day(c.dob_i) = month(c.dob_m) AND month(c.dob_i) = day(c.dob_m)
                     THEN 'SWAPPED_DOB'
                 WHEN c.nama_clean <> c.nama_master_clean
                      AND {name_jw} >= {_double(SPELLING_THRESHOLD)} THEN 'SPELLING_NAME'
                 ELSE 'GENERAL_REVIEW' END END"""
    winner = "f.tie AND NOT f.rejected"
    candidates = ", ".join(f"CASE WHEN {winner} THEN f.k{k}_nik END AS k{k}_nik, "
                           f"CASE WHEN {winner} AND f.k{k}_nik IS NOT NULL "
                           f"THEN {rnd}(f.k{k}_skor, 2) END AS k{k}_score"
                           for k in range(1, MAX_CANDIDATES + 1))
    parts, pairs = _pass3_parts(w, rules_["blocking"], file_id, master_id, nv, mv, timings)
    t = time.perf_counter()
    for index in range(parts):
        table = w.t("p3") if index == 0 else w.t("p3_part")
        joined = blocking_sr_sql(w, rules_["blocking"], file_id, master_id, nv, mv,
                                 (index, parts) if parts > 1 else None)
        sr.execute(f"DROP TABLE IF EXISTS {table} FORCE")
        sr.execute(f"""
        CREATE TABLE {table} {PROPS} AS
        WITH joined AS ({joined}),
        terms AS (
            SELECT incoming_row_id AS id, nik_master, nik_incoming, nama_clean, nama_master_clean,
                   nama_full, nama_master_full, tanggal_lahir_clean AS dob_i,
                   tanggal_lahir_master_clean AS dob_m,
                   {missing_sql(rules_['missing_elements'])} AS miss_all{term_select}
              FROM joined),
        scored AS (
            SELECT *, CASE WHEN nik_master IS NULL THEN CAST(0.0 AS DOUBLE)
                           ELSE {_score_expr(rules_['weights'])} END AS skor,
                      CASE WHEN nik_master IS NULL THEN 0 ELSE miss_all END AS miss
              FROM terms),
        ranked AS (
            SELECT id, nik_master, skor, miss,
                   row_number() OVER (PARTITION BY id ORDER BY skor DESC,
                                      nik_master ASC NULLS LAST) AS rn,
                   count(nik_master) OVER (PARTITION BY id) AS n_candidates
              FROM scored),
        uniq AS (
            SELECT * FROM (
                SELECT r.*, row_number() OVER (PARTITION BY id, nik_master ORDER BY rn) AS k
                  FROM ranked r WHERE rn <= {2 * MAX_CANDIDATES}) x
             WHERE k = 1),
        head AS (SELECT id, nik_master AS nik, skor, miss, n_candidates FROM uniq WHERE rn = 1),
        tied AS (
            SELECT u.id, u.nik_master, u.skor,
                   row_number() OVER (PARTITION BY u.id ORDER BY u.rn) AS kk
              FROM uniq u JOIN head h ON h.id = u.id
             WHERE u.nik_master IS NOT NULL AND h.skor - u.skor <= {eps}),
        tie_stats AS (SELECT id, count(*) AS n_tie, {k_pivot} FROM tied GROUP BY id),
        decided AS (
            SELECT h.*, COALESCE(s.n_tie, 0) AS n_tie, COALESCE(s.n_tie, 0) > 1 AS tie,
                   {', '.join(f's.k{k}_nik, s.k{k}_skor' for k in range(1, MAX_CANDIDATES + 1))},
                   CASE WHEN h.nik IS NULL THEN 3 ELSE {_classify(rules_)} END AS class
              FROM head h LEFT JOIN tie_stats s ON s.id = h.id),
        flagged AS (SELECT d.*, (d.nik IS NULL OR d.class = 3) AS rejected FROM decided d),
        picked AS (
            SELECT * FROM (
                SELECT s.id, s.nik_incoming, s.nama_clean, s.nama_master_clean, s.nama_full,
                       s.nama_master_full, s.dob_i, s.dob_m,
                       row_number() OVER (PARTITION BY s.id ORDER BY
                           s.nama_master_clean ASC NULLS LAST, s.nama_master_full ASC NULLS LAST,
                           s.dob_m ASC NULLS LAST) AS pick
                  FROM scored s JOIN flagged f ON f.id = s.id AND s.nik_master = f.nik
                 WHERE NOT f.rejected) x
             WHERE pick = 1)
        SELECT f.id, CASE WHEN f.rejected THEN NULL ELSE f.nik END AS master_nik,
               {rnd}(f.skor, 2) AS score,
               CASE WHEN f.rejected THEN 'UNMATCH' WHEN f.tie THEN 'CONFLICT'
                    WHEN f.class = 1 THEN 'AUTO' ELSE 'REVIEW' END AS status,
               'SCORING' AS method, ({winner}) AS rank_conflict,
               CAST(f.n_candidates AS BIGINT) AS n_candidates, FALSE AS owned,
               CAST(CASE WHEN {winner} THEN f.n_tie END AS INT) AS n_tie,
               {pattern} AS pattern_group,
               CASE WHEN NOT f.rejected
                    THEN {rnd}({name_jw} * CAST(100 AS DOUBLE), 1) END AS jw_name,
               {candidates}
          FROM flagged f LEFT JOIN picked c ON c.id = f.id""")
        if index:
            sr.execute(f"INSERT INTO {w.t('p3')} SELECT * FROM {table}")
    if parts > 1:
        sr.execute(f"DROP TABLE IF EXISTS {w.t('p3_part')} FORCE")
    timings["blockingMs"] = int((time.perf_counter() - t) * 1000)
    timings["candidatePullMs"] = 0
    return {"engine": "starrocks", "parts": parts, "pairs": pairs,
            "rows": sr.scalar(f"SELECT count(*) FROM {w.t('p3')}")}


def _load_decisions(w: Work, con, table: str, candidates: str) -> None:
    if con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:
        select = ", ".join(f"CAST({c} AS INTEGER)" if c in ("rank_conflict", "owned") else c
                           for c in DECISION_COLUMNS)
        sr.stream_load_query(con, f"SELECT {select} FROM {table}", SVC, w.name("dec"),
                             DECISION_COLUMNS)
    if con.execute(f"SELECT count(*) FROM {candidates}").fetchone()[0]:
        sr.stream_load_query(con, f"SELECT id, rk, nik, score FROM {candidates}", SVC,
                             w.name("cand"), CANDIDATE_COLUMNS)


def _records(file_id: str) -> str:
    return f"(SELECT * FROM {KL}.records WHERE file_id = {sq(file_id)})"


def decisions_cte(w: Work, file_id: str, p3: bool = False) -> str:
    p1 = (f"SELECT id, nik, CAST(100.0 AS DOUBLE), "
          f"CASE WHEN n_candidates > 1 THEN 'CONFLICT' ELSE 'AUTO' END, 'PASS1_NIK_NAMA', "
          f"n_candidates > 1, CAST(n_candidates AS BIGINT), FALSE, "
          f"CAST(CASE WHEN n_candidates > 1 THEN n_candidates END AS INT), "
          f"CASE WHEN n_candidates > 1 THEN 'GENERAL_REVIEW' END, CAST(100.0 AS DOUBLE) "
          f"FROM {w.t('p1')}")
    unmatched = (f"SELECT row_id, CAST(NULL AS VARCHAR), CAST(0.0 AS DOUBLE), 'UNMATCH', 'SCORING', "
                 f"FALSE, CAST(0 AS BIGINT), FALSE, CAST(NULL AS INT), CAST(NULL AS VARCHAR), "
                 f"CAST(NULL AS DOUBLE) FROM {_records(file_id)} r WHERE r.row_id NOT IN "
                 f"(SELECT id FROM {w.t('dec')} UNION ALL SELECT id FROM {w.t('p1')}"
                 + (f" UNION ALL SELECT id FROM {w.t('p3')}" if p3 else "") + ")")
    scored = f" UNION ALL SELECT {', '.join(DECISION_COLUMNS)} FROM {w.t('p3')}" if p3 else ""
    return (f"dec AS (SELECT {', '.join(DECISION_COLUMNS)} FROM {w.t('dec')} "
            f"UNION ALL {p1}{scored} UNION ALL {unmatched})")


def result_ctes(w: Work, job: dict, master_id: str, flags: dict) -> str:
    file_id, nv, mv = job["file_id"], job["_nv"], job["_mv"]
    p1_candidates = " UNION ALL ".join(
        f"SELECT id, CAST({k} AS INT), candidates[{k}], CAST(100.0 AS DOUBLE) FROM {w.t('p1')} "
        f"WHERE n_candidates > 1 AND array_length(candidates) >= {k}"
        for k in range(1, MAX_CANDIDATES + 1))
    if job.get("_p3_sr"):
        p1_candidates += "".join(
            f" UNION ALL SELECT id, CAST({k} AS INT), k{k}_nik, k{k}_score FROM {w.t('p3')} "
            f"WHERE k{k}_nik IS NOT NULL" for k in range(1, MAX_CANDIDATES + 1))
    pivot = ", ".join(
        f"max(CASE WHEN c.rk = {k} THEN c.nik END) AS k{k}_nik, "
        f"max(CASE WHEN c.rk = {k} THEN m.nama_lengkap END) AS k{k}_name, "
        f"max(CASE WHEN c.rk = {k} THEN m.tempat_lahir END) AS k{k}_pob, "
        f"max(CASE WHEN c.rk = {k} THEN m.tanggal_lahir END) AS k{k}_dob, "
        f"max(CASE WHEN c.rk = {k} THEN c.score END) AS k{k}_score"
        for k in range(1, MAX_CANDIDATES + 1))
    return f"""WITH {decisions_cte(w, file_id, bool(job.get("_p3_sr")))},
        cand AS (SELECT id, rk, nik, score FROM {w.t('cand')} UNION ALL {p1_candidates}),
        mp AS (
            SELECT * FROM (
                SELECT m.nik, m.nama_lengkap, m.tanggal_lahir, m.jenis_kelamin, m.nama_ibu,
                       m.tempat_lahir, m.provinsi, m.{nv} AS name_c, m.name_v3 AS name_full,
                       m.{mv} AS mother_c, m.pob_c,
                       row_number() OVER (PARTITION BY m.nik ORDER BY m.name_v0, m.mother_v0,
                                          m.pob_c, m.tanggal_lahir) AS rn
                  FROM {MASTER}.persons m
                 WHERE m.master_id = {sq(master_id)}
                   AND m.nik IN (SELECT master_nik FROM dec WHERE master_nik IS NOT NULL
                                 UNION SELECT nik FROM cand)) t
             WHERE rn = 1),
        kand AS (SELECT c.id, {pivot} FROM cand c LEFT JOIN mp m ON m.nik = c.nik GROUP BY c.id),
        pairs AS (
            SELECT d.*, i.nik AS i_nik, i.nik_trusted AS i_nik_trusted, i.nama AS i_nama,
                   i.tanggal_lahir AS i_tgl_raw, i.dob AS i_dob, i.jenis_kelamin AS i_jk,
                   i.nama_ibu AS i_ibu, i.tempat_lahir AS i_tmp, i.provinsi AS i_provinsi,
                   i.{nv} AS i_name_c, i.name_v3 AS i_name_full, i.{mv} AS i_mother_c,
                   i.pob_c AS i_pob_c,
                   m.nik AS m_nik, m.nama_lengkap AS m_nama, m.tanggal_lahir AS m_tgl,
                   m.jenis_kelamin AS m_jk, m.nama_ibu AS m_ibu, m.tempat_lahir AS m_tmp,
                   m.provinsi AS m_provinsi, m.name_c AS m_name_c, m.name_full AS m_name_full,
                   m.mother_c AS m_mother_c, m.pob_c AS m_pob_c,
                   {', '.join(f'k.k{k}_nik, k.k{k}_name, k.k{k}_pob, k.k{k}_dob, k.k{k}_score'
                              for k in range(1, MAX_CANDIDATES + 1))},
                   (o.id IS NOT NULL) AS nik_in_master
              FROM dec d
              JOIN {_records(file_id)} i ON i.row_id = d.id
              LEFT JOIN mp m ON m.nik = d.master_nik
              LEFT JOIN kand k ON k.id = d.id
              LEFT JOIN (SELECT DISTINCT id FROM {w.t('nik')}) o ON o.id = d.id),
        signed AS (SELECT *, {reasoning.signature_sql(flags)} AS signature FROM pairs)"""


def present_flags(file_id: str) -> dict:
    row = sr.query(f"SELECT {reasoning.present_sql()} FROM (SELECT nik AS i_nik, nama AS i_nama, "
                   f"tanggal_lahir AS i_tgl_raw, jenis_kelamin AS i_jk, nama_ibu AS i_ibu, "
                   f"tempat_lahir AS i_tmp FROM {_records(file_id)} r) t")[0]
    return {k: bool(v) for k, v in row.items()}


def decision_metrics(w: Work, file_id: str, n: int, p3: bool = False) -> dict:
    r = sr.query(f"""
        WITH {decisions_cte(w, file_id, p3)}
        SELECT count(*) AS total, count(DISTINCT id) AS ids, sum(n_candidates) AS candidates,
               sum(CASE WHEN method = 'PASS1_NIK_NAMA' THEN 1 ELSE 0 END) AS p1,
               sum(CASE WHEN method = 'PASS2_NAMA_TGL_IBU' THEN 1 ELSE 0 END) AS p2,
               sum(CASE WHEN method = 'SCORING' THEN 1 ELSE 0 END) AS p3,
               sum(CASE WHEN status = 'AUTO' THEN 1 ELSE 0 END) AS auto,
               sum(CASE WHEN status = 'REVIEW' THEN 1 ELSE 0 END) AS review,
               sum(CASE WHEN status = 'UNMATCH' THEN 1 ELSE 0 END) AS unmatch,
               sum(CASE WHEN status = 'CONFLICT' THEN 1 ELSE 0 END) AS conflict
          FROM dec""")[0]
    total, candidates = int(r["total"]), int(r["candidates"] or 0)
    if total != n or int(r["ids"]) != n:
        raise RuntimeError(f"Hasil memuat {total:,} baris untuk {n:,} baris incoming. "
                           f"Setiap baris incoming harus punya tepat satu baris hasil.")
    return {"totalIncoming": total, "totalCandidates": candidates,
            "avgCandidatesPerRow": round(candidates / total, 2) if total else 0.0,
            "pass1Count": int(r["p1"]), "pass2Count": int(r["p2"]), "scoringCount": int(r["p3"]),
            "autoCount": int(r["auto"]), "reviewCount": int(r["review"]),
            "unmatchCount": int(r["unmatch"]), "conflictCount": int(r["conflict"])}


def assemble(w: Work, job: dict, master_id: str, n: int) -> tuple[int, dict]:
    file_id = job["file_id"]
    metrics = decision_metrics(w, file_id, n, bool(job.get("_p3_sr")))
    ctes = result_ctes(w, job, master_id, present_flags(file_id))
    patterns = [(r["signature"], int(r["n"]), r["sample"]) for r in sr.query(
        f"{ctes} SELECT signature, count(*) AS n, min(id) AS sample FROM signed "
        f"WHERE signature IS NOT NULL GROUP BY signature ORDER BY n DESC, signature")]
    base = {sig: reasoning.template(sig) for sig, _, _ in patterns}
    templates = dict(base)
    t = time.perf_counter()
    stats = {}
    if cfg.REASONING_AI_BASE_URL:
        try:
            stats = dict(reasoning.refine(job, patterns, base, templates))
        except Exception as e:  # noqa: BLE001
            templates = dict(base)
            print(f"[R] {job['job_id']} LLM refinement failed, deterministic sentences kept: "
                  f"{type(e).__name__}: {e}", flush=True)
    job["_reasoning"] = {"rows": sum(n for _, n, _ in patterns), "patterns": len(patterns),
                         **stats, "ms": int((time.perf_counter() - t) * 1000)}
    print(f"[M] {job['job_id']} reasoning: {job['_reasoning']}", flush=True)
    actor = sq(job["actor"])
    incoming_snapshot = (
        "json_object('nama', i_nama, 'nik', i_nik, 'tanggal_lahir', "
        "COALESCE(date_format(i_dob, '%Y-%m-%d'), i_tgl_raw), 'jenis_kelamin', i_jk, "
        "'nama_ibu', i_ibu, 'tempat_lahir', i_tmp, 'provinsi', i_provinsi)")
    master_snapshot = (
        "CASE WHEN master_nik IS NULL THEN NULL ELSE json_object('nama_lengkap', m_nama, "
        "'nik', m_nik, 'tanggal_lahir', date_format(m_tgl, '%Y-%m-%d'), 'jenis_kelamin', m_jk, "
        "'nama_ibu', m_ibu, 'tempat_lahir', m_tmp, 'provinsi', m_provinsi) END")
    previous = (f"csv_file_id = {sq(file_id)} AND master_file_id = {sq(job['master_file_id'])}")
    if sr.query(f"SELECT 1 FROM {PORTAL}.matching_results WHERE {previous} LIMIT 1"):
        sr.execute(f"DELETE FROM {PORTAL}.matching_results WHERE {previous}")
    sr.execute(f"""
        INSERT INTO {PORTAL}.matching_results
        {ctes}
        SELECT uuid(), {sq(file_id)}, {sq(job['master_file_id'])}, {sq(job['job_id'])}, id,
               master_nik, score, status, method, rank_conflict, pattern_group,
               {reasoning.reasoning_sql(templates)}, {incoming_snapshot}, {master_snapshot},
               NULL, now(), now(), {actor}, {actor}
          FROM signed""")
    return len(patterns), metrics


def run(job: dict, report=lambda stage: None) -> dict:
    timings: dict[str, int] = {}
    started = time.perf_counter()

    def clock(name: str, t0: float) -> None:
        timings[name] = int((time.perf_counter() - t0) * 1000)

    w = Work(job["job_id"])
    con = duck.connect(s3=True)
    grading.apply_s3_endpoint(con, job)
    sr.attach(con)
    try:
        t = time.perf_counter()
        status = portal_status(job["job_id"])
        if status is None:
            register(job)
        elif status == "CANCELLED":
            raise Cancelled(job["job_id"])
        mark(job["job_id"], status="IN_PROGRESS", current_stage="BLOCKING", started_at="now()")
        grade = find_grade(job)
        rules_ = rules.matching_rules(grade)
        version = rules.record_version()
        applied = rules.rules_summary(grade, rules_, version)
        variant = names.variant_for(rules_["name_cleaning"])
        nv, mv = f"name_{variant}", f"mother_{variant}"
        job["_nv"], job["_mv"] = nv, mv
        master_id = ensure_master(job, report, variant)
        n = ensure_incoming(job, con, variant)
        w.drop()
        _create(w.t("dec"), DECISION_DDL)
        _create(w.t("cand"), CANDIDATE_DDL)
        clock("prepMs", t)
        print(f"[M] {job['job_id']} {n:,} incoming rows, grade {grade}, master {master_id}, "
              f"config {version}", flush=True)

        check_cancelled(job)
        mark(job["job_id"], current_stage="DETERMINISTIC")
        report("M1 pass 1")
        t = time.perf_counter()
        p1 = pass1(w, con, job["file_id"], master_id, nv, mv, rules_["contradiction_jw"])
        clock("pass1Ms", t)

        report("M2 pass 2")
        t = time.perf_counter()
        pass2(w, con, job["file_id"], master_id, nv, mv)
        _load_decisions(w, con, "p2_decisions", "p2_candidates")
        clock("pass2Ms", t)

        check_cancelled(job)
        mark(job["job_id"], current_stage="SCORING")
        report("M3 pass 3")
        fn = udf.ensure()
        job["_p3_sr"] = bool(fn)
        if fn:
            p3 = pass3_starrocks(w, fn, rules_, job["file_id"], master_id, nv, mv, timings)
        else:
            p3 = pass3(w, con, rules_, job["file_id"], master_id, nv, mv, timings)
            t = time.perf_counter()
            _load_decisions(w, con, "p3_decisions", "p3_candidates")
            clock("decisionLoadMs", t)

        check_cancelled(job)
        mark(job["job_id"], current_stage="CLASSIFYING")
        report("M4 assemble results in StarRocks")
        t = time.perf_counter()
        signatures, m = assemble(w, job, master_id, n)
        clock("classificationMs", t)
        timings["totalMs"] = int((time.perf_counter() - started) * 1000)
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
        timings["reasoningMs"] = job["_reasoning"]["ms"]
        detail = {**applied, "pass1": p1, "pass3": p3, "reasoningPatterns": signatures,
                  "reasoning": job["_reasoning"]}
        mark(job["job_id"], status="COMPLETED", current_stage="COMPLETED", completed_at="now()",
             last_error=None, total_incoming=m["totalIncoming"],
             total_candidates=m["totalCandidates"], avg_candidates_per_row=m["avgCandidatesPerRow"],
             pass1_count=m["pass1Count"], pass2_count=m["pass2Count"],
             scoring_count=m["scoringCount"], auto_count=m["autoCount"],
             review_count=m["reviewCount"], unmatch_count=m["unmatchCount"],
             conflict_count=m["conflictCount"], stage_durations=timings,
             blocking_metrics=detail, peak_rss_mb=rss,
             result_parquet_key=f"starrocks:{PORTAL}.matching_results")
    finally:
        try:
            w.drop()
        except Exception as e:  # noqa: BLE001
            print(f"[M] work tables not dropped: {e}")
        con.close()

    m["stageDurations"] = timings
    m["peakRssMb"] = rss
    m["configVersion"] = version
    m["rulesApplied"] = applied
    body = {"jobId": job["job_id"], "fileId": job["file_id"],
            "masterFileId": job["master_file_id"], "status": "COMPLETED",
            "resultParquetKey": None, "resultTable": f"{PORTAL}.matching_results", "metrics": m,
            "message": "Pencocokan data selesai; hasil tersimpan di StarRocks."}
    ok, detail_text, _ = post_json(job["callback_url"], body, {"x-callback-source": "matching-engine"})
    print(f"[M] {job['job_id']} done in {timings['totalMs']:,} ms — AUTO {m['autoCount']:,} "
          f"REVIEW {m['reviewCount']:,} UNMATCH {m['unmatchCount']:,} "
          f"CONFLICT {m['conflictCount']:,}; callback {detail_text}", flush=True)
    return body


def fail(job: dict, error: str) -> None:
    try:
        mark(job["job_id"], status="FAILED", current_stage="FAILED", failed_at="now()",
             last_error=error[:2000])
    except Exception as e:  # noqa: BLE001
        print(f"[M] {job.get('job_id')} could not mark FAILED: {e}")
    if job.get("callback_url"):
        post_json(job["callback_url"], {
            "jobId": job.get("job_id"), "fileId": job.get("file_id"),
            "masterFileId": job.get("master_file_id"), "status": "FAILED",
            "error": error[:2000], "message": "Proses matching gagal dieksekusi."},
            {"x-callback-source": "matching-engine"})
