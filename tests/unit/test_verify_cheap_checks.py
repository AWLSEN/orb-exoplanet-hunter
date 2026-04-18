"""Unit tests for verification.cheap_checks."""
from __future__ import annotations

from hunter.output.candidate import Candidate
from verification.cheap_checks import check_depth_distribution, check_ephemeris_consistency


def _cand(tic: int, sector: int, period: float, t0: float, depth: float = 0.002, tier: str = "strong") -> Candidate:
    return Candidate(
        tic_id=tic,
        sector=sector,
        period_days=period,
        t0_bjd=t0,
        depth=depth,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=5,
        score=0.7,
        tier=tier,
        n_sectors_confirmed=1,
    )


def test_depth_distribution_soft_skips_when_too_few_candidates() -> None:
    cands = [_cand(1, 1, 3.0, 1.0)]
    r = check_depth_distribution(cands, min_candidates=5)
    assert r.passed
    assert r.severity == "soft"
    assert "skipped" in r.reason


def test_depth_distribution_passes_with_spread() -> None:
    # Depths spanning 30 ppm to 30000 ppm = 3 orders of magnitude → stdev ~1.1.
    depths = [0.00003, 0.0001, 0.0005, 0.001, 0.003, 0.01, 0.03]
    cands = [_cand(i, 1, 3.0, 1.0, depth=d) for i, d in enumerate(depths)]
    r = check_depth_distribution(cands, min_orders_of_magnitude=0.8)
    assert r.passed
    assert r.metrics["log_depth_stdev"] > 0.8


def test_depth_distribution_fails_when_bunched() -> None:
    # All depths tight around 1000 ppm — systematics-looking.
    cands = [_cand(i, 1, 3.0, 1.0, depth=0.001 + i * 1e-6) for i in range(10)]
    r = check_depth_distribution(cands, min_orders_of_magnitude=0.5)
    assert not r.passed


def test_depth_distribution_ignores_rejected() -> None:
    cands = [
        _cand(i, 1, 3.0, 1.0, depth=0.001, tier="rejected") for i in range(10)
    ] + [_cand(99, 1, 3.0, 1.0, depth=0.005)]
    r = check_depth_distribution(cands, min_candidates=1)
    assert r.metrics["n_candidates"] == 1


def test_ephemeris_consistency_soft_skips_with_no_multisector() -> None:
    cands = [_cand(1, 1, 3.0, 1.0), _cand(2, 1, 5.0, 2.0)]
    r = check_ephemeris_consistency(cands)
    assert r.passed
    assert r.severity == "soft"


def test_ephemeris_consistency_passes_for_consistent_pair() -> None:
    # Same star, same period; sector 2 T0 = sector 1 T0 + 10*P.
    a = _cand(1, 1, 3.0, 1.0)
    b = _cand(1, 2, 3.0, 1.0 + 10 * 3.0)
    r = check_ephemeris_consistency([a, b])
    assert r.passed
    assert r.metrics["pairings_checked"] == 1
    assert r.metrics["worst_phase_err"] < 0.01


def test_ephemeris_consistency_fails_for_inconsistent_pair() -> None:
    # Same TIC + period but T0_B is shifted by 0.5*P — phase inconsistent.
    a = _cand(1, 1, 3.0, 1.0)
    b = _cand(1, 2, 3.0, 1.0 + 0.5 * 3.0)
    r = check_ephemeris_consistency([a, b], phase_sigma_threshold=0.1)
    assert not r.passed
    assert r.metrics["worst_phase_err"] >= 0.1


def test_ephemeris_consistency_buckets_by_period_bucket() -> None:
    # Same TIC, different periods → different groups; single-group pairs skipped.
    a = _cand(1, 1, 3.00, 1.0)
    b = _cand(1, 2, 5.00, 2.0)
    r = check_ephemeris_consistency([a, b])
    assert r.passed
    assert r.metrics["multi_sector_groups"] == 0
