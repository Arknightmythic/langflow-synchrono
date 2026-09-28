"""
FastAPI Integration Tests for Synchrono AI Reasoning.

Verifies:
  1. Health check endpoint (/v1/reasoning/health).
  2. Clear cache endpoint (/v1/reasoning/clear-cache).
  3. Trigger with slash-containing file_id (/v1/reasoning/trigger/{file_id:path}).
  4. Status query with slash-containing file_id (/v1/reasoning/status/{file_id:path}).
  5. JSON body dispatch (/v1/reasoning/dispatch).
  6. Embedding ('jahit') reasoning_router into an external FastAPI application.
"""

import sys
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from reasoning import app, reasoning_router
from reasoning.db import pinjam_koneksi
from reasoning.jobs import execute_pg


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_check_endpoint(client):
    response = client.get("/v1/reasoning/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("HEALTHY", "DEGRADED")
    assert "gemma3:12b" in data["llm_model"]


def test_clear_cache_endpoint(client):
    # Insert dummy pattern to ensure there's something to clear
    with pinjam_koneksi() as con:
        execute_pg(
            con,
            """
            INSERT INTO reasoning_patterns (pattern_signature, pattern_hash, pattern_name, reason_template, hit_count, created_at, updated_at)
            VALUES ('dummy_sig', 'dummy_hash', 'pattern_dummy', 'Template dummy', 1, now(), now())
            ON CONFLICT (pattern_hash) DO NOTHING;
            """
        )

    response = client.post("/v1/reasoning/clear-cache")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert data["deleted_patterns"] >= 1

    # Verify cache is actually empty now
    with pinjam_koneksi() as con:
        count = con.execute("SELECT count(*) FROM pg.public.reasoning_patterns").fetchone()[0]
        assert count == 0


def test_trigger_with_slash_in_file_id(client):
    # Test triggering using an ID containing multiple slashes
    slash_file_id = "uploads/2026/09/batch_sample_slash.csv"

    response = client.post(
        f"/v1/reasoning/trigger/{slash_file_id}",
        params={"dryRun": True, "clearCache": False}
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "QUEUED"
    assert data["fileId"] == slash_file_id
    assert data["jobId"].startswith("reasoning-")

    # Check status endpoint with the exact same slash file_id
    status_resp = client.get(f"/v1/reasoning/status/{slash_file_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["found"] is True
    assert status_data["fileId"] == slash_file_id


def test_dispatch_via_json_body(client):
    file_id = "test_dispatch_json_file"
    response = client.post(
        "/v1/reasoning/dispatch",
        json={
            "fileId": file_id,
            "llmModel": "gemma3:12b",
            "dryRun": True
        }
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "QUEUED"
    assert data["fileId"] == file_id
    assert data["jobId"].startswith("reasoning-")


def test_embedded_router_in_external_app():
    """Verify reasoning_router can be 'dijahit' (embedded) into an external FastAPI application."""
    # Simulate an external third-party FastAPI backend
    external_backend = FastAPI(title="External Company Monolith")

    # 'Jahit' router reasoning ke backend eksternal
    external_backend.include_router(reasoning_router, prefix="/api/external")

    with TestClient(external_backend) as ext_client:
        # Check health through external app prefix
        resp = ext_client.get("/api/external/v1/reasoning/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("HEALTHY", "DEGRADED")

        # Trigger through external app with slash ID
        custom_id = "org/dept/user/dataset_001.csv"
        trigger_resp = ext_client.post(
            f"/api/external/v1/reasoning/trigger/{custom_id}",
            params={"dryRun": True}
        )
        assert trigger_resp.status_code == 202
        assert trigger_resp.json()["fileId"] == custom_id
