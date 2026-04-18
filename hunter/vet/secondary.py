"""Gate: secondary eclipse detection.

A true planet occults the star (primary transit, phase 0) and is itself
occulted behind the star at phase 0.5. For planets the secondary depth
is usually <<1% of primary (reflected light + thermal emission), so a
significant secondary is a near-certain sign of an eclipsing binary.

Conventional threshold: secondary/primary depth ratio > 1/3 → EB. This
lets borderline hot Jupiters with real thermal secondaries pass while
killing obvious EBs.
"""
from __future__ import annotations

import numpy as np

from hunter.ingest.tess import LightCurve
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult


def check_secondary(
    lc: LightCurve,
    result: TransitSearchResult,
    *,
    ratio_threshold: float = 0.33,
    secondary_sigma_required: float = 3.0,
) -> GateResult:
    """Compare depths at phase 0 (primary) and phase 0.5 (secondary)."""
    if result.period_days <= 0 or result.duration_days <= 0:
        return GateResult(
            name="secondary",
            passed=False,
            severity="hard",
            reason="non-positive period or duration — search result invalid",
        )

    P = result.period_days
    t0 = result.t0_bjd
    half_dur = result.duration_days / 2

    phase = ((lc.time - t0) / P) % 1.0
    # In-transit: phase near 0 (or 1); secondary: phase near 0.5.
    primary_mask = (phase < (half_dur / P)) | (phase > (1 - half_dur / P))
    secondary_mask = np.abs(phase - 0.5) < (half_dur / P)

    if not primary_mask.any():
        return GateResult(
            name="secondary",
            passed=False,
            severity="hard",
            reason="no primary-transit points found at the reported ephemeris",
        )

    primary_depth = 1.0 - float(np.mean(lc.flux[primary_mask]))
    if primary_depth <= 0:
        # TLS sometimes returns a spurious best-fit on anti-transits.
        return GateResult(
            name="secondary",
            passed=False,
            severity="hard",
            reason=f"primary depth {primary_depth*1e6:.0f}ppm is non-positive",
            metrics={"primary_depth": primary_depth},
        )

    # Estimate the flux-level baseline from out-of-transit, out-of-secondary.
    oot_mask = ~primary_mask & ~secondary_mask
    baseline = float(np.mean(lc.flux[oot_mask])) if oot_mask.any() else 1.0

    if secondary_mask.sum() < 5:
        # Not enough points covering phase 0.5 to tell. Soft-skip — for very
        # short-period EBs this can happen with partial phase coverage; we
        # don't want to hard-pass (since secondary might exist) nor hard-fail.
        return GateResult(
            name="secondary",
            passed=True,
            severity="soft",
            reason=f"only {int(secondary_mask.sum())} points near phase 0.5; skipped",
            metrics={
                "primary_depth": primary_depth,
                "secondary_points": int(secondary_mask.sum()),
            },
        )

    secondary_flux = lc.flux[secondary_mask]
    secondary_depth = baseline - float(np.mean(secondary_flux))
    secondary_sigma = float(np.std(secondary_flux, ddof=1) / np.sqrt(secondary_flux.size))

    # Significance of the secondary vs noise.
    secondary_significance = secondary_depth / secondary_sigma if secondary_sigma > 0 else 0.0
    ratio = secondary_depth / primary_depth

    # Fail hard iff BOTH: secondary is statistically significant AND
    # its depth is a meaningful fraction of primary. This avoids killing
    # planets where noise at phase 0.5 happens to dip by chance.
    triggered = (secondary_significance >= secondary_sigma_required) and (ratio >= ratio_threshold)

    return GateResult(
        name="secondary",
        passed=not triggered,
        severity="hard",
        reason=(
            f"secondary depth {secondary_depth*1e6:.0f}ppm vs primary "
            f"{primary_depth*1e6:.0f}ppm (ratio {ratio:.2f}, "
            f"significance {secondary_significance:.1f}σ, "
            f"threshold ratio {ratio_threshold}/sigma {secondary_sigma_required})"
        ),
        metrics={
            "primary_depth": primary_depth,
            "secondary_depth": secondary_depth,
            "depth_ratio": ratio,
            "secondary_significance_sigma": secondary_significance,
            "secondary_points": int(secondary_mask.sum()),
            "baseline_flux": baseline,
        },
    )
