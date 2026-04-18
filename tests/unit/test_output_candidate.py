"""Unit tests for hunter.output.candidate."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.output.candidate import (
    Candidate,
    list_candidates,
    read_candidate,
    write_candidate,
)
from hunter.score.composite import Score
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult, VetReport


def _sample_result() -> TransitSearchResult:
    return TransitSearchResult(
        period_days=3.7,
        t0_bjd=2459000.123,
        depth=0.0025,
        duration_days=0.12,
        sde=15.8,
        snr=17.2,
        n_transits=7,
        tic_id=12345,
        sector=42,
    )


def _sample_vet() -> VetReport:
    rep = VetReport()
    rep.add(
        GateResult(
            name="odd_even",
            passed=True,
            severity="hard",
            reason="odd 2500ppm vs even 2400ppm (0.8σ, threshold 3σ)",
            metrics={"odd_depth": 0.0025, "even_depth": 0.0024, "significance_sigma": 0.8},
        )
    )
    rep.add(
        GateResult(
            name="ephemeris_match",
            passed=True,
            severity="soft",
            reason="no matching TOI",
            metrics={"match_count": 0},
        )
    )
    return rep


def _sample_score() -> Score:
    return Score(
        value=0.78,
        sde_component=0.6,
        recurrence_component=0.9,
        transits_component=0.8,
        depth_component=0.95,
        vet_clean_component=1.0,
        tier="confirmed",
    )


def test_candidate_from_components_roundtrips_core_fields() -> None:
    c = Candidate.from_components(
        _sample_result(),
        _sample_vet(),
        _sample_score(),
        n_sectors_confirmed=3,
        sectors_seen=[40, 41, 42],
        source="hunter:test",
    )
    assert c.tic_id == 12345
    assert c.sector == 42
    assert c.period_days == 3.7
    assert c.sde == 15.8
    assert c.score == 0.78
    assert c.tier == "confirmed"
    assert c.n_sectors_confirmed == 3
    assert c.sectors_seen == [40, 41, 42]
    assert c.source == "hunter:test"
    assert len(c.gate_results) == 2
    assert c.gate_results[0]["name"] == "odd_even"
    assert c.gate_results[0]["metrics"]["odd_depth"] == 0.0025


def test_candidate_discovered_at_matches_given_time() -> None:
    c = Candidate.from_components(
        _sample_result(), _sample_vet(), _sample_score(),
        now=1712000000.0,
    )
    assert c.discovered_at.startswith("2024-04-01")  # epoch → UTC date
    assert c.updated_at == c.discovered_at


def test_filename_is_stable() -> None:
    c = Candidate.from_components(_sample_result(), _sample_vet(), _sample_score())
    assert c.filename() == "tic12345-s42.json"


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    c = Candidate.from_components(_sample_result(), _sample_vet(), _sample_score())
    path = write_candidate(c, directory=tmp_path)
    assert path == tmp_path / "tic12345-s42.json"
    assert path.exists()
    c2 = read_candidate(path)
    assert c2.tic_id == c.tic_id
    assert c2.score == c.score
    assert c2.gate_results == c.gate_results


def test_to_json_is_valid_and_sorted(tmp_path: Path) -> None:
    c = Candidate.from_components(_sample_result(), _sample_vet(), _sample_score())
    payload = c.to_json()
    parsed = json.loads(payload)
    # sort_keys=True means top-level keys are alphabetical.
    keys = list(parsed.keys())
    assert keys == sorted(keys)


def test_write_is_atomic_via_tmp(tmp_path: Path) -> None:
    c = Candidate.from_components(_sample_result(), _sample_vet(), _sample_score())
    write_candidate(c, directory=tmp_path)
    # No stray .tmp files should remain.
    assert not list(tmp_path.glob("*.tmp"))


def test_list_candidates_returns_every_valid_json(tmp_path: Path) -> None:
    # Write two distinct candidates + a malformed file.
    c1 = Candidate.from_components(_sample_result(), _sample_vet(), _sample_score())
    r2 = _sample_result()
    r2 = type(r2)(**{**r2.__dict__, "tic_id": 99999, "sector": 10})
    c2 = Candidate.from_components(r2, _sample_vet(), _sample_score())
    write_candidate(c1, directory=tmp_path)
    write_candidate(c2, directory=tmp_path)
    (tmp_path / "tic55555-s05.json").write_text("not json")

    out = list_candidates(tmp_path)
    tics = sorted(o.tic_id for o in out)
    assert tics == [12345, 99999]


def test_list_candidates_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert list_candidates(tmp_path / "does-not-exist") == []


def test_numpy_scalars_serialize(tmp_path: Path) -> None:
    import numpy as np

    rep = VetReport()
    rep.add(
        GateResult(
            name="test",
            passed=True,
            severity="hard",
            reason="ok",
            metrics={"ruwe": np.float64(1.25), "n": np.int64(7)},
        )
    )
    c = Candidate.from_components(_sample_result(), rep, _sample_score())
    path = write_candidate(c, directory=tmp_path)
    parsed = json.loads(path.read_text())
    metrics = parsed["gate_results"][0]["metrics"]
    assert isinstance(metrics["ruwe"], float)
    assert metrics["ruwe"] == pytest.approx(1.25)
    assert isinstance(metrics["n"], int)
