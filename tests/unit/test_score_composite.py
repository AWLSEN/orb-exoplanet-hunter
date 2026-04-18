"""Unit tests for hunter.score.composite."""
from __future__ import annotations

import pytest

from hunter.search.tls_search import TransitSearchResult
from hunter.score.composite import (
    W_DEPTH,
    W_RECURRENCE,
    W_SDE,
    W_TRANSITS,
    W_VET_CLEAN,
    _depth_component,
    _recurrence_component,
    _sde_component,
    _transits_component,
    _vet_clean_component,
    score_candidate,
)
from hunter.vet.types import GateResult, VetReport


def _passing_vet(n_soft: int = 0) -> VetReport:
    rep = VetReport()
    rep.add(GateResult(name="odd_even", passed=True, severity="hard", reason="ok"))
    for i in range(n_soft):
        rep.add(GateResult(name=f"soft{i}", passed=False, severity="soft", reason="warn"))
    return rep


def _failing_vet() -> VetReport:
    rep = VetReport()
    rep.add(GateResult(name="secondary", passed=False, severity="hard", reason="EB"))
    return rep


def _r(sde: float, n_transits: int = 6, depth: float = 0.002) -> TransitSearchResult:
    return TransitSearchResult(
        period_days=3.0,
        t0_bjd=1.0,
        depth=depth,
        duration_days=0.1,
        sde=sde,
        snr=sde,
        n_transits=n_transits,
        tic_id=1,
        sector=1,
    )


def test_weights_sum_to_one() -> None:
    total = W_SDE + W_RECURRENCE + W_TRANSITS + W_DEPTH + W_VET_CLEAN
    assert total == pytest.approx(1.0, abs=1e-9)


def test_sde_component_monotone() -> None:
    xs = [8, 10, 15, 20, 30]
    ys = [_sde_component(x) for x in xs]
    assert all(ys[i] < ys[i + 1] for i in range(len(ys) - 1))


def test_recurrence_component_tiers() -> None:
    assert _recurrence_component(1) == 0.0
    assert _recurrence_component(2) == 0.6
    assert _recurrence_component(3) == 0.9
    assert _recurrence_component(5) <= 1.0
    assert _recurrence_component(5) > 0.9


def test_transits_component_saturates() -> None:
    # 20 transits is essentially saturated.
    assert _transits_component(20) > 0.99


def test_depth_component_peaks_mid_range() -> None:
    # Peak around 2000 ppm (0.002), worse at very shallow (50 ppm) and very deep (5%).
    peak = _depth_component(0.002)
    shallow = _depth_component(0.00005)
    deep = _depth_component(0.05)
    assert peak > shallow
    assert peak > deep
    assert _depth_component(0.0) == 0.0


def test_vet_clean_component_penalizes_warnings() -> None:
    rep0 = _passing_vet(n_soft=0)
    rep2 = _passing_vet(n_soft=2)
    rep10 = _passing_vet(n_soft=10)
    assert _vet_clean_component(rep0) == 1.0
    assert _vet_clean_component(rep2) == pytest.approx(0.7, abs=1e-9)
    assert _vet_clean_component(rep10) == 0.0


def test_score_of_rejected_candidate_is_zero() -> None:
    s = score_candidate(_r(sde=20), _failing_vet())
    assert s.value == 0.0
    assert s.tier == "rejected"


def test_score_weak_candidate_below_moderate_threshold() -> None:
    # Low SDE, 1 sector, few transits.
    s = score_candidate(_r(sde=8.5, n_transits=2, depth=0.0001), _passing_vet())
    assert 0.0 < s.value < 0.4
    assert s.tier == "weak"


def test_score_strong_candidate_no_recurrence() -> None:
    s = score_candidate(_r(sde=25, n_transits=10, depth=0.002), _passing_vet())
    # Single sector, no recurrence, so below 0.7 ceiling
    # But SDE+transits+depth+vet-clean sum up — we want a moderate-to-strong.
    assert 0.4 <= s.value
    assert s.tier in {"moderate", "strong"}


def test_score_confirmed_with_multi_sector_recurrence() -> None:
    s = score_candidate(
        _r(sde=25, n_transits=10, depth=0.002),
        _passing_vet(),
        n_sectors_confirmed=3,
    )
    assert s.value >= 0.7
    assert s.tier == "confirmed"


def test_score_is_bounded_0_1() -> None:
    # Extreme case with everything perfect — should stay <= 1.
    s = score_candidate(
        _r(sde=1000, n_transits=100, depth=0.002),
        _passing_vet(),
        n_sectors_confirmed=10,
    )
    assert 0.0 <= s.value <= 1.0


def test_soft_warnings_lower_score() -> None:
    clean = score_candidate(_r(sde=20), _passing_vet(n_soft=0))
    warned = score_candidate(_r(sde=20), _passing_vet(n_soft=2))
    assert warned.value < clean.value
