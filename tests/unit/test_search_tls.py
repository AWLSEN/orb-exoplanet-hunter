"""Unit tests for hunter.search.tls_search — runs TLS for real on
synthetic injected light curves. Slower than ingest/detrend tests (a few
seconds each) but still offline."""
from __future__ import annotations

import numpy as np
import pytest

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import synthetic
from hunter.search.tls_search import search


def test_recovers_injected_period_and_depth() -> None:
    # Deep injected transit (2000 ppm) on a quiet star → TLS should nail it.
    lc = synthetic(
        duration_days=20,
        cadence_s=600,
        noise_ppm=300,
        period_days=4.0,
        depth=0.002,
        transit_duration_days=0.1,
        t0_days=1.5,
        rng_seed=7,
    )
    flat = detrend(lc, window_length_days=0.5).flat
    result = search(flat, period_min_days=1.0, period_max_days=10.0)
    assert result.is_significant, f"SDE={result.sde:.2f} should be >= 8"
    assert result.sde > 10, f"deep injection should yield SDE>10, got {result.sde:.2f}"
    # Period within 1% of injection.
    assert result.period_days == pytest.approx(4.0, rel=0.01)
    # Depth within 30% (TLS biweighted depth is slightly biased).
    assert 0.0014 < result.depth < 0.0026


def test_null_data_is_insignificant() -> None:
    lc = synthetic(
        duration_days=20,
        noise_ppm=300,
        period_days=None,
        rng_seed=13,
    )
    result = search(lc, period_min_days=1.0, period_max_days=10.0)
    # Pure noise — TLS should not produce a strong signal.
    assert result.sde < 8.0, f"noise-only LC gave SDE={result.sde:.2f}, should be <8"
    assert not result.is_significant


def test_short_lightcurve_rejected() -> None:
    lc = synthetic(duration_days=0.1, noise_ppm=500)
    with pytest.raises(ValueError, match="TLS needs"):
        search(lc)


def test_bad_period_bounds_raise() -> None:
    lc = synthetic(duration_days=10, noise_ppm=500)
    with pytest.raises(ValueError, match="period_min_days must be positive"):
        search(lc, period_min_days=0)
    with pytest.raises(ValueError, match="must exceed"):
        search(lc, period_min_days=5, period_max_days=2)


def test_default_period_max_is_half_span() -> None:
    lc = synthetic(duration_days=20, noise_ppm=500)
    # TLS called with no max — internally picks LC.duration/2 = 10d. We can't
    # check that directly without tapping a private, so just make sure the
    # search runs and returns a result shape.
    result = search(lc, period_min_days=1.0)
    assert result.period_days > 0
    assert result.tic_id == lc.tic_id
    assert result.sector == lc.sector
