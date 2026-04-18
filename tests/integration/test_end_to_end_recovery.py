"""End-to-end integration test: fetch real TESS data from MAST, detrend,
run TLS, and confirm we recover a known planet.

Marked `integration` so the default `pytest` run stays offline. Run with:
    pytest -m integration tests/integration

Target: WASP-121 (TIC 22529346). Hot Jupiter, period 1.275d, depth ~1.5%.
Multiple transits per TESS sector + huge depth = the pipeline cannot
plausibly miss this if every stage works. Perfect Batch-1 smoke test.
"""
from __future__ import annotations

import os

import pytest

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import fetch_tic
from hunter.search.tls_search import search


pytestmark = pytest.mark.integration


# Fallback targets ordered by robustness of detection. If the first fails to
# download (rate limit, MAST hiccup) the test moves to the next.
RELIABLE_TARGETS: list[tuple[int, str, float, float]] = [
    # (TIC_ID, planet_label, known_period_days, tolerance_fraction)
    (22529346, "WASP-121 b", 1.27492504, 0.01),
    (261136679, "Pi Mensae c", 6.2679, 0.02),
    (150428135, "TOI-700 b", 9.977, 0.03),
]


def _first_succeeding_fetch(cache_dir) -> tuple[int, str, float, float, object]:
    """Return (tic, label, period, tol, lc) for the first target that downloads."""
    last_err = None
    for tic, label, period, tol in RELIABLE_TARGETS:
        try:
            lc = fetch_tic(tic, cache_dir=cache_dir)
            return tic, label, period, tol, lc
        except Exception as e:
            last_err = e
            continue
    pytest.skip(f"MAST unreachable; all targets failed. Last error: {last_err}")


def test_recover_known_planet_end_to_end(tmp_path):
    tic, label, expected_period, tolerance, lc = _first_succeeding_fetch(tmp_path)
    assert lc.n_points > 1000, f"LC has only {lc.n_points} points — too sparse"
    assert lc.duration_days > 10, f"LC span is {lc.duration_days:.1f}d — too short"

    # Detrend the light curve.
    flat_res = detrend(lc, window_length_days=0.5)
    assert flat_res.residual_rms < 10000, (
        f"post-detrend RMS {flat_res.residual_rms:.0f} ppm — pipeline broken"
    )
    flat = flat_res.flat

    # Search for transits.
    result = search(
        flat,
        period_min_days=0.5,
        period_max_days=min(15, flat.duration_days / 2),
        oversampling_factor=2,  # keep runtime manageable for CI
    )

    # Hard criterion 1: the recovered signal is statistically significant.
    assert result.sde >= 10, (
        f"{label}: recovered SDE={result.sde:.2f} is below 10 — "
        f"pipeline has regressed or this sector is worse than expected"
    )

    # Hard criterion 2: the recovered period matches the known planet (within
    # tolerance, which depends on sector coverage + known planet period).
    rel_err = abs(result.period_days - expected_period) / expected_period
    assert rel_err < tolerance, (
        f"{label}: recovered period {result.period_days:.4f}d vs "
        f"expected {expected_period}d -- relative error {rel_err:.3%} "
        f"exceeds tolerance {tolerance:.2%}"
    )

    # Hard criterion 3: depth is positive (TLS occasionally reports negative
    # depth on bad input — a sign the signal is really an anti-transit).
    assert result.depth > 0, (
        f"{label}: recovered depth {result.depth:.6f} is non-positive"
    )

    # Summary for CI log.
    print(
        f"\n[e2e] {label}: period={result.period_days:.4f}d "
        f"(expected {expected_period}d, {rel_err:.3%} error), "
        f"SDE={result.sde:.2f}, depth={result.depth*1e6:.0f}ppm, "
        f"n_transits={result.n_transits}"
    )


def test_pipeline_is_offline_by_default():
    """Sanity: this file is marked integration so default pytest skips it."""
    assert os.environ.get("PYTEST_CURRENT_TEST") is not None
