"""Gate: odd-vs-even transit depth agreement.

Eclipsing binaries often look like planets on period P but their "odd"
transits (primary eclipses) and "even" transits (secondary eclipses)
have different depths. A true planet shows statistically identical
depths on odd and even transits. This gate folds the light curve at
2×P and compares the means.

Hard fail when the odd-even depth difference exceeds 3σ of the combined
depth uncertainty — the conventional EB rejection threshold.
"""
from __future__ import annotations

import numpy as np

from hunter.ingest.tess import LightCurve
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult


def check_odd_even(
    lc: LightCurve,
    result: TransitSearchResult,
    *,
    sigma_threshold: float = 3.0,
) -> GateResult:
    """Compute depth on odd and even transit numbers and compare."""
    if result.period_days <= 0 or result.duration_days <= 0:
        return GateResult(
            name="odd_even",
            passed=False,
            severity="hard",
            reason="non-positive period or duration — search result invalid",
        )

    period = result.period_days
    t0 = result.t0_bjd
    half_dur = result.duration_days / 2

    # Transit number for each point (integer for points near a transit midpoint).
    transit_num_real = (lc.time - t0) / period
    transit_num = np.round(transit_num_real).astype(int)

    # Points within half-duration of a transit center.
    phase_from_center = np.abs((lc.time - t0) - transit_num * period)
    in_transit = phase_from_center < half_dur

    if not in_transit.any():
        return GateResult(
            name="odd_even",
            passed=False,
            severity="hard",
            reason="no in-transit points found at the reported ephemeris",
        )

    odd_mask = in_transit & (transit_num % 2 != 0)
    even_mask = in_transit & (transit_num % 2 == 0)

    # A meaningful odd-vs-even comparison needs >=2 distinct transits per
    # parity — otherwise one lucky/unlucky transit dominates and we're
    # really just measuring noise. Single-sector short-period candidates
    # sometimes have too few; soft-skip so they aren't blocked spuriously.
    odd_transits = int(len(np.unique(transit_num[odd_mask]))) if odd_mask.any() else 0
    even_transits = int(len(np.unique(transit_num[even_mask]))) if even_mask.any() else 0

    if odd_transits < 2 or even_transits < 2:
        return GateResult(
            name="odd_even",
            passed=True,
            severity="soft",
            reason=(
                f"insufficient parity coverage "
                f"(odd_transits={odd_transits}, even_transits={even_transits}); skipped"
            ),
            metrics={
                "odd_transits": odd_transits,
                "even_transits": even_transits,
                "odd_n": int(odd_mask.sum()),
                "even_n": int(even_mask.sum()),
            },
        )

    odd_flux = lc.flux[odd_mask]
    even_flux = lc.flux[even_mask]

    odd_depth = 1.0 - float(np.mean(odd_flux))
    even_depth = 1.0 - float(np.mean(even_flux))
    odd_sigma = float(np.std(odd_flux, ddof=1) / np.sqrt(odd_flux.size))
    even_sigma = float(np.std(even_flux, ddof=1) / np.sqrt(even_flux.size))
    diff = abs(odd_depth - even_depth)
    diff_sigma = float(np.sqrt(odd_sigma ** 2 + even_sigma ** 2))

    # Protect against zero-sigma (all identical flux values by chance).
    significance = diff / diff_sigma if diff_sigma > 0 else 0.0

    passed = significance < sigma_threshold
    return GateResult(
        name="odd_even",
        passed=passed,
        severity="hard",
        reason=(
            f"odd depth {odd_depth*1e6:.0f}ppm vs even {even_depth*1e6:.0f}ppm "
            f"({significance:.1f}σ difference, threshold {sigma_threshold}σ)"
        ),
        metrics={
            "odd_depth": odd_depth,
            "even_depth": even_depth,
            "odd_n": int(odd_mask.sum()),
            "even_n": int(even_mask.sum()),
            "odd_transits": odd_transits,
            "even_transits": even_transits,
            "significance_sigma": significance,
        },
    )
