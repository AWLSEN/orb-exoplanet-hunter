"""Unit tests for hunter.ingest.tess — no network, no disk (tmp dir only)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from hunter.ingest.tess import (
    LightCurve,
    _cache_path,
    fingerprint,
    load_cached,
    normalize,
    save_cached,
    synthetic,
)


def test_synthetic_noise_only_has_unit_median() -> None:
    lc = synthetic(noise_ppm=500)
    assert lc.time.shape == lc.flux.shape == lc.flux_err.shape
    assert lc.n_points > 0
    assert abs(np.median(lc.flux) - 1.0) < 5e-4  # very tight for pure noise
    assert lc.source == "synthetic"


def test_synthetic_transit_produces_dips() -> None:
    lc = synthetic(period_days=3.0, depth=0.002, transit_duration_days=0.1, noise_ppm=100)
    # At depth 2000 ppm vs 100 ppm noise, the minimum must clearly dip below 1.
    assert lc.flux.min() < 0.998
    # Majority of points are out-of-transit and stay near unit median.
    assert abs(np.median(lc.flux) - 1.0) < 1e-3


def test_normalize_drops_nan_and_sorts() -> None:
    t = np.array([2.0, 1.0, np.nan, 3.0])
    f = np.array([1.1, np.nan, 1.0, 1.2])
    e = np.array([0.01, 0.01, 0.01, 0.01])
    t2, f2, e2 = normalize(t, f, e)
    # NaN rows dropped.
    assert t2.size == 2
    # Sorted.
    assert np.all(np.diff(t2) > 0)
    # Normalized to unit median.
    assert abs(np.median(f2) - 1.0) < 1e-9


def test_normalize_rejects_zero_error_points() -> None:
    t = np.array([1.0, 2.0])
    f = np.array([1.0, 1.1])
    e = np.array([0.0, 0.01])
    t2, f2, e2 = normalize(t, f, e)
    assert t2.size == 1


def test_normalize_raises_on_all_bad() -> None:
    t = np.array([np.nan, np.nan])
    f = np.array([1.0, 1.0])
    e = np.array([0.01, 0.01])
    with pytest.raises(ValueError, match="no finite points"):
        normalize(t, f, e)


def test_normalize_raises_on_negative_median() -> None:
    with pytest.raises(ValueError, match="median flux must be positive"):
        normalize(np.array([1.0, 2.0]), np.array([-1.0, -2.0]), np.array([0.1, 0.1]))


def test_lightcurve_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="shape mismatch"):
        LightCurve(
            tic_id=1,
            sector=1,
            time=np.array([1.0, 2.0]),
            flux=np.array([1.0]),
            flux_err=np.array([0.01, 0.01]),
            cadence_s=600,
        )


def test_lightcurve_empty_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        LightCurve(
            tic_id=1,
            sector=1,
            time=np.array([], dtype=np.float64),
            flux=np.array([], dtype=np.float64),
            flux_err=np.array([], dtype=np.float64),
            cadence_s=600,
        )


def test_lightcurve_completeness_and_duration() -> None:
    lc = synthetic(duration_days=10.0, cadence_s=600, noise_ppm=100)
    assert lc.duration_days == pytest.approx(10.0, abs=0.01)
    assert lc.completeness == pytest.approx(1.0, abs=0.02)


def test_cache_round_trip(tmp_path: Path) -> None:
    lc = synthetic(tic_id=99, sector=3, period_days=5.0, depth=0.003, noise_ppm=200)
    path = save_cached(lc, cache_dir=tmp_path)
    assert path.exists()
    assert path.name == "s03-c600.npz"

    lc2 = load_cached(99, 3, 600, cache_dir=tmp_path)
    assert lc2 is not None
    np.testing.assert_allclose(lc.time, lc2.time)
    np.testing.assert_allclose(lc.flux, lc2.flux)
    np.testing.assert_allclose(lc.flux_err, lc2.flux_err)
    assert lc2.tic_id == 99
    assert lc2.sector == 3
    assert lc2.cadence_s == 600
    assert lc2.source == "synthetic"


def test_cache_miss_returns_none(tmp_path: Path) -> None:
    assert load_cached(999999, 42, 600, cache_dir=tmp_path) is None


def test_cache_path_layout(tmp_path: Path) -> None:
    p = _cache_path(tmp_path, 12345, 7, 120)
    assert p == tmp_path / "tic12345" / "s07-c120.npz"
    assert p.parent.exists()


def test_fingerprint_stable() -> None:
    lc1 = synthetic(rng_seed=1)
    lc2 = synthetic(rng_seed=1)
    lc3 = synthetic(rng_seed=2)
    assert fingerprint(lc1) == fingerprint(lc2)
    assert fingerprint(lc1) != fingerprint(lc3)
    assert len(fingerprint(lc1)) == 12
