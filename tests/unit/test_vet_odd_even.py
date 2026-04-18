"""Unit tests for hunter.vet.odd_even — synthesize known true-positives
(matched-depth transits) and known false-positives (mismatched-depth
eclipsing binaries) to prove both sides of the gate."""
from __future__ import annotations

import numpy as np

from hunter.ingest.tess import LightCurve, synthetic
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.odd_even import check_odd_even


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


def _add_alternating_depth_transits(lc: LightCurve, period: float, t0: float, duration: float, odd_depth: float, even_depth: float) -> LightCurve:
    """Synthesize an EB: every other transit is deeper."""
    flux = lc.flux.copy()
    transit_num_real = (lc.time - t0) / period
    transit_num = np.round(transit_num_real).astype(int)
    phase_from_center = np.abs((lc.time - t0) - transit_num * period)
    in_transit = phase_from_center < duration / 2
    odd_mask = in_transit & (transit_num % 2 != 0)
    even_mask = in_transit & (transit_num % 2 == 0)
    flux[odd_mask] -= odd_depth
    flux[even_mask] -= even_depth
    return LightCurve(
        tic_id=lc.tic_id,
        sector=lc.sector,
        time=lc.time,
        flux=flux,
        flux_err=lc.flux_err,
        cadence_s=lc.cadence_s,
        source=lc.source + "|eb-injected",
    )


def test_passes_for_true_planet_with_matched_depths() -> None:
    # True planet: all transits same depth. Long baseline -> many transits.
    lc = synthetic(
        duration_days=30,
        noise_ppm=150,
        period_days=3.0,
        depth=0.003,
        transit_duration_days=0.08,
        t0_days=1.0,
        rng_seed=1,
    )
    r = _mock_result(lc, 3.0, 1.0, 0.08)
    gate = check_odd_even(lc, r)
    assert gate.passed, f"planet should pass odd-even (reason: {gate.reason})"
    assert gate.severity == "hard"
    assert gate.metrics["odd_n"] >= 3
    assert gate.metrics["even_n"] >= 3


def test_fails_for_eclipsing_binary_with_mismatched_depths() -> None:
    # Start from a clean LC; inject a mismatched pattern directly.
    clean = synthetic(
        duration_days=30,
        noise_ppm=100,
        period_days=None,
        rng_seed=2,
    )
    eb = _add_alternating_depth_transits(
        clean, period=3.0, t0=1.0, duration=0.08, odd_depth=0.005, even_depth=0.001
    )
    r = _mock_result(eb, 3.0, 1.0, 0.08)
    gate = check_odd_even(eb, r)
    assert not gate.passed, f"EB should fail, got: {gate.reason}"
    assert gate.severity == "hard"
    assert gate.metrics["significance_sigma"] > 3.0


def test_soft_skip_when_insufficient_parity_coverage() -> None:
    # Only 2 transits total — can't compare parities.
    lc = synthetic(
        duration_days=6,
        noise_ppm=100,
        period_days=3.0,
        depth=0.003,
        transit_duration_days=0.08,
        t0_days=1.0,
        rng_seed=3,
    )
    r = _mock_result(lc, 3.0, 1.0, 0.08)
    gate = check_odd_even(lc, r)
    assert gate.passed, "too-few-transits case must soft-pass, not hard-fail"
    assert gate.severity == "soft"
    assert "insufficient" in gate.reason.lower()


def test_hard_fail_on_invalid_search_result() -> None:
    lc = synthetic(duration_days=10, noise_ppm=100)
    bad = TransitSearchResult(
        period_days=-1.0,  # invalid
        t0_bjd=0.0,
        depth=0.001,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=3,
        tic_id=lc.tic_id,
        sector=lc.sector,
    )
    gate = check_odd_even(lc, bad)
    assert not gate.passed
    assert gate.severity == "hard"
    assert "non-positive" in gate.reason


def test_metrics_contain_depth_fields() -> None:
    lc = synthetic(
        duration_days=30,
        noise_ppm=150,
        period_days=3.0,
        depth=0.002,
        transit_duration_days=0.08,
        t0_days=1.0,
    )
    r = _mock_result(lc, 3.0, 1.0, 0.08)
    gate = check_odd_even(lc, r)
    assert {"odd_depth", "even_depth", "odd_n", "even_n"}.issubset(gate.metrics)
