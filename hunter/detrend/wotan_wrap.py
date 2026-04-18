"""Detrend a light curve with wotan's robust filters.

The goal is to remove slow stellar variability + instrumental systematics
(TESS momentum dumps, scattered light from Earth/Moon) while preserving
transit dips. wotan's biweight filter is the community default because
it's outlier-robust — a deep transit doesn't drag the trend down, which
would otherwise hide the dip.

Downstream (transit search) expects a light curve whose out-of-transit
flux is very close to 1.0 with near-Gaussian noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from hunter.ingest.tess import LightCurve

Method = Literal["biweight", "median", "gp"]


@dataclass
class DetrendResult:
    """Output of a detrend call: the flat LC + the removed trend for plotting."""

    flat: LightCurve            # out-of-transit flux ≈ 1.0
    trend: np.ndarray           # multiplicative trend that was divided out
    window_length_days: float   # filter window actually used
    method: Method

    @property
    def residual_rms(self) -> float:
        """RMS of flat.flux - 1.0, in ppm."""
        return float(np.std(self.flat.flux - 1.0) * 1e6)


def _choose_window_length(lc: LightCurve, user_override: float | None) -> float:
    """Pick a sensible window length in days.

    Rule of thumb for M-dwarfs with expected transit durations < 4h:
    window ≈ 0.5 day. Short enough to track rotation but far longer than
    any transit we care about, so the filter doesn't eat the dip.
    """
    if user_override is not None:
        if user_override <= 0:
            raise ValueError(f"window_length_days must be positive, got {user_override}")
        return float(user_override)
    span = lc.duration_days
    if span < 2:
        return max(0.1, span / 5)
    return 0.5


def detrend(
    lc: LightCurve,
    *,
    method: Method = "biweight",
    window_length_days: float | None = None,
    break_tolerance_days: float = 0.5,
) -> DetrendResult:
    """Run wotan's flatten and return (flat_lc, trend, meta).

    `break_tolerance_days`: gaps >= this many days are treated as segment
    boundaries — wotan re-starts the filter on each side. TESS sectors
    have a mid-sector gap of ~1 day for data download so this matters.
    """
    if lc.n_points < 100:
        raise ValueError(f"detrend needs >=100 points, got {lc.n_points}")

    window = _choose_window_length(lc, window_length_days)

    # Deferred import — wotan is heavy (numba, scipy) and unit tests of the
    # wrapper's helpers shouldn't have to pay the import cost.
    from wotan import flatten

    flat_flux, trend = flatten(
        lc.time,
        lc.flux,
        window_length=window,
        method=method,
        return_trend=True,
        break_tolerance=break_tolerance_days,
        edge_cutoff=0.5,
    )

    # wotan can leave NaNs at segment edges; drop them so downstream tools
    # don't trip on missing values.
    good = np.isfinite(flat_flux) & np.isfinite(trend) & (trend > 0)
    if good.sum() < lc.n_points * 0.5:
        raise RuntimeError(
            f"detrend kept only {good.sum()}/{lc.n_points} points — filter is "
            f"eating data; window ({window}d) probably too short for this LC"
        )

    flat = LightCurve(
        tic_id=lc.tic_id,
        sector=lc.sector,
        time=lc.time[good],
        flux=flat_flux[good],
        flux_err=lc.flux_err[good] / trend[good],  # scale error by the trend
        cadence_s=lc.cadence_s,
        source=f"{lc.source}|detrend:{method}",
        meta={**lc.meta, "detrend_window_days": window, "detrend_method": method},
    )
    return DetrendResult(
        flat=flat,
        trend=trend[good],
        window_length_days=window,
        method=method,
    )
