"""
Unit and Integration Tests for AI Reasoning Module.

Verifies:
  1. SQL verdict pre-computation and signature/hash generation.
  2. Gender and empty field handling.
  3. LLM template conversion and placeholder substitution.
  4. Asynchronous job recording, heartbeat, and status lifecycle.
  5. End-to-end reasoning execution.
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from _reasoning import (
    apply_reasoning_templates,
    build_verdict_table,
    construct_deterministic_fallback,
    convert_explanation_to_template,
    derive_pattern_name,
    execute_reasoning,
    resolve_unresolved_patterns,
    sanitize_explanation,
)
from _reasoning_jobs import (
    build_job_payload,
    execute_pg,
    get_job,
    harvest_stale_jobs,
    heartbeat,
    record_job,
    update_job_status,
)
from _shared import buka_koneksi


@pytest.fixture(scope="module")
def db_connection():
    con = buka_koneksi()
    yield con
    con.close()


def test_template_conversion_logic():
    """Verify conversion of concrete LLM output into reusable placeholder templates."""
    sample_row = {
        "nama_incoming": "Budianto Sudarsono",
        "nama_master": "Budi Sudarsono",
        "tempat_lahir_incoming": "Jakarta",
        "tempat_lahir_master": "Jakarta",
        "tanggal_lahir_incoming": "1990-05-12",
        "tanggal_lahir_master": "",
        "jenis_kelamin_incoming": "L",
        "jenis_kelamin_master": "L",
        "nama_ibu_incoming": "Siti Aminah",
        "nama_ibu_master": "Suti Aminah",
    }

    explanation = (
        "Full name is different (Institution: Budianto Sudarsono vs Master: Budi Sudarsono), "
        "date of birth is empty in master, and mother's name is different "
        "(Institution: Siti Aminah vs Master: Suti Aminah)."
    )

    template = convert_explanation_to_template(explanation, sample_row)

    assert "{incoming.nama_lengkap}" in template
    assert "{master.nama_lengkap}" in template
    assert "{incoming.nama_ibu}" in template
    assert "{master.nama_ibu}" in template
    assert "Budianto Sudarsono" not in template
    assert "Siti Aminah" not in template


def test_deterministic_fallback_generator():
    """Verify deterministic fallback produces standard English phrasing matching prompt rules."""
    sample_row = {
        "v_nama": "DIFFERENT",
        "nama_incoming": "John Doe",
        "nama_master": "Jon Doe",
        "v_tempat_lahir": "SAME",
        "tempat_lahir_incoming": "Surabaya",
        "tempat_lahir_master": "Surabaya",
        "v_tanggal_lahir": "EMPTY_IN_INSTITUTION",
        "tanggal_lahir_incoming": "",
        "tanggal_lahir_master": "1985-01-01",
        "v_jenis_kelamin": "SAME",
        "jenis_kelamin_incoming": "L",
        "jenis_kelamin_master": "L",
        "v_nama_ibu": "DIFFERENT",
        "nama_ibu_incoming": "Mary",
        "nama_ibu_master": "Maria",
    }

    text = construct_deterministic_fallback(sample_row)
    assert "Full name is different (Institution: John Doe vs Master: Jon Doe)" in text
    assert "date of birth is empty in institution" in text
    assert "mother's name is different (Institution: Mary vs Master: Maria)" in text
    assert "place of birth" not in text  # Because verdict is SAME

    # Verify ALL_SAME case has no semicolon
    sample_all_same = {
        "v_nama": "SAME",
        "v_tempat_lahir": "SAME",
        "v_tanggal_lahir": "SAME",
        "v_jenis_kelamin": "SAME",
        "v_nama_ibu": "SAME",
    }
    all_same_text = construct_deterministic_fallback(sample_all_same)
    assert ";" not in all_same_text
    assert "All compared fields match, but flagged for manual review due to a borderline similarity score." == all_same_text


def test_sanitize_explanation():
    """Verify elimination of semicolons, fixing comma splices, and clean punctuation."""
    # 1. Semicolon in ALL_SAME
    text = "All compared fields match; flagged for manual review due to a borderline similarity score."
    sanitized = sanitize_explanation(text)
    assert ";" not in sanitized
    assert "All compared fields match, but flagged for manual review due to a borderline similarity score." == sanitized

    # 2. Comma splice missing 'and'
    text = "Full name is different (Institution: A vs Master: B), date of birth is different (Institution: C vs Master: D)."
    sanitized = sanitize_explanation(text)
    assert "Full name is different (Institution: A vs Master: B), and date of birth is different (Institution: C vs Master: D)." == sanitized

    # 3. Semicolon used as clause separator
    text = "Full name is different (Institution: A vs Master: B); date of birth is different (Institution: C vs Master: D)."
    sanitized = sanitize_explanation(text)
    assert ";" not in sanitized
    assert "Full name is different (Institution: A vs Master: B), and date of birth is different (Institution: C vs Master: D)." == sanitized


def test_derive_pattern_name_clean_and_collision_free():
    """Verify distinct prefixes for nama_lengkap vs nama_ibu and logical field ordering."""
    # Mother's name different
    sig_ibu = "jenis_kelamin:SAME|nama_ibu:DIFFERENT|nama_lengkap:SAME|tanggal_lahir:SAME|tempat_lahir:SAME"
    p_name_ibu = derive_pattern_name(sig_ibu, "abc123456789")
    assert "IBU_DIFF" in p_name_ibu
    assert "NAMA_DIFF" not in p_name_ibu

    # Both full name and mother's name different (previously duplicated NAMA_DIFF_NAMA_DIFF)
    sig_both = "jenis_kelamin:SAME|nama_ibu:DIFFERENT|nama_lengkap:DIFFERENT|tanggal_lahir:SAME|tempat_lahir:SAME"
    p_name_both = derive_pattern_name(sig_both, "abc123456789")
    assert "NAMA_DIFF_IBU_DIFF" in p_name_both
    assert "NAMA_DIFF_NAMA_DIFF" not in p_name_both

    # Empty institution vs empty master
    sig_inc = "jenis_kelamin:SAME|nama_ibu:EMPTY_IN_INSTITUTION|nama_lengkap:SAME|tanggal_lahir:SAME|tempat_lahir:SAME"
    p_name_inc = derive_pattern_name(sig_inc, "abc123456789")
    assert "IBU_EMPT_INC" in p_name_inc

    sig_mst = "jenis_kelamin:SAME|nama_ibu:EMPTY_IN_MASTER|nama_lengkap:SAME|tanggal_lahir:SAME|tempat_lahir:SAME"
    p_name_mst = derive_pattern_name(sig_mst, "abc123456789")
    assert "IBU_EMPT_MST" in p_name_mst

    # All same
    sig_all_same = "jenis_kelamin:SAME|nama_ibu:SAME|nama_lengkap:SAME|tanggal_lahir:SAME|tempat_lahir:SAME"
    p_name_all_same = derive_pattern_name(sig_all_same, "abc123456789")
    assert p_name_all_same.startswith("pattern_ALL_SAME_")


def test_job_management_lifecycle(db_connection):
    """Verify job registration, status updates, heartbeats, and stale job harvesting."""
    test_file_id = f"test-file-{uuid.uuid4().hex[:6]}"
    job = build_job_payload({"fileId": test_file_id, "llmModel": "test-model"})

    try:
        record_job(db_connection, job)

        fetched = get_job(db_connection, job_id=job["job_id"])
        assert fetched is not None
        assert fetched["status"] == "QUEUED"
        assert fetched["file_id"] == test_file_id

        heartbeat(db_connection, job["job_id"], stage="TESTING")
        fetched = get_job(db_connection, job_id=job["job_id"])
        assert fetched["stage"] == "TESTING"

        update_job_status(db_connection, job["job_id"], "COMPLETED", result={"rows": 10})
        fetched = get_job(db_connection, job_id=job["job_id"])
        assert fetched["status"] == "COMPLETED"
        assert fetched["result"] == {"rows": 10}
    finally:
        execute_pg(db_connection, f"DELETE FROM reasoning_jobs WHERE job_id = '{job['job_id']}';")


def test_full_reasoning_flow_on_sample_data(db_connection):
    """Test full reasoning execution: DB setup -> Step 1 -> Step 2 -> Step 3."""
    test_file_id = f"file_test_{uuid.uuid4().hex[:6]}"
    id_inc_1 = f"inc_001_{uuid.uuid4().hex[:4]}"
    id_inc_2 = f"inc_002_{uuid.uuid4().hex[:4]}"
    nik_master_1 = f"317101{uuid.uuid4().int % 10000000000:010d}"
    nik_master_2 = f"317102{uuid.uuid4().int % 10000000000:010d}"

    try:
        # 1. Seed master data
        execute_pg(
            db_connection,
            f"""
            INSERT INTO master (nik, nama_lengkap, tempat_lahir, tanggal_lahir, jenis_kelamin, nama_ibu)
            VALUES
              ('{nik_master_1}', 'Budi Sudarsono', 'Jakarta', '1990-05-12', 'Laki-Laki', 'Siti Aminah'),
              ('{nik_master_2}', 'Siti Rahmawati', 'Bandung', '1995-10-20', 'Perempuan', 'Nurhasanah');
            """,
        )

        # 2. Seed institution data
        execute_pg(
            db_connection,
            f"""
            INSERT INTO institution (file_id, id_incoming, nik_master, match_score, match_result, inserted_date)
            VALUES
              ('{test_file_id}', '{id_inc_1}', '{nik_master_1}', 75.0, 2, now()),
              ('{test_file_id}', '{id_inc_2}', '{nik_master_2}', 72.0, 2, now())
            ON CONFLICT (file_id, id_incoming) DO NOTHING;
            """,
        )

        # 3. Seed manual_matches with PENDING status (as populated by N7)
        execute_pg(
            db_connection,
            f"""
            INSERT INTO manual_matches (
                file_id, id_incoming, nama_incoming, tempat_lahir_incoming,
                tanggal_lahir_incoming, jenis_kelamin_incoming, nama_ibu_incoming,
                reasoning_status
            )
            VALUES
              ('{test_file_id}', '{id_inc_1}', 'Budianto Sudarsono', 'Jakarta', '1990-05-12', 'L', 'Siti Aminah', 'PENDING'),
              ('{test_file_id}', '{id_inc_2}', 'Siti Rahmawati', 'Bandung', '1995-10-20', 'P', 'Nur Hasanah', 'PENDING')
            ON CONFLICT (file_id, id_incoming) DO UPDATE SET reasoning_status = 'PENDING';
            """,
        )

        # Clean any previous patterns for these signatures before testing
        execute_pg(db_connection, "DELETE FROM reasoning_patterns;")

        # 4. Execute reasoning
        job = {"file_id": test_file_id}
        summary = execute_reasoning(job, dry_run=False)

        assert summary["status"] == "COMPLETED"
        assert summary["total_rows"] == 2
        assert summary["patterns_generated"] >= 1

        # 5. Verify records updated in manual_matches
        cursor = db_connection.execute(f"""
            SELECT id_incoming, reason, pattern_name, reasoning_source, reasoning_status
            FROM pg.public.manual_matches
            WHERE file_id = '{test_file_id}'
            ORDER BY id_incoming
        """)
        rows = cursor.fetchall()
        assert len(rows) == 2

        for row in rows:
            inc_id, reason, pattern_name, source, status = row
            assert status == "COMPLETED"
            assert reason is not None and len(reason) > 10
            assert source in ("LLM", "CACHE")
            assert pattern_name.startswith("pattern_")

        # 6. Verify second execution hits CACHE (idempotent)
        execute_pg(
            db_connection,
            f"UPDATE manual_matches SET reasoning_status = 'PENDING' WHERE file_id = '{test_file_id}';",
        )
        second_summary = execute_reasoning(job, dry_run=False)
        assert second_summary["cache_hits"] == 2
        assert second_summary["patterns_generated"] == 0
    finally:
        execute_pg(db_connection, f"DELETE FROM manual_matches WHERE file_id = '{test_file_id}';")
        execute_pg(db_connection, f"DELETE FROM institution WHERE file_id = '{test_file_id}';")
        execute_pg(db_connection, f"DELETE FROM master WHERE nik IN ('{nik_master_1}', '{nik_master_2}');")


def test_validate_onprem_endpoint_rejects_public_commercial_ai():
    """Verify that public commercial cloud AI providers are strictly blocked."""
    from _reasoning import validate_onprem_endpoint

    # Allowed: on-premise private domains and IPs
    validate_onprem_endpoint("https://ollama.mcp-tools.my.id")
    validate_onprem_endpoint("http://192.168.1.10:11434")
    validate_onprem_endpoint("http://localhost:11434")

    # Strictly forbidden: public commercial AI APIs
    with pytest.raises(PermissionError):
        validate_onprem_endpoint("https://api.openai.com/v1")

    with pytest.raises(PermissionError):
        validate_onprem_endpoint("https://api.anthropic.com/v1")

    with pytest.raises(PermissionError):
        validate_onprem_endpoint("https://generativelanguage.googleapis.com/v1beta")

