"""End-to-end: TestClient drives POST /hunt/target on a real TESS star,
then asserts the candidate is readable via GET /candidates and GET
/candidates/{tic}. Validates the full FastAPI surface against live MAST.

Marked integration so the default pytest run skips it. ~30s runtime."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.integration

# Shotgun-easy target — WASP-121 b. See Batch 1 for the same choice.
TARGET_TIC = 22529346


@pytest.fixture
def fresh_app(tmp_path: Path, monkeypatch):
    """Boot the orchestrator pointed at a fresh tmp data dir."""
    data_dir = tmp_path / "data"
    (data_dir / "candidates").mkdir(parents=True)
    monkeypatch.setenv("HUNTER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HUNTER_BACKGROUND", "0")
    import hunter.orchestrator
    importlib.reload(hunter.orchestrator)
    return hunter.orchestrator.app, data_dir


def test_hunt_target_writes_candidate_and_dashboard_reads_it(fresh_app) -> None:
    app, data_dir = fresh_app
    with TestClient(app) as client:
        # Trigger the hunt. Synchronous — may take ~30s.
        r = client.post("/hunt/target", params={"tic": TARGET_TIC, "min_sde": 8.0})
        assert r.status_code == 200, f"hunt endpoint failed: {r.status_code} {r.text[:300]}"
        body = r.json()
        if not body["accepted"]:
            pytest.skip(f"hunt rejected target (likely MAST issue): {body['reason']}")

        # /candidates now sees this candidate.
        lst = client.get("/candidates").json()
        assert any(c["tic_id"] == TARGET_TIC for c in lst)

        # /candidates/{tic} returns full record with score + tier.
        detail = client.get(f"/candidates/{TARGET_TIC}").json()
        assert detail["tic_id"] == TARGET_TIC
        assert 0.0 <= detail["score"] <= 1.0
        assert detail["tier"] in {"weak", "moderate", "strong", "confirmed"}
        assert detail["sde"] >= 8.0
        assert detail["depth"] > 0

        # Dashboard HTML renders with working template hooks.
        html = client.get("/").text
        assert "orb-exoplanet-hunter" in html
        assert "/candidates" in html

        # On-disk file is present + readable via fetch.
        files = list((data_dir / "candidates").glob("tic*-s*.json"))
        assert len(files) == 1
        print(
            f"\n[e2e-dash] TIC {TARGET_TIC}: period={detail['period_days']:.4f}d, "
            f"SDE={detail['sde']:.2f}, tier={detail['tier']}, score={detail['score']:.3f}"
        )


def test_hunt_target_refuses_when_halted(fresh_app) -> None:
    app, data_dir = fresh_app
    (data_dir / "PIPELINE_HALT").write_text('{"halted_at":"now","reasons":[]}')
    with TestClient(app) as client:
        r = client.post("/hunt/target", params={"tic": TARGET_TIC})
    assert r.status_code == 503
    assert "halted" in r.text.lower()
