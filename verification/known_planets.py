"""Pipeline-health check: re-discover a canonical set of known planets.

Before every sector processing run, re-process a small "gold set" of
well-characterized TESS targets and confirm we still recover them.
If any known planet fails to recover above threshold, the pipeline is
broken (library upgrade, config drift, MAST data-format change, etc.)
and we halt before publishing anything.

Gold set is picked for diversity:
  - WASP-121 b (hot Jupiter, deep, short period — shotgun-easy)
  - Pi Mensae c (small planet, bright star, medium period)
  - TOI-700 b (M-dwarf planet, low-SNR — the hardest of the three,
    bellwether for regressions in the low-signal regime)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from hunter.ingest.tess import LightCurve, fetch_tic
from hunter.detrend.wotan_wrap import detrend
from hunter.search.tls_search import search
from verification.types import HealthResult

log = logging.getLogger(__name__)

IngestFn = Callable[[int], LightCurve]


@dataclass(frozen=True)
class GoldTarget:
    """Known target we must re-discover."""

    tic_id: int
    label: str
    period_days: float
    tolerance_rel: float         # required period match precision
    min_sde: float               # required SDE floor


DEFAULT_GOLD_SET: tuple[GoldTarget, ...] = (
    GoldTarget(22529346, "WASP-121 b", 1.27492504, tolerance_rel=0.01, min_sde=15.0),
    GoldTarget(261136679, "Pi Mensae c", 6.2679, tolerance_rel=0.02, min_sde=10.0),
    GoldTarget(150428135, "TOI-700 b", 9.977, tolerance_rel=0.05, min_sde=8.0),
)


def _default_fetch(tic_id: int) -> LightCurve:
    return fetch_tic(tic_id)


def check_known_planets(
    *,
    targets: Iterable[GoldTarget] = DEFAULT_GOLD_SET,
    ingest_fn: IngestFn = _default_fetch,
) -> HealthResult:
    """Re-run the pipeline on each gold target; hard-fail on first regression.

    Per-target outcomes are captured in `metrics` so a failing check tells
    an operator *which* target broke, not just "something broke".
    """
    per_target: dict[str, dict[str, Any]] = {}  # type: ignore[name-defined]
    n_recovered = 0
    n_attempted = 0
    first_failure: str | None = None

    for tgt in targets:
        n_attempted += 1
        entry: dict = {"label": tgt.label, "expected_period": tgt.period_days}
        try:
            lc = ingest_fn(tgt.tic_id)
            flat = detrend(lc, window_length_days=0.5).flat
            result = search(
                flat,
                period_min_days=0.5,
                period_max_days=min(15.0, flat.duration_days / 2),
                oversampling_factor=2,
            )
            rel_err = abs(result.period_days - tgt.period_days) / tgt.period_days
            entry.update(
                {
                    "recovered_period": result.period_days,
                    "sde": result.sde,
                    "rel_err": rel_err,
                }
            )
            if result.sde < tgt.min_sde:
                entry["error"] = f"SDE {result.sde:.2f} below floor {tgt.min_sde}"
            elif rel_err > tgt.tolerance_rel:
                entry["error"] = (
                    f"period {result.period_days:.4f} vs expected {tgt.period_days} "
                    f"(rel_err {rel_err:.3%} > tolerance {tgt.tolerance_rel:.2%})"
                )
            else:
                entry["recovered"] = True
                n_recovered += 1
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        if "error" in entry and first_failure is None:
            first_failure = f"{tgt.label}: {entry['error']}"
        per_target[tgt.label] = entry

    passed = n_recovered == n_attempted
    reason = (
        f"recovered {n_recovered}/{n_attempted} gold targets"
        if passed
        else f"recovered {n_recovered}/{n_attempted} — first failure: {first_failure}"
    )
    return HealthResult(
        name="known_planets",
        passed=passed,
        severity="hard",
        reason=reason,
        metrics={
            "n_attempted": n_attempted,
            "n_recovered": n_recovered,
            "targets": per_target,
        },
    )
