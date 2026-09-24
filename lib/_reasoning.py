"""
AI Reasoning Engine (DuckDB-Native).

Generates concise explanations for records flagged with match_result = 2 (MANUAL_REVIEW).
Executes a three-step DuckDB-native workflow:
  1. SQL Verdict: Pre-computes 5-field comparison verdicts and signatures for all pending rows.
  2. Pattern Resolution: Fetches unique signatures; invokes local LLM strictly for uncached patterns.
  3. Bulk Template Application: Performs bulk SQL update to write back to manual_matches.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

from _reasoning_jobs import execute_pg, heartbeat, q
from _shared import buka_koneksi

# ── Configuration ─────────────────────────────────────────────────────────────

REASONING_AI_BASE_URL = (
    os.getenv("REASONING_AI_BASE_URL", "").strip().strip('"')
    or os.getenv("OLLAMA_LOCAL_BASE_URL", "").strip().strip('"')
    or os.getenv("NORMALISASI_AI_BASE_URL", "").strip().strip('"')
)

REASONING_AI_MODEL = (
    os.getenv("REASONING_AI_MODEL", "").strip()
    or os.getenv("NORMALISASI_AI_MODEL", "gemma4:31b").strip()
)

REASONING_AI_API_KEY = (
    os.getenv("REASONING_AI_API_KEY", "").strip()
    or os.getenv("OLLAMA_API_KEY", "ollama").strip()
)

REASONING_AI_TIMEOUT = int(os.getenv("REASONING_AI_TIMEOUT", "60"))
REASONING_AI_RETRIES = int(os.getenv("REASONING_AI_RETRIES", "3"))
REASONING_AI_RETRY_DELAY = float(os.getenv("REASONING_AI_RETRY_DELAY", "1.5"))

SYSTEM_PROMPT = """You are an AI tasked with explaining why a pair of identity records was flagged for manual review.
Your goal is a very short, concise explanation in English that a manual reviewer can read at a glance.

You will be given two things:
1. The field values for "Institution" (the incoming record) and "Master" (the reference registry).
2. A FIELD COMPARISON block: a pre-computed, authoritative verdict for each field.

## THE FIELD COMPARISON BLOCK IS THE GROUND TRUTH

Each field carries one verdict:
- `SAME`                  -> the two values are equivalent
- `DIFFERENT`             -> both sides have a value, and they differ
- `EMPTY_IN_INSTITUTION`  -> the Institution side has no value
- `EMPTY_IN_MASTER`       -> the Master side has no value

STRICT RULES:
- Describe ONLY fields whose verdict is NOT `SAME`.
- NEVER claim a field is different when its verdict is `SAME`. This is the most important rule.
  Do not describe a field as differing just because the two spellings look unusual to you. Trust the verdict, not your own comparison.
- Do not invent, guess, or mention any field that is not listed in FIELD COMPARISON.
- If EVERY field's verdict is `SAME`, respond with exactly this sentence and nothing else:
  "All compared fields match, but flagged for manual review due to a borderline similarity score."

## HOW TO WRITE IT
- MUST be in English.
- No introductory phrase. Start immediately with the first difference.
- For a `DIFFERENT` verdict, you MUST label both sides and separate them with "vs", e.g.
  "full name is different (Institution: Budianto Sudarsono vs Master: Budi Sudarsono)".
- For an `EMPTY_IN_INSTITUTION` verdict, write that the field "is empty in institution".
  For `EMPTY_IN_MASTER`, write that it "is empty in master". Do not use "vs" for these.
- NEVER abbreviate or truncate names and places. Copy the values EXACTLY as given.
- Mention every non-`SAME` field. Do not skip any.
- Output ONLY the plain text sentence. Do NOT enclose your output in quotation marks or brackets.

Good example (verdicts: nama_lengkap=DIFFERENT, tanggal_lahir=EMPTY_IN_INSTITUTION, nama_ibu=DIFFERENT, rest SAME):
Full name is different (Institution: Budianto Sudarsono vs Master: Budi Sudarsono), date of birth is empty in institution, and mother's name is different (Institution: Siti Aminah vs Master: Suti Aminah).
"""

TEMPLATE_FIELDS = [
    ("nama_lengkap", "nama_incoming", "nama_master"),
    ("tempat_lahir", "tempat_lahir_incoming", "tempat_lahir_master"),
    ("tanggal_lahir", "tanggal_lahir_incoming", "tanggal_lahir_master"),
    ("jenis_kelamin", "jenis_kelamin_incoming", "jenis_kelamin_master"),
    ("nama_ibu", "nama_ibu_incoming", "nama_ibu_master"),
]


# ── Step 1: SQL Verdict ───────────────────────────────────────────────────────

def build_verdict_table(con, file_id: str) -> int:
    """
    Compare all 5 identity fields in a single SQL statement.
    Stores the precomputed verdicts and signatures in TEMP TABLE reasoning_verdict.
    Returns the count of rows queued for reasoning.
    """
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE reasoning_verdict AS
        WITH raw_joined AS (
            SELECT
                mm.file_id,
                mm.id_incoming,
                TRIM(COALESCE(mm.nama_incoming, '')) AS nama_incoming,
                TRIM(COALESCE(mm.tempat_lahir_incoming, '')) AS tempat_lahir_incoming,
                TRIM(COALESCE(mm.tanggal_lahir_incoming, '')) AS tanggal_lahir_incoming,
                TRIM(COALESCE(mm.jenis_kelamin_incoming, '')) AS jenis_kelamin_incoming,
                TRIM(COALESCE(mm.nama_ibu_incoming, '')) AS nama_ibu_incoming,
                TRIM(COALESCE(m.nama_lengkap, '')) AS nama_master,
                TRIM(COALESCE(m.tempat_lahir, '')) AS tempat_lahir_master,
                TRIM(COALESCE(CAST(m.tanggal_lahir AS VARCHAR), '')) AS tanggal_lahir_master,
                TRIM(COALESCE(m.jenis_kelamin, '')) AS jenis_kelamin_master,
                TRIM(COALESCE(m.nama_ibu, '')) AS nama_ibu_master
            FROM pg.public.manual_matches mm
            JOIN pg.public.institution i USING (file_id, id_incoming)
            LEFT JOIN pg.public.master m ON m.nik = i.nik_master
            WHERE mm.file_id = {q(file_id)}
              AND mm.reasoning_status IN ('PENDING', 'FAILED')
        ),
        normalized AS (
            SELECT
                *,
                CASE
                    WHEN LOWER(jenis_kelamin_incoming) IN ('l', 'laki-laki', 'pria', 'laki laki', '1') THEN 'L'
                    WHEN LOWER(jenis_kelamin_incoming) IN ('p', 'perempuan', 'wanita', '2') THEN 'P'
                    ELSE UPPER(jenis_kelamin_incoming)
                END AS norm_gender_incoming,
                CASE
                    WHEN LOWER(jenis_kelamin_master) IN ('l', 'laki-laki', 'pria', 'laki laki', '1') THEN 'L'
                    WHEN LOWER(jenis_kelamin_master) IN ('p', 'perempuan', 'wanita', '2') THEN 'P'
                    ELSE UPPER(jenis_kelamin_master)
                END AS norm_gender_master
            FROM raw_joined
        ),
        verdicts AS (
            SELECT
                *,
                -- 1. nama_lengkap
                CASE
                    WHEN LOWER(nama_incoming) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_INSTITUTION'
                    WHEN LOWER(nama_master) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_MASTER'
                    WHEN LOWER(nama_incoming) = LOWER(nama_master) THEN 'SAME'
                    ELSE 'DIFFERENT'
                END AS v_nama,
                -- 2. tempat_lahir
                CASE
                    WHEN LOWER(tempat_lahir_incoming) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_INSTITUTION'
                    WHEN LOWER(tempat_lahir_master) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_MASTER'
                    WHEN LOWER(tempat_lahir_incoming) = LOWER(tempat_lahir_master) THEN 'SAME'
                    ELSE 'DIFFERENT'
                END AS v_tempat_lahir,
                -- 3. tanggal_lahir
                CASE
                    WHEN LOWER(tanggal_lahir_incoming) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_INSTITUTION'
                    WHEN LOWER(tanggal_lahir_master) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_MASTER'
                    WHEN tanggal_lahir_incoming = tanggal_lahir_master THEN 'SAME'
                    ELSE 'DIFFERENT'
                END AS v_tanggal_lahir,
                -- 4. jenis_kelamin
                CASE
                    WHEN LOWER(jenis_kelamin_incoming) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_INSTITUTION'
                    WHEN LOWER(jenis_kelamin_master) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_MASTER'
                    WHEN norm_gender_incoming = norm_gender_master THEN 'SAME'
                    ELSE 'DIFFERENT'
                END AS v_jenis_kelamin,
                -- 5. nama_ibu
                CASE
                    WHEN LOWER(nama_ibu_incoming) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_INSTITUTION'
                    WHEN LOWER(nama_ibu_master) IN ('', 'null', 'none', 'nan', '-', 'kosong') THEN 'EMPTY_IN_MASTER'
                    WHEN LOWER(nama_ibu_incoming) = LOWER(nama_ibu_master) THEN 'SAME'
                    ELSE 'DIFFERENT'
                END AS v_nama_ibu
            FROM normalized
        )
        SELECT
            *,
            CONCAT(
                'jenis_kelamin:', v_jenis_kelamin, '|',
                'nama_ibu:', v_nama_ibu, '|',
                'nama_lengkap:', v_nama, '|',
                'tanggal_lahir:', v_tanggal_lahir, '|',
                'tempat_lahir:', v_tempat_lahir
            ) AS pattern_signature,
            MD5(CONCAT(
                'jenis_kelamin:', v_jenis_kelamin, '|',
                'nama_ibu:', v_nama_ibu, '|',
                'nama_lengkap:', v_nama, '|',
                'tanggal_lahir:', v_tanggal_lahir, '|',
                'tempat_lahir:', v_tempat_lahir
            )) AS pattern_hash
        FROM verdicts;
    """)

    row_count = con.execute("SELECT COUNT(*) FROM reasoning_verdict").fetchone()[0]
    return row_count


DISALLOWED_PUBLIC_AI_HOSTS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "api.together.xyz",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.cohere.com",
    "ollama.com",
}


def validate_onprem_endpoint(url: str) -> None:
    """
    Ensure the endpoint is dedicated on-premise infrastructure.
    Strictly forbids public third-party commercial cloud AI providers.
    """
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].lower()
    for banned in DISALLOWED_PUBLIC_AI_HOSTS:
        if host == banned or host.endswith("." + banned):
            raise PermissionError(
                f"Public cloud commercial AI endpoint '{host}' is strictly forbidden. "
                "AI Reasoning with PII data must exclusively use on-premise LLM infrastructure."
            )


def call_local_llm(prompt: str, model: str | None = None) -> str:
    """
    Invoke on-premise LLM endpoint (Ollama / vLLM OpenAI-compatible).
    Includes automatic retries and enforces on-premise infrastructure constraints.
    """
    base_url = REASONING_AI_BASE_URL.rstrip("/")
    if not base_url:
        raise RuntimeError(
            "On-premise LLM endpoint not configured. Set REASONING_AI_BASE_URL or OLLAMA_LOCAL_BASE_URL."
        )

    validate_onprem_endpoint(base_url)

    model_name = model or REASONING_AI_MODEL
    if base_url.endswith("/chat/completions"):
        url = base_url
    elif base_url.endswith("/v1") or base_url.endswith("/v1/"):
        url = f"{base_url.rstrip('/')}/chat/completions"
    else:
        url = f"{base_url}/v1/chat/completions"

    payload = json.dumps({
        "model": model_name,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")

    last_error = None
    for attempt in range(1, REASONING_AI_RETRIES + 1):
        current_api_key = os.getenv("REASONING_AI_API_KEY", "").strip() or REASONING_AI_API_KEY
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {current_api_key}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=REASONING_AI_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"].strip()
                while (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
                    content = content[1:-1].strip()
                return content
        except urllib.error.HTTPError as err:
            last_error = f"HTTP {err.code}: {err.read().decode(errors='replace')[:200]}"
            if 400 <= err.code < 500 and err.code != 429:
                break
        except Exception as err:
            last_error = f"{type(err).__name__}: {err}"

        if attempt < REASONING_AI_RETRIES:
            time.sleep(REASONING_AI_RETRY_DELAY * attempt)

    raise RuntimeError(f"Local LLM call failed after {REASONING_AI_RETRIES} attempts: {last_error}")


def construct_deterministic_fallback(sample_row: dict) -> str:
    """
    Constructs a deterministic grammatical explanation strictly matching prompt rules.
    Used when local LLM endpoint is unavailable during testing.
    """
    verdicts = {
        "full name": (sample_row.get("v_nama"), sample_row.get("nama_incoming"), sample_row.get("nama_master")),
        "place of birth": (sample_row.get("v_tempat_lahir"), sample_row.get("tempat_lahir_incoming"), sample_row.get("tempat_lahir_master")),
        "date of birth": (sample_row.get("v_tanggal_lahir"), sample_row.get("tanggal_lahir_incoming"), sample_row.get("tanggal_lahir_master")),
        "gender": (sample_row.get("v_jenis_kelamin"), sample_row.get("jenis_kelamin_incoming"), sample_row.get("jenis_kelamin_master")),
        "mother's name": (sample_row.get("v_nama_ibu"), sample_row.get("nama_ibu_incoming"), sample_row.get("nama_ibu_master")),
    }

    clauses = []
    for label, (v, inc, mst) in verdicts.items():
        if v == "SAME":
            continue
        if v == "EMPTY_IN_INSTITUTION":
            clauses.append(f"{label} is empty in institution")
        elif v == "EMPTY_IN_MASTER":
            clauses.append(f"{label} is empty in master")
        elif v == "DIFFERENT":
            clauses.append(f"{label} is different (Institution: {inc} vs Master: {mst})")

    if not clauses:
        return "All compared fields match, but flagged for manual review due to a borderline similarity score."

    if len(clauses) == 1:
        text = clauses[0]
    elif len(clauses) == 2:
        text = f"{clauses[0]}, and {clauses[1]}"
    else:
        text = ", ".join(clauses[:-1]) + f", and {clauses[-1]}"

    return text[0].upper() + text[1:] + "."


def sanitize_explanation(text: str) -> str:
    """
    Cleans up punctuation, removes semicolons, fixes comma splices,
    and ensures proper English sentence capitalization and termination.
    """
    text = text.strip()
    while (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    # 1. Eliminate any stray semicolons in ALL_SAME pattern
    text = text.replace(
        "All compared fields match; flagged for manual review due to a borderline similarity score.",
        "All compared fields match, but flagged for manual review due to a borderline similarity score.",
    )
    # Replace any stray semicolon with comma
    text = re.sub(r";\s*", ", ", text)

    # 2. Fix comma splice in two-clause sentence missing coordinating conjunction 'and'
    # e.g., "... vs Master: X), date of birth is different..." -> "... vs Master: X), and date of birth is different..."
    if " and " not in text:
        text = re.sub(
            r"\),\s*([a-zA-Z' ]+ is (?:different|empty))",
            r"), and \1",
            text,
            flags=re.IGNORECASE,
        )

    # 3. Clean multiple spaces if any
    text = re.sub(r"[ \t]+", " ", text).strip()

    # 4. Capitalization and trailing punctuation
    text = text.rstrip(".") + "."
    if text:
        text = text[0].upper() + text[1:]
    return text


def convert_explanation_to_template(explanation: str, sample_row: dict) -> str:
    """
    Converts literal sample values inside the LLM explanation into standardized placeholders.
    Ensures safe, collision-free replacements using word boundaries and index claiming.
    """
    explanation = sanitize_explanation(explanation)

    claims = []  # list of (start_idx, end_idx, placeholder)

    pairs = [
        (sample_row.get("nama_incoming"), "{incoming.nama_lengkap}"),
        (sample_row.get("nama_master"), "{master.nama_lengkap}"),
        (sample_row.get("tempat_lahir_incoming"), "{incoming.tempat_lahir}"),
        (sample_row.get("tempat_lahir_master"), "{master.tempat_lahir}"),
        (sample_row.get("tanggal_lahir_incoming"), "{incoming.tanggal_lahir}"),
        (sample_row.get("tanggal_lahir_master"), "{master.tanggal_lahir}"),
        (sample_row.get("jenis_kelamin_incoming"), "{incoming.jenis_kelamin}"),
        (sample_row.get("jenis_kelamin_master"), "{master.jenis_kelamin}"),
        (sample_row.get("nama_ibu_incoming"), "{incoming.nama_ibu}"),
        (sample_row.get("nama_ibu_master"), "{master.nama_ibu}"),
    ]

    valid_pairs = []
    for val, placeholder in pairs:
        if val is not None and str(val).strip() and str(val).strip().lower() not in {"null", "none", "nan", "-", "kosong"}:
            valid_pairs.append((str(val).strip(), placeholder))

    valid_pairs.sort(key=lambda p: len(p[0]), reverse=True)

    def collides(start, end):
        return any(not (end <= s or e <= start) for s, e, _ in claims)

    for val_str, placeholder in valid_pairs:
        prefix = r"\b" if val_str[0].isalnum() else ""
        suffix = r"\b" if val_str[-1].isalnum() else ""
        pattern = prefix + re.escape(val_str) + suffix
        for match in re.finditer(pattern, explanation, flags=re.IGNORECASE):
            if collides(match.start(), match.end()):
                continue
            claims.append((match.start(), match.end(), placeholder))
            break

    if not claims:
        return explanation

    claims.sort(key=lambda item: item[0])
    fragments = []
    cursor = 0
    for start, end, placeholder in claims:
        fragments.append(explanation[cursor:start])
        fragments.append(placeholder)
        cursor = end
    fragments.append(explanation[cursor:])
    return "".join(fragments)


FIELD_CODE = {
    "nama_lengkap": "NAMA",
    "tempat_lahir": "TMPT",
    "tanggal_lahir": "TGLL",
    "jenis_kelamin": "JKEL",
    "nama_ibu": "IBU",
}

FIELD_ORDER = ["nama_lengkap", "tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nama_ibu"]

VERDICT_CODE = {
    "DIFFERENT": "DIFF",
    "EMPTY_IN_INSTITUTION": "EMPT_INC",
    "EMPTY_IN_MASTER": "EMPT_MST",
}


def derive_pattern_name(signature: str, pattern_hash: str) -> str:
    """Generate a clean, collision-free, human-readable pattern name."""
    short_hash = pattern_hash[:8]
    field_verdicts = {}
    for piece in signature.split("|"):
        if ":" in piece:
            f, v = piece.split(":", 1)
            if v != "SAME":
                field_verdicts[f] = v

    if not field_verdicts:
        return f"pattern_ALL_SAME_{short_hash}"

    ordered_keys = sorted(
        field_verdicts.keys(),
        key=lambda k: FIELD_ORDER.index(k) if k in FIELD_ORDER else 99,
    )

    parts = []
    for f in ordered_keys:
        v = field_verdicts[f]
        f_code = FIELD_CODE.get(f, f[:4].upper())
        v_code = VERDICT_CODE.get(v, v[:4].upper())
        parts.append(f"{f_code}_{v_code}")

    suffix = "_".join(parts)
    return f"pattern_{suffix}_{short_hash}"


def resolve_unresolved_patterns(
    con,
    model: str | None = None,
    limit: int | None = None,
    allow_fallback: bool = True,
) -> dict:
    """
    Detect uncached patterns, generate explanations via LLM or deterministic fallback,
    and persist templates into PostgreSQL reasoning_patterns.
    """
    query = """
        SELECT
            pattern_signature,
            pattern_hash,
            MIN(id_incoming) AS sample_id,
            COUNT(*) AS pattern_count
        FROM reasoning_verdict
        WHERE pattern_hash NOT IN (SELECT pattern_hash FROM pg.public.reasoning_patterns)
        GROUP BY pattern_signature, pattern_hash
        ORDER BY pattern_count DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"

    unresolved = con.execute(query).fetchall()
    results = {
        "unresolved_count": len(unresolved),
        "patterns_generated": 0,
        "llm_calls": 0,
        "fallback_calls": 0,
    }

    new_hashes = []
    llm_sample_ids = []
    for signature, p_hash, sample_id, _ in unresolved:
        new_hashes.append(p_hash)
        sample_query = f"""
            SELECT * FROM reasoning_verdict
            WHERE pattern_hash = {q(p_hash)} AND id_incoming = {q(sample_id)}
            LIMIT 1
        """
        cursor = con.execute(sample_query)
        col_names = [d[0] for d in cursor.description]
        row_vals = cursor.fetchone()
        sample_row = dict(zip(col_names, row_vals))

        prompt_body = f"""RECORD VALUES:
Institution:
  Full Name: {sample_row.get('nama_incoming')}
  Place of Birth: {sample_row.get('tempat_lahir_incoming')}
  Date of Birth: {sample_row.get('tanggal_lahir_incoming')}
  Gender: {sample_row.get('jenis_kelamin_incoming')}
  Mother's Name: {sample_row.get('nama_ibu_incoming')}

Master:
  Full Name: {sample_row.get('nama_master')}
  Place of Birth: {sample_row.get('tempat_lahir_master')}
  Date of Birth: {sample_row.get('tanggal_lahir_master')}
  Gender: {sample_row.get('jenis_kelamin_master')}
  Mother's Name: {sample_row.get('nama_ibu_master')}

FIELD COMPARISON:
- nama_lengkap: {sample_row.get('v_nama')}
- tempat_lahir: {sample_row.get('v_tempat_lahir')}
- tanggal_lahir: {sample_row.get('v_tanggal_lahir')}
- jenis_kelamin: {sample_row.get('v_jenis_kelamin')}
- nama_ibu: {sample_row.get('v_nama_ibu')}
"""
        explanation = None
        used_llm = False
        if REASONING_AI_BASE_URL:
            try:
                explanation = call_local_llm(prompt_body, model=model)
                used_llm = True
                results["llm_calls"] += 1
            except Exception as err:
                if not allow_fallback:
                    raise
                print(f"[REASONING] LLM error for pattern {p_hash[:8]}: {err}. Falling back to rule generator.")

        if not explanation:
            explanation = construct_deterministic_fallback(sample_row)
            results["fallback_calls"] += 1

        explanation = sanitize_explanation(explanation)

        if used_llm:
            llm_sample_ids.append(sample_id)

        template = convert_explanation_to_template(explanation, sample_row)
        pattern_name = derive_pattern_name(signature, p_hash)

        execute_pg(
            con,
            f"""
            INSERT INTO reasoning_patterns (
                pattern_hash, pattern_name, pattern_signature, reason_template,
                sample_id, hit_count, created_at, updated_at
            )
            VALUES (
                {q(p_hash)}, {q(pattern_name)}, {q(signature)}, {q(template)},
                {q(sample_id)}, 1, now(), now()
            )
            ON CONFLICT (pattern_hash) DO NOTHING
            """,
        )
        results["patterns_generated"] += 1

    results["new_hashes"] = new_hashes
    results["llm_sample_ids"] = llm_sample_ids
    return results


# ── Step 3: Bulk Template Application ─────────────────────────────────────────

def apply_reasoning_templates(
    con,
    file_id: str,
    new_hashes: list[str] | None = None,
    llm_sample_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Substitutes record values into templates and updates manual_matches in bulk.
    """
    if llm_sample_ids is not None:
        llm_sample_ids_set = set(llm_sample_ids)
    elif new_hashes:
        new_hashes_sql = ", ".join(q(h) for h in new_hashes)
        rows = con.execute(
            f"SELECT sample_id FROM pg.public.reasoning_patterns WHERE pattern_hash IN ({new_hashes_sql}) AND sample_id IS NOT NULL"
        ).fetchall()
        llm_sample_ids_set = {r[0] for r in rows if r[0]}
    else:
        llm_sample_ids_set = set()

    if llm_sample_ids_set:
        sample_ids_sql = ", ".join(q(s) for s in llm_sample_ids_set)
        source_expr = f"CASE WHEN rv.id_incoming IN ({sample_ids_sql}) THEN 'LLM' ELSE 'CACHE' END"
    else:
        source_expr = "'CACHE'"

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE filled_reasons AS
        SELECT
            rv.file_id,
            rv.id_incoming,
            rp.pattern_name,
            {source_expr} AS reasoning_source,
            TRIM(TRIM(
              REPLACE(
                REPLACE(
                  REPLACE(
                    REPLACE(
                      REPLACE(
                        REPLACE(
                          REPLACE(
                            REPLACE(
                              REPLACE(
                                REPLACE(
                                  rp.reason_template,
                                  '{{incoming.nama_lengkap}}', COALESCE(NULLIF(TRIM(rv.nama_incoming), ''), 'EMPTY')
                                ),
                                '{{master.nama_lengkap}}', COALESCE(NULLIF(TRIM(rv.nama_master), ''), 'EMPTY')
                              ),
                              '{{incoming.tempat_lahir}}', COALESCE(NULLIF(TRIM(rv.tempat_lahir_incoming), ''), 'EMPTY')
                            ),
                            '{{master.tempat_lahir}}', COALESCE(NULLIF(TRIM(rv.tempat_lahir_master), ''), 'EMPTY')
                          ),
                          '{{incoming.tanggal_lahir}}', COALESCE(NULLIF(TRIM(rv.tanggal_lahir_incoming), ''), 'EMPTY')
                        ),
                        '{{master.tanggal_lahir}}', COALESCE(NULLIF(TRIM(rv.tanggal_lahir_master), ''), 'EMPTY')
                      ),
                      '{{incoming.jenis_kelamin}}', COALESCE(NULLIF(TRIM(rv.jenis_kelamin_incoming), ''), 'EMPTY')
                    ),
                    '{{master.jenis_kelamin}}', COALESCE(NULLIF(TRIM(rv.jenis_kelamin_master), ''), 'EMPTY')
                  ),
                  '{{incoming.nama_ibu}}', COALESCE(NULLIF(TRIM(rv.nama_ibu_incoming), ''), 'EMPTY')
                ),
                '{{master.nama_ibu}}', COALESCE(NULLIF(TRIM(rv.nama_ibu_master), ''), 'EMPTY')
              ),
              chr(34)), chr(39)) AS reason
        FROM reasoning_verdict rv
        JOIN pg.public.reasoning_patterns rp ON rp.pattern_hash = rv.pattern_hash;
    """)

    counts = con.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(CASE WHEN reasoning_source = 'CACHE' THEN 1 END) AS cache_hits,
            COUNT(CASE WHEN reasoning_source = 'LLM' THEN 1 END) AS llm_hits
        FROM filled_reasons
    """).fetchone()

    total_rows, cache_hits, llm_hits = counts[0], counts[1], counts[2]

    if not dry_run and total_rows > 0:
        con.execute("""
            INSERT INTO pg.public.manual_matches (
                file_id, id_incoming, reason, pattern_name, reasoning_source, reasoning_status
            )
            SELECT
                file_id, id_incoming, reason, pattern_name, reasoning_source, 'COMPLETED'
            FROM filled_reasons
            ON CONFLICT (file_id, id_incoming) DO UPDATE SET
                reason = EXCLUDED.reason,
                pattern_name = EXCLUDED.pattern_name,
                reasoning_source = EXCLUDED.reasoning_source,
                reasoning_status = EXCLUDED.reasoning_status;
        """)

        # Increment pattern hit counts
        pattern_counts = con.execute(
            "SELECT pattern_hash, COUNT(*) FROM reasoning_verdict GROUP BY pattern_hash"
        ).fetchall()
        for p_hash, cnt in pattern_counts:
            execute_pg(
                con,
                f"""
                UPDATE reasoning_patterns
                SET hit_count = hit_count + {cnt},
                    updated_at = now()
                WHERE pattern_hash = {q(p_hash)}
                """,
            )

    return {
        "updated_rows": total_rows,
        "cache_hits": cache_hits,
        "llm_hits": llm_hits,
        "dry_run": dry_run,
    }


# ── Full Orchestration ────────────────────────────────────────────────────────

def execute_reasoning(job: dict, dry_run: bool = False, limit: int | None = None) -> dict:
    """
    Orchestrate full reasoning workflow:
      Session -> Verdict -> Pattern Resolution -> Bulk Apply.
    """
    file_id = job["file_id"]
    job_id = job.get("job_id")
    llm_model = job.get("llm_model")

    start_time = time.perf_counter()
    con = buka_koneksi()

    try:
        if job_id:
            heartbeat(con, job_id, stage="BUILDING_VERDICT")

        row_count = build_verdict_table(con, file_id)
        if row_count == 0:
            result = {
                "file_id": file_id,
                "status": "COMPLETED",
                "message": "No pending manual review rows found.",
                "total_rows": 0,
                "patterns_resolved": 0,
                "duration_seconds": round(time.perf_counter() - start_time, 2),
            }
            return result

        if job_id:
            heartbeat(con, job_id, stage="RESOLVING_PATTERNS")

        resolution = resolve_unresolved_patterns(con, model=llm_model, limit=limit)

        if job_id:
            heartbeat(con, job_id, stage="APPLYING_TEMPLATES")

        application = apply_reasoning_templates(
            con,
            file_id,
            new_hashes=resolution.get("new_hashes"),
            llm_sample_ids=resolution.get("llm_sample_ids"),
            dry_run=dry_run,
        )

        duration = round(time.perf_counter() - start_time, 2)
        summary = {
            "file_id": file_id,
            "status": "COMPLETED",
            "total_rows": row_count,
            "patterns_generated": resolution["patterns_generated"],
            "cache_hits": application["cache_hits"],
            "llm_hits": application["llm_hits"],
            "llm_calls": resolution["llm_calls"],
            "duration_seconds": duration,
            "dry_run": dry_run,
        }
        return summary
    finally:
        con.close()
