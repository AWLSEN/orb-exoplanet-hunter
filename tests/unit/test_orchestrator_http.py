"""Unit tests for the FastAPI dashboard.

Uses FastAPI's TestClient — no uvicorn, no real ports. Tmp dirs make
each test independent.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hunter.output.candidate import Candidate, write_candidate


@pytest.fixture
def app_with_data_dir(tmp_path: Path, monkeypatch):
    """Reload the orchestrator module pointed at a clean tmp data dir."""
    data_dir = tmp_path / "data"
    (data_dir / "candidates").mkdir(parents=True)
    monkeypatch.setenv("HUNTER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUNTER_BACKGROUND", "0")  # no live health loop in tests
    # Force re-import so module-level DATA_DIR picks up the new env.
    import importlib
    import hunter.orchestrator
    importlib.reload(hunter.orchestrator)
    return hunter.orchestrator.app, data_dir


def _sample_candidate(tic: int = 111, sector: int = 7) -> Candidate:
    return Candidate(
        tic_id=tic, sector=sector, period_days=3.2, t0_bjd=1.5,
        depth=0.003, duration_days=0.09, sde=14.5, snr=14.5, n_transits=6,
        score=0.72, tier="strong", n_sectors_confirmed=1, sectors_seen=[sector],
    )


def test_health_endpoint_returns_200_and_not_halted(app_with_data_dir) -> None:
    app, _data = app_with_data_dir
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["halted"] is False


def test_health_reports_halt_when_flag_present(app_with_data_dir) -> None:
    app, data = app_with_data_dir
    (data / "PIPELINE_HALT").write_text('{"halted_at":"now","reasons":[]}')
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.json()["halted"] is True


def test_candidates_empty_list(app_with_data_dir) -> None:
    app, _ = app_with_data_dir
    with TestClient(app) as client:
        r = client.get("/candidates")
    assert r.status_code == 200
    assert r.json() == []


def test_candidates_list_returns_written_record(app_with_data_dir) -> None:
    app, data = app_with_data_dir
    c = _sample_candidate()
    write_candidate(c, directory=data / "candidates")
    with TestClient(app) as client:
        r = client.get("/candidates")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["tic_id"] == c.tic_id


def test_candidates_filter_by_tier(app_with_data_dir) -> None:
    app, data = app_with_data_dir
    c1 = _sample_candidate(tic=1)
    c1_cfd = Candidate(**{**c1.__dict__, "tic_id": 2, "tier": "confirmed"})
    write_candidate(c1, directory=data / "candidates")
    write_candidate(c1_cfd, directory=data / "candidates")
    with TestClient(app) as client:
        r = client.get("/candidates", params={"tier": "confirmed"})
    assert [row["tic_id"] for row in r.json()] == [2]


def test_candidates_filter_by_score(app_with_data_dir) -> None:
    app, data = app_with_data_dir
    low = Candidate(**{**_sample_candidate(tic=1).__dict__, "score": 0.3})
    high = Candidate(**{**_sample_candidate(tic=2).__dict__, "score": 0.9})
    write_candidate(low, directory=data / "candidates")
    write_candidate(high, directory=data / "candidates")
    with TestClient(app) as client:
        r = client.get("/candidates", params={"min_score": 0.5})
    assert [row["tic_id"] for row in r.json()] == [2]


def test_candidate_detail_found(app_with_data_dir) -> None:
    app, data = app_with_data_dir
    write_candidate(_sample_candidate(tic=42, sector=9), directory=data / "candidates")
    with TestClient(app) as client:
        r = client.get("/candidates/42")
    assert r.status_code == 200
    assert r.json()["tic_id"] == 42


def test_candidate_detail_404(app_with_data_dir) -> None:
    app, _ = app_with_data_dir
    with TestClient(app) as client:
        r = client.get("/candidates/999")
    assert r.status_code == 404


def test_pipeline_health_empty_returns_default_shape(app_with_data_dir) -> None:
    app, _ = app_with_data_dir
    with TestClient(app) as client:
        r = client.get("/pipeline-health")
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["results"] == []


def test_root_returns_html_dashboard(app_with_data_dir) -> None:
    app, _ = app_with_data_dir
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "<title>orb-exoplanet-hunter</title>" in r.text
    assert "/candidates" in r.text  # dashboard JS fetches from this path
