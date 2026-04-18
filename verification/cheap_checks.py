"""Cheap sanity checks run on the *current candidate database*.

These don't re-run the pipeline. They read the on-disk Candidate set
and look for statistical patterns that should hold if the pipeline is
healthy:

- `check_depth_distribution` — transit depths should cover several
  orders of magnitude. Bunching at one depth suggests systematics are
  leaking into our signals.
- `check_ephemeris_consistency` — when the same (TIC, period) shows
  up in two sectors, the predicted-vs-observed transit times must
  agree within timing uncertainty; if not, one of them is noise.

Neither is expensive; the orchestrator runs them hourly.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from hunter.output.candidate import Candidate
from verification.types import HealthResult


def check_depth_distribution(
    candidates: Iterable[Candidate],
    *,
    min_orders_of_magnitude: float = 0.8,
    min_candidates: int = 5,
) -> HealthResult:
    """Flag depth bunching.

    Convert depths to log10(ppm), require a spread of at least
    `min_orders_of_magnitude`. Uses standard deviation as a robust
    single-number spread measure.
    """
    positive_depths_ppm = [
        c.depth * 1e6 for c in candidates if c.depth > 0 and c.tier != "rejected"
    ]
    n = len(positive_depths_ppm)
    if n < min_candidates:
        return HealthResult(
            name="depth_distribution",
            passed=True,
            severity="soft",
            reason=f"only {n} candidates (need {min_candidates}); skipped",
            metrics={"n_candidates": n},
        )
    log_depths = [math.log10(d) for d in positive_depths_ppm]
    mean_log = sum(log_depths) / n
    var = sum((x - mean_log) ** 2 for x in log_depths) / n
    stdev = math.sqrt(var)

    passed = stdev >= min_orders_of_magnitude
    return HealthResult(
        name="depth_distribution",
        passed=passed,
        severity="hard",
        reason=(
            f"log10(depth_ppm) std = {stdev:.2f} "
            f"({'>' if passed else '<'} required {min_orders_of_magnitude}); "
            f"n={n}, mean log10(ppm)={mean_log:.2f}"
        ),
        metrics={
            "n_candidates": n,
            "log_depth_stdev": stdev,
            "mean_log_depth_ppm": mean_log,
        },
    )


def check_ephemeris_consistency(
    candidates: Iterable[Candidate],
    *,
    phase_sigma_threshold: float = 0.1,
) -> HealthResult:
    """For each (tic_id, period-bucket) with >=2 sectors, check T0 consistency.

    Predicted T0 at sector B from the seed candidate in sector A is:
        predicted_t0_B = t0_A + round((t0_B_obs - t0_A) / P) * P
    The residual |observed - predicted| divided by period gives a phase
    error. Across all multi-sector candidates, the RMS of the phase
    residual should be well below threshold if ephemeris is real.
    """
    # Group candidates by (tic_id, period rounded to ~1% bucket).
    groups: dict[tuple[int, float], list[Candidate]] = defaultdict(list)
    for c in candidates:
        if c.tier == "rejected" or c.period_days <= 0:
            continue
        bucket = round(c.period_days * 100) / 100.0  # 0.01 day buckets
        groups[(c.tic_id, bucket)].append(c)

    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    if not multi:
        return HealthResult(
            name="ephemeris_consistency",
            passed=True,
            severity="soft",
            reason="no multi-sector candidates yet; skipped",
            metrics={"multi_sector_groups": 0},
        )

    worst = 0.0
    total_sq = 0.0
    count = 0
    for (_tic, _bucket), members in multi.items():
        members = sorted(members, key=lambda c: c.sector)
        seed = members[0]
        P = seed.period_days
        for m in members[1:]:
            cycles = round((m.t0_bjd - seed.t0_bjd) / P)
            predicted = seed.t0_bjd + cycles * P
            residual_days = abs(m.t0_bjd - predicted)
            phase_err = residual_days / P
            worst = max(worst, phase_err)
            total_sq += phase_err ** 2
            count += 1

    rms = math.sqrt(total_sq / count) if count else 0.0
    passed = worst <= phase_sigma_threshold

    return HealthResult(
        name="ephemeris_consistency",
        passed=passed,
        severity="hard",
        reason=(
            f"{count} multi-sector pairings; worst phase err {worst:.3f}, "
            f"RMS {rms:.3f} (threshold {phase_sigma_threshold})"
        ),
        metrics={
            "multi_sector_groups": len(multi),
            "pairings_checked": count,
            "worst_phase_err": worst,
            "rms_phase_err": rms,
            "threshold": phase_sigma_threshold,
        },
    )
