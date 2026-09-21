"""API tests via TestClient with an isolated database and model dir."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(name="client")
def fixture_client(tmp_path, monkeypatch):
    """App client backed by a throwaway DB (fresh schema) and no model."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'api_test.db'}")
    monkeypatch.setattr("src.ml_model.MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.ml_model.LATEST_POINTER_PATH", os.path.join(str(tmp_path), "latest_model.txt")
    )
    monkeypatch.setattr(
        "src.ml_model.RUNS_LOG_PATH", os.path.join(str(tmp_path), "runs.json")
    )
    monkeypatch.setattr(
        "src.drift.REFERENCE_PATH", os.path.join(str(tmp_path), "drift_reference.json")
    )
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_pipeline_state(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["last_scrape_status"] in {"never_run", "success", "running", "failed"}
    assert "model_available" in body


def test_calculate_endpoint_returns_breakdown(client):
    response = client.post(
        "/api/calculate", json={"price_jpy": 1_500_000, "engine_cc": 1500}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cif_kes"] > 0
    assert body["total_landed_cost_kes"] > body["cif_kes"]
    assert body["fx_is_estimate"] is True  # documented fallback, flagged


def test_calculate_rejects_bad_input(client):
    response = client.post("/api/calculate", json={"price_jpy": -5, "engine_cc": 1500})
    assert response.status_code == 422


def test_predict_returns_503_when_untrained(client):
    response = client.post(
        "/api/predict",
        json={
            "make": "Toyota", "model": "Axio", "year": 2020,
            "mileage_km": 30000, "engine_cc": 1500,
        },
    )
    assert response.status_code == 503


def test_listings_endpoint_ok_when_empty(client):
    response = client.get("/api/listings")
    assert response.status_code == 200
    assert response.json() == []


def test_price_events_endpoint_ok_when_empty(client):
    response = client.get("/api/price-events")
    assert response.status_code == 200
    assert response.json() == []


def test_pages_render(client):
    for path in ("/", "/predict-page", "/price-movements"):
        response = client.get(path)
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]


def test_drift_endpoint_without_reference(client):
    """Fresh test DB + temp model dir: no reference captured yet."""
    response = client.get("/api/drift")
    assert response.status_code == 200
    assert response.json()["status"] == "no_reference"


def test_health_includes_drift_flag(client):
    body = client.get("/api/health").json()
    assert "drift_status" in body
    assert "retraining_recommended" in body


def test_catalog_endpoints_cascade(client):
    assert client.get("/api/catalog/makes").json() == []
    assert client.get(
        "/api/catalog/models", params={"make": "Toyota"}
    ).json() == []
    assert client.get(
        "/api/catalog/years", params={"make": "Toyota", "model": "Fielder"}
    ).json() == []


def test_comparison_endpoint_404_for_unknown_car(client):
    response = client.get(
        "/api/comparison", params={"make": "Toyota", "model": "Fielder"}
    )
    assert response.status_code == 404


def test_dashboard_page_and_fragments(client):
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "sel-make" in page.text

    options = client.get("/dashboard/options", params={"make": "Toyota"})
    assert options.status_code == 200

    result = client.get(
        "/dashboard/result", params={"make": "Toyota", "model": "Fielder"}
    )
    assert result.status_code == 200

