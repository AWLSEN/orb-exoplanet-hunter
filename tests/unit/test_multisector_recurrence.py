"""Unit tests for hunter.multisector.recurrence."""
from __future__ import annotations

import pytest

from hunter.multisector.recurrence import (
    CandidateEphemeris,
    _ephemeris_matches,
    cluster_recurrences,
    n_sectors_for,
)


def _ce(tic: int, sector: int, period: float, t0: float = 0.0) -> CandidateEphemeris:
    return CandidateEphemeris(tic_id=tic, sector=sector, period_days=period, t0_bjd=t0)


def test_match_same_tic_same_period() -> None:
    a = _ce(1, 1, 3.0, 1.0)
    b = _ce(1, 2, 3.003, 1.0)  # within 1%
    assert _ephemeris_matches(a, b, rel_tol=0.01)


def test_match_different_tic_rejected() -> None:
    a = _ce(1, 1, 3.0)
    b = _ce(2, 2, 3.0)
    assert not _ephemeris_matches(a, b, rel_tol=0.01)


def test_match_alias_2x() -> None:
    a = _ce(1, 1, 6.0, 0.0)
    b = _ce(1, 2, 3.0, 0.0)
    assert _ephemeris_matches(a, b, rel_tol=0.01)


def test_match_alias_half() -> None:
    a = _ce(1, 1, 1.5, 0.0)
    b = _ce(1, 2, 3.0, 0.0)
    assert _ephemeris_matches(a, b, rel_tol=0.01)


def test_match_rejects_phase_inconsistency() -> None:
    # Same TIC + same period, but T0 offset is NOT consistent with the period.
    a = _ce(1, 1, 3.0, 0.0)
    b = _ce(1, 2, 3.0, 1.5)  # phase offset 0.5 — should fail
    assert not _ephemeris_matches(a, b, rel_tol=0.01)


def test_match_allows_integer_multiple_of_period_in_t0() -> None:
    a = _ce(1, 1, 3.0, 1.0)
    b = _ce(1, 2, 3.0, 1.0 + 3.0 * 5)  # t0 shifted by 5 full periods
    assert _ephemeris_matches(a, b, rel_tol=0.01)


def test_cluster_single_candidate_forms_cluster_of_one() -> None:
    out = cluster_recurrences([_ce(1, 1, 3.0)])
    assert len(out) == 1
    assert out[0].n_sectors == 1
    assert out[0].tic_id == 1


def test_cluster_groups_two_sector_recurrences() -> None:
    cands = [
        _ce(1, 1, 3.0, 0.0),
        _ce(1, 2, 3.005, 0.0),    # matches cluster A
        _ce(2, 1, 5.0, 0.0),      # different TIC — new cluster
        _ce(1, 3, 3.0, 0.0),      # also cluster A
    ]
    clusters = cluster_recurrences(cands)
    by_tic = {c.tic_id: c for c in clusters}
    assert set(by_tic.keys()) == {1, 2}
    assert by_tic[1].n_sectors == 3
    assert by_tic[2].n_sectors == 1


def test_cluster_separates_different_periods_same_tic() -> None:
    # Same star, but two different period signals: e.g. two planets in the system.
    cands = [
        _ce(1, 1, 3.0, 0.0),
        _ce(1, 2, 5.0, 0.0),
        _ce(1, 3, 3.0, 0.0),
    ]
    clusters = cluster_recurrences(cands)
    assert len(clusters) == 2
    periods = sorted(c.period_days for c in clusters)
    assert abs(periods[0] - 3.0) < 0.1
    assert abs(periods[1] - 5.0) < 0.1


def test_n_sectors_for_returns_cluster_size() -> None:
    a = _ce(1, 1, 3.0, 0.0)
    b = _ce(1, 2, 3.0, 0.0)
    c = _ce(1, 3, 3.0, 0.0)
    d = _ce(2, 1, 5.0, 0.0)
    clusters = cluster_recurrences([a, b, c, d])
    assert n_sectors_for(a, clusters) == 3
    assert n_sectors_for(d, clusters) == 1


def test_n_sectors_for_unknown_returns_one() -> None:
    # Candidate not in any cluster (hasn't been through cluster_recurrences).
    a = _ce(1, 1, 3.0)
    other = [cluster_recurrences([_ce(1, 1, 3.0)])[0]]
    unknown = _ce(999, 5, 1.0)
    assert n_sectors_for(unknown, other) == 1


def test_period_averaging_is_running_mean() -> None:
    # Three candidates at 3.0 / 3.006 / 3.003 → mean 3.003
    cands = [_ce(1, 1, 3.0), _ce(1, 2, 3.006), _ce(1, 3, 3.003)]
    clusters = cluster_recurrences(cands)
    assert len(clusters) == 1
    assert clusters[0].period_days == pytest.approx((3.0 + 3.006 + 3.003) / 3, abs=1e-9)


def test_nonpositive_period_never_matches() -> None:
    a = _ce(1, 1, 3.0, 0.0)
    b = _ce(1, 2, 0.0, 0.0)
    assert not _ephemeris_matches(a, b, rel_tol=0.01)
    assert not _ephemeris_matches(b, a, rel_tol=0.01)
