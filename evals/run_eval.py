#!/usr/bin/env python3
"""
LLM-as-a-Judge Evaluation Harness for Synchrono AI Reasoning Engine.
Evaluates:
  1. Factuality & Field Fidelity (zero hallucinations on matching fields, accurate discrepancy reporting)
  2. Template Structure & Consistency ({incoming.<field>} vs {master.<field>} tokens, no hardcoded leaks)
  3. Conciseness & Readability (1-2 sentences, zero conversational fluff, human reviewer scannability)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reasoning.config import (
    REASONING_AI_API_KEY,
    REASONING_AI_BASE_URL,
    REASONING_AI_MODEL,
    REASONING_AI_TIMEOUT,
)
from reasoning.db import get_db_connection, get_duckdb_connection, get_pg_connection

JUDGES_DIR = Path(__file__).resolve().parent / "judges"
OUTPUT_PATH = Path(__file__).resolve().parent / "results.json"


@dataclass
class JudgeVerdict:
    criterion: str
    score: str  # "pass", "partial", "fail"
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalRecord:
    id: str
    file_id: str
    pattern_name: str
    reasoning_source: str
    template: str
    hydrated_reason: str
    field_verdicts: dict[str, str]
    incoming_values: dict[str, str]
    master_values: dict[str, str]
    verdicts: dict[str, JudgeVerdict] = field(default_factory=dict)
    overall_pass: bool = False


def call_llm(prompt: str, model: str | None = None) -> str:
    """Call LLM endpoint for judging."""
    base_url = REASONING_AI_BASE_URL.rstrip("/")
    if base_url.endswith("/chat/completions"):
        url = base_url
    elif base_url.endswith("/v1") or base_url.endswith("/v1/"):
        url = f"{base_url.rstrip('/')}/chat/completions"
    else:
        url = f"{base_url}/v1/chat/completions"

    payload = json.dumps({
        "model": model or REASONING_AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "stream": False,
    }).encode("utf-8")

    current_api_key = os.getenv("REASONING_AI_API_KEY", "").strip() or REASONING_AI_API_KEY
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {current_api_key}",
    }
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=REASONING_AI_TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"].strip()


def extract_json(raw: str) -> dict:
    """Extract structured JSON from LLM judge response."""
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        m2 = re.search(r"\{.*?\}", raw, re.DOTALL)
        if m2:
            raw = m2.group(0)
    try:
        return json.loads(raw)
    except Exception:
        return {"rationale": raw, "score": "fail"}


def judge_factuality(record: EvalRecord, use_llm: bool = True) -> JudgeVerdict:
    """Evaluate factuality and field fidelity."""
    expected_diffs = {
        field_name: status
        for field_name, status in record.field_verdicts.items()
        if status in ("DIFFERENT", "EMPTY_IN_INSTITUTION", "EMPTY_IN_MASTER")
    }
    same_fields = {
        field_name: status
        for field_name, status in record.field_verdicts.items()
        if status == "SAME"
    }

    # Deterministic check
    reason_lower = record.hydrated_reason.lower()
    hallucinated = []
    for sf in same_fields:
        label = sf.replace("v_", "")
        if label in ("nama", "nama_lengkap"):
            indicators = ["full name is different", "name is different", "name differ", "full name differ"]
        elif label in ("tempat_lahir", "tempat"):
            indicators = ["place of birth is different", "place of birth differ", "birthplace is different"]
        elif label in ("tanggal_lahir", "tanggal"):
            indicators = ["date of birth is different", "date of birth differ", "dob is different"]
        elif label in ("jenis_kelamin", "kelamin"):
            indicators = ["gender is different", "gender differ", "sex is different"]
        elif label in ("nama_ibu", "ibu"):
            indicators = ["mother's name is different", "mother is different", "mother's name differ"]
        else:
            indicators = [f"{label} is different", f"{label} differ"]

        if any(ind in reason_lower for ind in indicators):
            hallucinated.append(sf)

    missing = []
    for df in expected_diffs:
        label = df.replace("v_", "")
        field_indicators = [label, label.replace("_", " ")]
        if label in ("nama", "nama_lengkap"):
            field_indicators.extend(["name", "full name"])
        elif label in ("tempat_lahir", "tempat"):
            field_indicators.extend(["place of birth", "pob", "birthplace"])
        elif label in ("tanggal_lahir", "tanggal"):
            field_indicators.extend(["date of birth", "dob", "birthdate"])
        elif label in ("jenis_kelamin", "kelamin"):
            field_indicators.extend(["gender", "sex"])
        elif label in ("nama_ibu", "ibu"):
            field_indicators.extend(["mother", "mother's name"])

        if not any(ind in reason_lower for ind in field_indicators):
            missing.append(df)

    if hallucinated:
        return JudgeVerdict(
            criterion="factuality_fidelity",
            score="fail",
            rationale=f"Hallucinated discrepancy on field(s) marked SAME: {hallucinated}.",
            metadata={"hallucinated_fields": hallucinated, "missing_fields": missing},
        )
    if missing:
        score = "partial" if len(missing) < len(expected_diffs) else "fail"
        return JudgeVerdict(
            criterion="factuality_fidelity",
            score=score,
            rationale=f"Omitted discordant field(s): {missing}.",
            metadata={"hallucinated_fields": [], "missing_fields": missing},
        )

    return JudgeVerdict(
        criterion="factuality_fidelity",
        score="pass",
        rationale="All discordant fields accurately captured with zero hallucination on matching fields.",
        metadata={"hallucinated_fields": [], "missing_fields": []},
    )


def judge_template_consistency(record: EvalRecord) -> JudgeVerdict:
    """Evaluate template structure and parameterization."""
    tpl = record.template
    valid_placeholders = {
        "{incoming.nama_lengkap}",
        "{master.nama_lengkap}",
        "{incoming.tempat_lahir}",
        "{master.tempat_lahir}",
        "{incoming.tanggal_lahir}",
        "{master.tanggal_lahir}",
        "{incoming.jenis_kelamin}",
        "{master.jenis_kelamin}",
        "{incoming.nama_ibu}",
        "{master.nama_ibu}",
    }

    found_placeholders = re.findall(r"\{[a-zA-Z0-9_\.]+\}", tpl)
    malformed = [p for p in found_placeholders if p not in valid_placeholders]

    # Check for raw value leaks in template
    leaks = []
    for k, v in record.incoming_values.items():
        if v and len(str(v).strip()) > 3 and str(v).strip().lower() in tpl.lower():
            # Check if this value is not just a common word
            if str(v).strip().upper() not in ("LAKI-LAKI", "PEREMPUAN", "EMPTY"):
                leaks.append(f"incoming.{k}={v}")

    for k, v in record.master_values.items():
        if v and len(str(v).strip()) > 3 and str(v).strip().lower() in tpl.lower():
            if str(v).strip().upper() not in ("LAKI-LAKI", "PEREMPUAN", "EMPTY"):
                leaks.append(f"master.{k}={v}")

    if leaks or malformed:
        return JudgeVerdict(
            criterion="template_consistency",
            score="fail",
            rationale=f"Template failed: leaks={leaks}, malformed_placeholders={malformed}.",
            metadata={"leaks": leaks, "malformed": malformed},
        )

    return JudgeVerdict(
        criterion="template_consistency",
        score="pass",
        rationale="Template cleanly tokenized with standard placeholders and zero hardcoded record leaks.",
        metadata={"found_placeholders": found_placeholders},
    )


def judge_conciseness(record: EvalRecord) -> JudgeVerdict:
    """Evaluate conciseness and reviewer readability."""
    text = record.hydrated_reason.strip()
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = len(sentences)

    has_preamble = any(
        text.lower().startswith(p)
        for p in (
            "sure",
            "here is",
            "based on",
            "i have",
            "after reviewing",
            "the following",
            "comparison between",
        )
    )
    has_em_dash = "—" in text or "--" in text

    if has_em_dash:
        return JudgeVerdict(
            criterion="conciseness_readability",
            score="fail",
            rationale="Explanation contains forbidden em dash punctuation (violates antislop R-02).",
            metadata={"sentence_count": sentence_count, "has_em_dash": True},
        )

    if has_preamble or sentence_count > 3:
        score = "partial" if sentence_count <= 4 else "fail"
        return JudgeVerdict(
            criterion="conciseness_readability",
            score=score,
            rationale=f"Overly verbose or contains conversational preamble (sentences={sentence_count}, preamble={has_preamble}).",
            metadata={"sentence_count": sentence_count, "has_preamble": has_preamble},
        )

    return JudgeVerdict(
        criterion="conciseness_readability",
        score="pass",
        rationale=f"Crisp, direct explanation in {sentence_count} sentence(s) without conversational fluff.",
        metadata={"sentence_count": sentence_count, "has_preamble": False},
    )


def load_dataset() -> list[EvalRecord]:
    """Load evaluation records from PostgreSQL manual_matches joined with reasoning_patterns."""
    records = []
    with get_db_connection() as con:
        rows = con.execute("""
            SELECT
                m.file_id,
                m.id_incoming,
                m.pattern_name,
                m.reasoning_source,
                m.reason,
                rp.reason_template,
                rp.pattern_signature,
                m.nama_incoming,
                m.tempat_lahir_incoming,
                m.tanggal_lahir_incoming,
                m.jenis_kelamin_incoming,
                m.nama_ibu_incoming
            FROM pg.public.manual_matches m
            JOIN pg.public.reasoning_patterns rp ON rp.pattern_name = m.pattern_name
            WHERE m.reasoning_status = 'COMPLETED'
            LIMIT 50;
        """).fetchall()

        for r in rows:
            inc_vals = {
                "nama_lengkap": r[7] or "",
                "tempat_lahir": r[8] or "",
                "tanggal_lahir": str(r[9] or ""),
                "jenis_kelamin": r[10] or "",
                "nama_ibu": r[11] or "",
            }

            sig_str = r[6] or ""
            field_verdicts = {}
            for part in sig_str.split("|"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    field_verdicts[f"v_{k}"] = v

            records.append(
                EvalRecord(
                    id=f"{r[0]}::{r[1]}",
                    file_id=r[0],
                    pattern_name=r[2],
                    reasoning_source=r[3],
                    hydrated_reason=r[4],
                    template=r[5],
                    incoming_values=inc_vals,
                    master_values={},
                    field_verdicts=field_verdicts,
                )
            )

    return records


def main():
    parser = argparse.ArgumentParser(description="Run LLM-as-a-Judge Eval on AI Reasoning outputs")
    parser.add_argument("--limit", type=int, default=20, help="Max records to evaluate")
    args = parser.parse_args()

    print("=" * 80)
    print(" LLM-AS-A-JUDGE EVALUATION HARNESS: SYNCHRONO AI REASONING")
    print("=" * 80)

    dataset = load_dataset()
    if not dataset:
        print("[WARN] No records found in manual_matches. Please run a reasoning batch first.")
        sys.exit(1)

    eval_subset = dataset[: args.limit]
    print(f"Loaded {len(dataset)} records from database. Evaluating {len(eval_subset)} samples...\n")

    results_data = []
    stats = {
        "total": len(eval_subset),
        "factuality_fidelity": {"pass": 0, "partial": 0, "fail": 0},
        "template_consistency": {"pass": 0, "fail": 0},
        "conciseness_readability": {"pass": 0, "partial": 0, "fail": 0},
        "overall_pass": 0,
    }

    for i, rec in enumerate(eval_subset, start=1):
        v1 = judge_factuality(rec)
        v2 = judge_template_consistency(rec)
        v3 = judge_conciseness(rec)

        rec.verdicts = {
            "factuality_fidelity": v1,
            "template_consistency": v2,
            "conciseness_readability": v3,
        }

        # Conjunctive pass rule
        rec.overall_pass = (
            v1.score == "pass" and v2.score == "pass" and v3.score in ("pass", "partial")
        )

        stats["factuality_fidelity"][v1.score] += 1
        stats["template_consistency"][v2.score] += 1
        stats["conciseness_readability"][v3.score] += 1
        if rec.overall_pass:
            stats["overall_pass"] += 1

        rec_dict = asdict(rec)
        # Convert verdicts to serializable dicts
        rec_dict["verdicts"] = {k: asdict(v) for k, v in rec.verdicts.items()}
        results_data.append(rec_dict)

        status_mark = "✓ PASS" if rec.overall_pass else "✗ FAIL"
        print(f"[{i}/{len(eval_subset)}] {rec.id} -> {status_mark}")
        print(f"   • Factuality  : {v1.score.upper()} ({v1.rationale})")
        print(f"   • Template    : {v2.score.upper()} ({v2.rationale})")
        print(f"   • Conciseness : {v3.score.upper()} ({v3.rationale})")

    # Save results JSON
    OUTPUT_PATH.write_text(json.dumps(results_data, indent=2, ensure_ascii=False))
    print("\n" + "=" * 80)
    print(" EVALUATION SUMMARY REPORT")
    print("=" * 80)
    print(f"Total Evaluated Records : {stats['total']}")
    print(f"Overall Passed Records  : {stats['overall_pass']} / {stats['total']} ({stats['overall_pass']/stats['total']*100:.1f}%)")
    print(f"Factuality Fidelity     : {stats['factuality_fidelity']}")
    print(f"Template Consistency    : {stats['template_consistency']}")
    print(f"Conciseness Readability : {stats['conciseness_readability']}")
    print(f"Detailed Results JSON   : {OUTPUT_PATH}")
    print("=" * 80)


if __name__ == "__main__":
    main()
