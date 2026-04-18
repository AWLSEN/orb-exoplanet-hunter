"""Pipeline-health check: injection-recovery sensitivity sweep.

For each (period, depth) cell in a grid, inject a synthetic transit into
a noise light curve and run the pipeline. Record what fraction of
injections recovers with the right period. The resulting map tells us
*where in parameter space* our pipeline is blind — and crucially, if
the map REGRESSES across runs, that's a pipeline break even if the
known-planets check still passes.

Kept intentionally small by default (3 periods × 3 depths × 2 trials =
18 TLS runs, ~1 minute). Orchestrator schedules it less frequently
than the known-planets check (nightly vs per-sector).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import synthetic
from hunter.search.tls_search import search
from verification.types import HealthResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridCell:
    period_days: float
    depth: float
    n_trials: int


DEFAULT_GRID: tuple[GridCell, ...] = (
    GridCell(1.5, 0.005, n_trials=2),
    GridCell(1.5, 0.002, n_trials=2),
    GridCell(5.0, 0.005, n_trials=2),
    GridCell(5.0, 0.002, n_trials=2),
    GridCell(10.0, 0.005, n_trials=2),
    GridCell(10.0, 0.002, n_trials=2),
)


def _attempt_recovery(
    period: float,
    depth: float,
    rng_seed: int,
    *,
    duration_days: float = 25.0,
    noise_ppm: float = 300.0,
) -> tuple[bool, float]:
    """Inject + recover one synthetic planet. Returns (success, rel_err)."""
    lc = synthetic(
        tic_id=-1,
        sector=-1,
        duration_days=duration_days,
        cadence_s=600,
        noise_ppm=noise_ppm,
        period_days=period,
        depth=depth,
        transit_duration_days=0.1,
        t0_days=1.0,
        rng_seed=rng_seed,
    )
    try:
        flat = detrend(lc, window_length_days=0.5).flat
    except Exception:
        return False, float("inf")
    try:
        r = search(
            flat,
            period_min_days=0.5,
            period_max_days=min(15, flat.duration_days / 2),
            oversampling_factor=2,
        )
    except Exception:
        return False, float("inf")
    rel_err = abs(r.period_days - period) / period
    return (r.sde >= 8.0 and rel_err < 0.02), rel_err


@dataclass
class CellResult:
    period_days: float
    depth: float
    trials: int
    recoveries: int

    @property
    def rate(self) -> float:
        return self.recoveries / self.trials if self.trials else 0.0


def check_injection_recovery(
    *,
    grid: Iterable[GridCell] = DEFAULT_GRID,
    min_recovery_rate: float = 0.5,
    rng_seed_base: int = 100,
) -> HealthResult:
    """Run the sweep. Hard-fail if any cell falls below `min_recovery_rate`."""
    cells: list[CellResult] = []
    failures: list[str] = []
    for cell in grid:
        recoveries = 0
        for t in range(cell.n_trials):
            ok, _err = _attempt_recovery(
                cell.period_days,
                cell.depth,
                rng_seed=rng_seed_base + int(cell.period_days * 100) + int(cell.depth * 1e6) + t,
            )
            if ok:
                recoveries += 1
        c = CellResult(
            period_days=cell.period_days,
            depth=cell.depth,
            trials=cell.n_trials,
            recoveries=recoveries,
        )
        cells.append(c)
        if c.rate < min_recovery_rate:
            failures.append(f"({c.period_days:.1f}d, {c.depth*1e6:.0f}ppm) {recoveries}/{c.trials}")

    total_trials = sum(c.trials for c in cells)
    total_recovs = sum(c.recoveries for c in cells)
    overall = total_recovs / total_trials if total_trials else 0.0

    passed = not failures
    reason = (
        f"overall recovery {total_recovs}/{total_trials} ({overall:.0%})"
        if passed
        else f"recovery below {min_recovery_rate:.0%} in cells: {', '.join(failures)}"
    )

    return HealthResult(
        name="injection_recovery",
        passed=passed,
        severity="hard",
        reason=reason,
        metrics={
            "overall_rate": overall,
            "cells": [
                {
                    "period_days": c.period_days,
                    "depth": c.depth,
                    "trials": c.trials,
                    "recoveries": c.recoveries,
                    "rate": c.rate,
                }
                for c in cells
            ],
        },
    )
