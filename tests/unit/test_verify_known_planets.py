"""Unit tests for verification.known_planets — injected ingest_fn returns
synthetic light curves with known transits so we never hit MAST."""
from __future__ import annotations

import numpy as np

from hunter.ingest.tess import LightCurve, synthetic
from verification.known_planets import GoldTarget, check_known_planets


def _injected_planet_lc(tic: int, period: float, depth: float = 0.004) -> LightCurve:
    return synthetic(
        tic_id=tic,
        sector=1,
        duration_days=25,
        cadence_s=600,
        noise_ppm=150,
        period_days=period,
        depth=depth,
        transit_duration_days=0.1,
        t0_days=1.0,
        rng_seed=tic + 1,
    )


def _gold(tic: int, period: float, min_sde: float = 8.0) -> GoldTarget:
    return GoldTarget(
        tic_id=tic,
        label=f"gold-{tic}",
        period_days=period,
        tolerance_rel=0.05,
        min_sde=min_sde,
    )


def test_passes_when_all_gold_targets_recover() -> None:
    targets = (
        _gold(1, 3.0),
        _gold(2, 5.0),
    )
    def ingest(tic):
        period = {1: 3.0, 2: 5.0}[tic]
        return _injected_planet_lc(tic, period)

    result = check_known_planets(targets=targets, ingest_fn=ingest)
    assert result.passed, f"should pass all recoveries (reason: {result.reason})"
    assert result.metrics["n_recovered"] == 2
    assert result.metrics["n_attempted"] == 2


def test_fails_when_ingest_raises_for_one_target() -> None:
    targets = (_gold(1, 3.0), _gold(2, 5.0))
    def ingest(tic):
        if tic == 2:
            raise RuntimeError("mock MAST down")
        return _injected_planet_lc(tic, 3.0)
    result = check_known_planets(targets=targets, ingest_fn=ingest)
    assert not result.passed
    assert result.severity == "hard"
    assert "mock MAST down" in result.reason
    assert result.metrics["n_recovered"] == 1


def test_fails_when_recovered_period_outside_tolerance() -> None:
    targets = (_gold(1, 3.0),)
    def ingest(tic):
        return _injected_planet_lc(tic, 4.5)  # injected at wrong period
    result = check_known_planets(targets=targets, ingest_fn=ingest)
    assert not result.passed
    assert "rel_err" in result.reason or "period" in result.reason


def test_fails_when_sde_below_floor() -> None:
    targets = (GoldTarget(tic_id=1, label="gold-1", period_days=3.0, tolerance_rel=0.05, min_sde=100.0),)
    def ingest(tic):
        return _injected_planet_lc(tic, 3.0)  # recovered just fine, but SDE well below 100
    result = check_known_planets(targets=targets, ingest_fn=ingest)
    assert not result.passed
    assert "SDE" in result.reason


def test_per_target_metrics_populated() -> None:
    targets = (_gold(1, 3.0),)
    def ingest(tic):
        return _injected_planet_lc(tic, 3.0)
    result = check_known_planets(targets=targets, ingest_fn=ingest)
    tgt_metrics = result.metrics["targets"]["gold-1"]
    assert tgt_metrics["expected_period"] == 3.0
    assert tgt_metrics["recovered"] is True
    assert tgt_metrics["rel_err"] < 0.05
    assert tgt_metrics["sde"] > 0
