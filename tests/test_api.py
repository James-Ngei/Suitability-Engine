"""
Smoke / contract tests for the FastAPI backend in `api.py`.

The client is created WITHOUT the context-manager form, so Starlette does not
run the startup lifespan handler — no county data is fetched from R2 / Planetary
Computer during the test run. That keeps these tests offline and fast: they
cover the metadata endpoints (which read only the committed JSON configs) and
the request-validation / not-loaded paths of `/analyze`.
"""

import pytest
from starlette.testclient import TestClient

import api

client = TestClient(api.app)


def test_ping():
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_reports_available_counties():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert "baringo" in body["available_counties"]


def test_health_reports_status():
    r = client.get("/health")
    assert r.status_code == 200
    # With no counties loaded (startup skipped), health is "degraded".
    assert r.json()["status"] in ("healthy", "degraded")


def test_list_counties_endpoint():
    r = client.get("/counties")
    assert r.status_code == 200
    assert any(c["county"] == "baringo" for c in r.json())


def test_list_crops_endpoint():
    r = client.get("/crops")
    assert r.status_code == 200
    assert any(c["crop_id"] == "cotton" for c in r.json())


def test_county_info_returns_weights_summing_to_one():
    r = client.get("/county", params={"county": "baringo", "crop": "cotton"})
    assert r.status_code == 200
    body = r.json()
    assert body["county"] == "baringo"
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-6


def test_criteria_endpoint_lists_all_criteria():
    r = client.get("/criteria", params={"county": "baringo", "crop": "cotton"})
    assert r.status_code == 200
    criteria = r.json()
    assert len(criteria) == 5
    names = {c["name"] for c in criteria}
    assert names == {"rainfall", "elevation", "temperature", "soil", "slope"}


def test_analyze_on_unloaded_county_is_rejected():
    # No data loaded in a test run → /analyze must refuse rather than 500.
    weights = {"rainfall": 0.3, "elevation": 0.15, "temperature": 0.2,
               "soil": 0.2, "slope": 0.15}
    r = client.post("/analyze", params={"county": "baringo"},
                    json={"weights": weights})
    assert r.status_code in (404, 503)


def test_analyze_rejects_malformed_body():
    # Missing the required `weights` field → 422 from pydantic validation.
    r = client.post("/analyze", params={"county": "baringo"}, json={})
    assert r.status_code == 422
