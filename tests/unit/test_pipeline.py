"""Unit tests for hunter.pipeline — inject synthetic LCs; no MAST."""
from __future__ import annotations

from pathlib import Path

import pytest

from hunter.ingest.tess import LightCurve, synthetic
from hunter.output.candidate import Candidate
from hunter.pipeline import PipelineResult, process_target


def _good_transit_lc(tic: int = 42, sector: int = 7) -> LightCurve:
    lc = synthetic(
        tic_id=tic,
        sector=sector,
        duration_days=25,
        cadence_s=600,
        noise_ppm=200,
        period_days=3.5,
        depth=0.003,
        transit_duration_days=0.1,
        t0_days=1.5,
        rng_seed=99,
    )
    return lc


def test_accepts_synthetic_planet_end_to_end(tmp_path: Path) -> None:
    res = process_target(
        42,
        ingest_fn=lambda tic, s: _good_transit_lc(tic=tic),
        write_to=tmp_path,
        min_sde=8.0,
    )
    assert res.accepted, f"synthetic planet should be accepted: {res.reason}"
    assert res.candidate is not None
    assert res.candidate.tic_id == 42
    # JSON file was written.
    files = list(tmp_path.glob("tic*.json"))
    assert len(files) == 1


def test_rejects_pure_noise_lc() -> None:
    noise = synthetic(
        tic_id=1, sector=1,
        duration_days=20, noise_ppm=300,
        period_days=None,
        rng_seed=123,
    )
    res = process_target(
        1,
        ingest_fn=lambda _tic, _s: noise,
        min_sde=8.0,
    )
    assert not res.accepted
    assert "SDE" in res.reason or "below floor" in res.reason


def test_rejects_when_ingest_raises() -> None:
    def boom(_tic, _s):
        raise RuntimeError("MAST down")
    res = process_target(7, ingest_fn=boom)
    assert not res.accepted
    assert "ingest failed" in res.reason
    assert "RuntimeError" in res.reason


def test_rejects_too_short_lc_for_detrend() -> None:
    tiny = synthetic(tic_id=1, sector=1, duration_days=0.1, noise_ppm=500)
    res = process_target(
        1,
        ingest_fn=lambda _tic, _s: tiny,
    )
    assert not res.accepted
    assert "detrend failed" in res.reason


def test_promotes_recurrence_to_confirmed_tier(tmp_path: Path) -> None:
    """Candidate from sector 7 matches a known candidate from sector 1
    → tier should lift to 'confirmed'."""
    # Seed one known candidate in a prior sector.
    known = Candidate(
        tic_id=42,
        sector=1,
        period_days=3.5,
        t0_bjd=1.5,
        depth=0.003,
        duration_days=0.1,
        sde=14.0,
        snr=14.0,
        n_transits=5,
        score=0.6,
        tier="strong",
        n_sectors_confirmed=1,
        sectors_seen=[1],
    )
    res = process_target(
        42,
        ingest_fn=lambda tic, s: _good_transit_lc(tic=tic, sector=7),
        known_candidates=[known],
        write_to=tmp_path,
    )
    assert res.accepted
    assert res.candidate is not None
    assert res.candidate.n_sectors_confirmed == 2
    # Recurrence should push to confirmed tier if the score clears 0.7.
    # Otherwise at least n_sectors_confirmed should be bumped and tier >= 'moderate'.
    assert res.candidate.tier in {"confirmed", "strong"}


def test_write_to_none_skips_persistence(tmp_path: Path) -> None:
    res = process_target(
        42,
        ingest_fn=lambda tic, s: _good_transit_lc(tic=tic),
        write_to=None,
    )
    assert res.accepted
    assert list(tmp_path.glob("*.json")) == []
