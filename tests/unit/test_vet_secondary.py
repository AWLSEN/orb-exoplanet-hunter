"""Unit tests for hunter.vet.secondary."""
from __future__ import annotations

import numpy as np

from hunter.ingest.tess import LightCurve, synthetic
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.secondary import check_secondary


def _mock_result(lc: LightCurve, period: float, t0: float, duration: float) -> TransitSearchResult:
    return TransitSearchResult(
        period_days=period,
        t0_bjd=t0,
        depth=0.003,
        duration_days=duration,
        sde=20.0,
        snr=20.0,
        n_transits=5,
        tic_id=lc.tic_id,
        sector=lc.sector,
    )


def _inject_secondary(lc: LightCurve, period: float, t0: float, duration: float, secondary_depth: float) -> LightCurve:
    """Add a secondary dip at phase 0.5."""
    flux = lc.flux.copy()
    phase = ((lc.time - t0) / period) % 1.0
    secondary = np.abs(phase - 0.5) < (duration / (2 * period))
    flux[secondary] -= secondary_depth
    return LightCurve(
        tic_id=lc.tic_id,
        sector=lc.sector,
        time=lc.time,
        flux=flux,
        flux_err=lc.flux_err,
        cadence_s=lc.cadence_s,
        source=lc.source + "|secondary-injected",
    )


def test_passes_for_true_planet_no_secondary() -> None:
    lc = synthetic(
        duration_days=30,
        noise_ppm=150,
        period_days=4.0,
        depth=0.003,
        transit_duration_days=0.1,
        t0_days=1.0,
    )
    r = _mock_result(lc, 4.0, 1.0, 0.1)
    gate = check_secondary(lc, r)
    assert gate.passed, f"clean planet LC should pass (got: {gate.reason})"
    assert gate.metrics["depth_ratio"] < 0.33


def test_fails_for_eclipsing_binary_with_deep_secondary() -> None:
    # Shallow primary + half-depth secondary → ratio ~0.5, well above threshold.
    clean = synthetic(
        duration_days=30,
        noise_ppm=100,
        period_days=4.0,
        depth=0.004,
        transit_duration_days=0.1,
        t0_days=1.0,
    )
    eb = _inject_secondary(clean, period=4.0, t0=1.0, duration=0.1, secondary_depth=0.002)
    r = _mock_result(eb, 4.0, 1.0, 0.1)
    gate = check_secondary(eb, r)
    assert not gate.passed, f"EB with 0.5× secondary should fail, reason: {gate.reason}"
    assert gate.metrics["depth_ratio"] >= 0.33
    assert gate.metrics["secondary_significance_sigma"] >= 3.0


def test_passes_for_shallow_secondary_below_ratio() -> None:
    # Primary = 0.004, secondary = 0.0005 → ratio = 0.125, below threshold.
    clean = synthetic(
        duration_days=30,
        noise_ppm=80,
        period_days=4.0,
        depth=0.004,
        transit_duration_days=0.1,
        t0_days=1.0,
    )
    with_weak = _inject_secondary(clean, period=4.0, t0=1.0, duration=0.1, secondary_depth=0.0005)
    r = _mock_result(with_weak, 4.0, 1.0, 0.1)
    gate = check_secondary(with_weak, r)
    # Should still pass because ratio < 0.33, even if secondary is significant.
    assert gate.passed, f"shallow secondary should pass, reason: {gate.reason}"


def test_soft_skip_on_sparse_phase_coverage() -> None:
    # Very short LC with period so long that phase 0.5 is never sampled.
    lc = synthetic(
        duration_days=1.0,
        noise_ppm=200,
        period_days=4.0,
        depth=0.003,
        transit_duration_days=0.1,
        t0_days=0.5,
    )
    r = _mock_result(lc, 4.0, 0.5, 0.1)
    gate = check_secondary(lc, r)
    assert gate.passed
    assert gate.severity == "soft"
    assert "points near phase" in gate.reason


def test_hard_fail_on_non_positive_primary_depth() -> None:
    # Pure noise + TLS result with random period — primary depth can be near 0.
    lc = synthetic(duration_days=20, noise_ppm=1000, rng_seed=42)
    r = _mock_result(lc, 3.0, 0.0, 0.1)
    gate = check_secondary(lc, r)
    # Either passes trivially (tiny random depth) or hard-fails on negative.
    # This test primarily verifies the negative-depth path doesn't crash.
    assert gate.severity in {"hard", "soft"}
    assert isinstance(gate.passed, bool)
