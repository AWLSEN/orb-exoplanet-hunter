"""Pipeline-health check: false-positive rate on null data.

Generate pure-noise light curves (or phase-randomized real ones — the
effect is the same: no periodic transit signal). Run the full detrend
+ TLS chain. Measure the fraction that erroneously report SDE >= the
operational threshold.

If this fraction exceeds a small budget (default 1%), the significance
calculation is broken and every "candidate" above threshold is suspect
until proven otherwise. Hard-fail the pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import synthetic
from hunter.search.tls_search import search
from verification.types import HealthResult

log = logging.getLogger(__name__)


@dataclass
class TrialResult:
    sde: float
    triggered: bool
    error: str | None = None


def _run_trial(rng_seed: int, noise_ppm: float, sde_threshold: float) -> TrialResult:
    """Generate noise LC, run pipeline, record SDE + trigger flag."""
    lc = synthetic(
        tic_id=-1,
        sector=-1,
        duration_days=25.0,
        cadence_s=600,
        noise_ppm=noise_ppm,
        period_days=None,   # no injection — pure noise
        rng_seed=rng_seed,
    )
    try:
        flat = detrend(lc, window_length_days=0.5).flat
    except Exception as e:
        return TrialResult(sde=0.0, triggered=False, error=f"detrend: {e}")
    try:
        r = search(
            flat,
            period_min_days=0.5,
            period_max_days=min(15, flat.duration_days / 2),
            oversampling_factor=2,
        )
    except Exception as e:
        return TrialResult(sde=0.0, triggered=False, error=f"search: {e}")
    return TrialResult(sde=r.sde, triggered=r.sde >= sde_threshold)


def check_null_rate(
    *,
    n_trials: int = 20,
    sde_threshold: float = 8.0,
    max_fp_rate: float = 0.05,
    noise_ppm: float = 500.0,
    seed_base: int = 9000,
) -> HealthResult:
    """Run n_trials on pure noise; hard-fail if FP rate > max_fp_rate.

    Default 20 trials × 8+ seconds each = ~3 minutes. Orchestrator
    schedules nightly (same cadence as injection-recovery).
    """
    trials: list[TrialResult] = []
    for i in range(n_trials):
        trials.append(_run_trial(seed_base + i, noise_ppm, sde_threshold))

    triggers = [t for t in trials if t.triggered]
    errors = [t for t in trials if t.error]
    effective = n_trials - len(errors)
    fp_rate = len(triggers) / effective if effective > 0 else 0.0

    passed = fp_rate <= max_fp_rate
    reason = (
        f"{len(triggers)}/{effective} null LCs crossed SDE {sde_threshold} "
        f"(rate {fp_rate:.2%}, budget {max_fp_rate:.2%})"
    )
    return HealthResult(
        name="null_fp_rate",
        passed=passed,
        severity="hard",
        reason=reason,
        metrics={
            "n_trials": n_trials,
            "effective": effective,
            "triggers": len(triggers),
            "errors": len(errors),
            "fp_rate": fp_rate,
            "max_sde_seen": max((t.sde for t in trials), default=0.0),
        },
    )
