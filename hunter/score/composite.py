"""Composite ranking score for surviving candidates.

Once a candidate clears the vetting chain we need a single number to
order the dashboard by. Inputs are orthogonal signals we already
compute — SDE, n_transits, depth, vet soft-warnings, multi-sector
recurrence count. The score is bounded [0, 1]; higher = more promising.

Weights are tuned for M-dwarf HZ candidates: detection significance
(SDE) matters most, recurrence close second (two independent sectors
observing the same ephemeris is the gold signal), depth and transit
count are soft evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import VetReport

# Score weights — sum to 1.0.
W_SDE = 0.40
W_RECURRENCE = 0.35
W_TRANSITS = 0.10
W_DEPTH = 0.05
W_VET_CLEAN = 0.10


@dataclass
class Score:
    value: float            # [0, 1]
    sde_component: float
    recurrence_component: float
    transits_component: float
    depth_component: float
    vet_clean_component: float
    # Qualitative label for the dashboard.
    tier: str               # "confirmed" / "strong" / "moderate" / "weak"


def _sigmoid(x: float, steepness: float = 1.0, midpoint: float = 0.0) -> float:
    """Standard sigmoid; used for monotone bounded mappings."""
    try:
        return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))
    except OverflowError:
        return 0.0 if x < midpoint else 1.0


def _sde_component(sde: float) -> float:
    """SDE 8 → 0, SDE 15 → ~0.5, SDE 30 → ~0.95. Monotone in SDE."""
    return _sigmoid(sde, steepness=0.25, midpoint=15.0)


def _recurrence_component(n_sectors_confirmed: int) -> float:
    """1 sector = 0.0, 2 = 0.6, 3+ = 0.9, capped near 1.0 for 4+."""
    if n_sectors_confirmed <= 1:
        return 0.0
    if n_sectors_confirmed == 2:
        return 0.6
    if n_sectors_confirmed == 3:
        return 0.9
    return min(1.0, 0.9 + 0.025 * (n_sectors_confirmed - 3))


def _transits_component(n_transits: int) -> float:
    """Monotone in transit count; saturates around 10."""
    return _sigmoid(n_transits, steepness=0.5, midpoint=5.0)


def _depth_component(depth: float) -> float:
    """Very shallow (< 200 ppm) and very deep (> 2%) both penalize slightly —
    shallow is noise-dominated, deep is EB-likely — middle is planet-like."""
    if depth <= 0:
        return 0.0
    depth_ppm = depth * 1e6
    # Peak around 2000 ppm (~0.2%) — typical for small planets around cool stars.
    logd = math.log10(max(1.0, depth_ppm))
    return max(0.0, 1.0 - 0.6 * abs(logd - math.log10(2000)))


def _vet_clean_component(report: VetReport) -> float:
    """1.0 if no soft warnings, decreasing by 0.15 per warning down to 0."""
    n_soft = len(report.soft_failures)
    return max(0.0, 1.0 - 0.15 * n_soft)


def _tier(value: float, n_sectors_confirmed: int) -> str:
    if n_sectors_confirmed >= 2 and value >= 0.7:
        return "confirmed"
    if value >= 0.7:
        return "strong"
    if value >= 0.4:
        return "moderate"
    return "weak"


def score_candidate(
    result: TransitSearchResult,
    vet: VetReport,
    *,
    n_sectors_confirmed: int = 1,
) -> Score:
    """Turn (search result, vet report, recurrence count) into one score."""
    if not vet.passed:
        # Hard-fail candidates shouldn't be scored; defensive guard.
        return Score(
            value=0.0,
            sde_component=0.0,
            recurrence_component=0.0,
            transits_component=0.0,
            depth_component=0.0,
            vet_clean_component=0.0,
            tier="rejected",
        )

    sde_c = _sde_component(result.sde)
    rec_c = _recurrence_component(n_sectors_confirmed)
    tra_c = _transits_component(result.n_transits)
    dep_c = _depth_component(result.depth)
    vet_c = _vet_clean_component(vet)

    value = (
        W_SDE * sde_c
        + W_RECURRENCE * rec_c
        + W_TRANSITS * tra_c
        + W_DEPTH * dep_c
        + W_VET_CLEAN * vet_c
    )
    value = max(0.0, min(1.0, value))
    return Score(
        value=value,
        sde_component=sde_c,
        recurrence_component=rec_c,
        transits_component=tra_c,
        depth_component=dep_c,
        vet_clean_component=vet_c,
        tier=_tier(value, n_sectors_confirmed),
    )
