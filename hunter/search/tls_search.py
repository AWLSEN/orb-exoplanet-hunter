"""Transit Least Squares search against a detrended light curve.

TLS is the community-standard successor to Box-Least-Squares (BLS):
same idea (grid search over (period, T0, duration) for a periodic flux
deficit) but the transit shape template is a realistic limb-darkened
model instead of a box, which gains sensitivity for shallow transits.

We only use the outputs we actually need downstream: best-fit period,
depth, SDE (Signal Detection Efficiency — the headline statistic), T0,
duration. Everything else stays inside the TLS result object if callers
need it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from hunter.ingest.tess import LightCurve


@dataclass
class TransitSearchResult:
    """What TLS found on a light curve."""

    period_days: float          # best-fit orbital period
    t0_bjd: float               # time of first transit midpoint (LC's time reference)
    depth: float                # fractional transit depth (1 - min/median)
    duration_days: float        # fitted transit duration
    sde: float                  # Signal Detection Efficiency (higher = more significant)
    snr: float                  # transit signal-to-noise ratio
    n_transits: int             # number of full transits in the searched window
    tic_id: int
    sector: int
    # Retain the raw TLS result for gates that want more detail (odd/even, etc.).
    raw: object = None

    @property
    def is_significant(self) -> bool:
        """Default SDE threshold for shortlisting (community-standard 8σ equiv)."""
        return self.sde >= 8.0


def search(
    lc: LightCurve,
    *,
    period_min_days: float = 0.5,
    period_max_days: float | None = None,
    oversampling_factor: int = 3,
    duration_grid_step: float = 1.1,
    use_threads: int = 1,
    verbose: bool = False,
) -> TransitSearchResult:
    """Run TLS and return the best-fit candidate.

    `period_max_days` defaults to half the LC span — TLS needs at least
    two transits to form a period.
    """
    if lc.n_points < 100:
        raise ValueError(f"TLS needs >=100 points, got {lc.n_points}")
    if period_min_days <= 0:
        raise ValueError("period_min_days must be positive")

    if period_max_days is None:
        period_max_days = max(period_min_days * 2, lc.duration_days / 2)
    if period_max_days <= period_min_days:
        raise ValueError(
            f"period_max_days ({period_max_days}) must exceed period_min_days "
            f"({period_min_days}); LC duration is {lc.duration_days:.2f}d"
        )

    from transitleastsquares import transitleastsquares

    model = transitleastsquares(lc.time, lc.flux, lc.flux_err)
    # `use_threads=1` keeps tests deterministic and matches Orb's typical
    # single-worker budget per sector-batch unit.
    result = model.power(
        period_min=period_min_days,
        period_max=period_max_days,
        oversampling_factor=oversampling_factor,
        duration_grid_step=duration_grid_step,
        use_threads=use_threads,
        show_progress_bar=verbose,
    )

    # TLS returns a giant object; pick out what we need.
    # `depth_mean[0]` is the *mean in-transit flux* (so ~0.998 for a 0.002
    # depth); convert to a fractional drop so downstream code can compare
    # apples-to-apples with injection depth.
    period = float(result.period)
    t0 = float(result.T0)
    depth = float(1.0 - result.depth_mean[0])
    duration = float(result.duration)
    sde = float(result.SDE)
    snr = float(getattr(result, "snr", 0.0) or 0.0)
    # In rare pathological cases (all-NaN light curves) distinct_transit_count
    # can be missing; default to 0 so downstream code isn't surprised.
    n_transits = int(getattr(result, "distinct_transit_count", 0) or 0)

    return TransitSearchResult(
        period_days=period,
        t0_bjd=t0,
        depth=depth,
        duration_days=duration,
        sde=sde,
        snr=snr,
        n_transits=n_transits,
        tic_id=lc.tic_id,
        sector=lc.sector,
        raw=result,
    )
