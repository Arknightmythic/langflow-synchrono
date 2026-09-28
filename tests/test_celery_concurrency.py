"""
Celery & Concurrency Idempotency Unit Tests.
Verifies:
  1. Celery app configuration and task registration.
  2. Distributed pattern lock state machine (RESOLVING -> COMPLETED).
  3. Atomic claim winner vs waiter resolution.
"""

import sys
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from reasoning.celery_app import celery_app
from reasoning.tasks import process_reasoning_batch
from reasoning.db import get_pg_connection, get_db_connection
from reasoning.jobs import execute_pg


def test_celery_app_configuration():
    """Verify Celery app loads correct configurations."""
    assert celery_app.main == "synchrono_reasoning"
    assert "reasoning.tasks.process_reasoning_batch" in celery_app.tasks
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_acks_late is True


def test_pattern_locking_atomic_claim():
    """Verify atomic distributed locking: only 1 worker wins the claim for the same pattern."""
    p_hash = f"test_lock_{int(time.time()*1000)}"
    p_name = "test_pattern_lock"
    p_sig = "dummy_sig"
    worker_1 = "worker_alpha"
    worker_2 = "worker_beta"

    try:
        # Worker 1 attempts to claim
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO reasoning_patterns (
                        pattern_hash, pattern_name, pattern_signature, status, locked_by, locked_at, sample_id, hit_count, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, 'RESOLVING', %s, now(), 'sample_1', 0, now(), now())
                    ON CONFLICT (pattern_hash) DO UPDATE
                    SET status = 'RESOLVING', locked_by = EXCLUDED.locked_by, locked_at = now()
                    WHERE reasoning_patterns.status = 'FAILED'
                    RETURNING pattern_hash, status, locked_by;
                """, (p_hash, p_name, p_sig, worker_1))
                claim_1 = cur.fetchone()

        assert claim_1 is not None
        assert claim_1[2] == worker_1
        assert claim_1[1] == "RESOLVING"

        # Worker 2 attempts to claim the EXACT same pattern simultaneously
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO reasoning_patterns (
                        pattern_hash, pattern_name, pattern_signature, status, locked_by, locked_at, sample_id, hit_count, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, 'RESOLVING', %s, now(), 'sample_2', 0, now(), now())
                    ON CONFLICT (pattern_hash) DO UPDATE
                    SET status = 'RESOLVING', locked_by = EXCLUDED.locked_by, locked_at = now()
                    WHERE reasoning_patterns.status = 'FAILED'
                    RETURNING pattern_hash, status, locked_by;
                """, (p_hash, p_name, p_sig, worker_2))
                claim_2 = cur.fetchone()

        # Worker 2 must be rejected (returned None because conflict WHERE was not met)
        assert claim_2 is None

        # Worker 1 completes the pattern
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE reasoning_patterns
                    SET reason_template = 'Resolved template',
                        status = 'COMPLETED',
                        updated_at = now()
                    WHERE pattern_hash = %s;
                """, (p_hash,))

        # Worker 2 reads the completed pattern
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, reason_template FROM reasoning_patterns WHERE pattern_hash = %s", (p_hash,))
                row = cur.fetchone()
                assert row[0] == "COMPLETED"
                assert row[1] == "Resolved template"

    finally:
        # Cleanup
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reasoning_patterns WHERE pattern_hash = %s", (p_hash,))
