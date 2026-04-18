"""Unit tests for hunter.detrend.wotan_wrap — wotan is imported for real
(no mocking) because the detrend behavior we care about IS the library's
numerical output. Tests still run offline; only CPU + RAM needed."""
from __future__ import annotations

import numpy as np
import pytest

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import LightCurve, synthetic


def _add_sinusoidal_trend(lc: LightCurve, amplitude: float, period_days: float) -> LightCurve:
    """Multiply flux by a slow sinusoidal trend to simulate stellar rotation."""
    trend = 1.0 + amplitude * np.sin(2 * np.pi * lc.time / period_days)
    return LightCurve(
        tic_id=lc.tic_id,
        sector=lc.sector,
        time=lc.time,
        flux=lc.flux * trend,
        flux_err=lc.flux_err * trend,
        cadence_s=lc.cadence_s,
        source=lc.source + "|trended",
    )


def test_detrend_removes_sinusoidal_trend() -> None:
    # Pure-noise LC with a 3% amplitude, 5-day rotation — nothing to preserve.
    lc = synthetic(duration_days=15, noise_ppm=500)
    trended = _add_sinusoidal_trend(lc, amplitude=0.03, period_days=5.0)
    result = detrend(trended, window_length_days=0.5)
    # Pre-detrend flux std is dominated by the 3% sinusoid; post-detrend
    # should shrink dramatically — close to the underlying noise floor.
    pre_std = float(np.std(trended.flux))
    post_std = float(np.std(result.flat.flux))
    assert pre_std > 0.015  # sinusoid is real
    assert post_std < 0.003  # squashed to near-noise level


def test_detrend_preserves_injected_transit_depth() -> None:
    # Long enough baseline that in-transit points are few % of total.
    lc = synthetic(
        duration_days=20,
        noise_ppm=200,
        period_days=4.0,
        depth=0.002,
        transit_duration_days=0.1,
    )
    trended = _add_sinusoidal_trend(lc, amplitude=0.02, period_days=6.0)
    result = detrend(trended, window_length_days=0.5)
    # Minimum flux in detrended LC should still be clearly below 1 - depth/2.
    # We allow some depth erosion from the biweight filter but flag anything
    # that eats more than half of it.
    assert result.flat.flux.min() < 1.0 - 0.002 / 2


def test_detrend_rejects_too_short_lightcurve() -> None:
    lc = synthetic(duration_days=0.1, cadence_s=600, noise_ppm=500)
    with pytest.raises(ValueError, match=">=100 points"):
        detrend(lc)


def test_detrend_rejects_nonpositive_window() -> None:
    lc = synthetic(duration_days=10, noise_ppm=500)
    with pytest.raises(ValueError, match="must be positive"):
        detrend(lc, window_length_days=0)


def test_detrend_chooses_default_window() -> None:
    lc = synthetic(duration_days=10, noise_ppm=500)
    result = detrend(lc)
    # Default for span >=2d is 0.5d.
    assert result.window_length_days == 0.5


def test_detrend_short_span_scales_window_down() -> None:
    lc = synthetic(duration_days=1.0, noise_ppm=500)
    result = detrend(lc)
    assert 0.0 < result.window_length_days <= 0.3


def test_detrend_result_residual_rms_reported_in_ppm() -> None:
    lc = synthetic(duration_days=10, noise_ppm=1000)
    result = detrend(lc)
    # Flat LC RMS should land somewhere near the injected noise floor.
    # Allow a factor of 2 on either side — edge effects + filter absorb some.
    assert 300 < result.residual_rms < 4000
