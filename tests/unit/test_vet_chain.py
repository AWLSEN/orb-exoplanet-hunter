"""Unit tests for the vet chain orchestrator."""
from __future__ import annotations

import numpy as np
import pytest

from hunter.ingest.tess import LightCurve, synthetic
from hunter.search.tls_search import TransitSearchResult
from hunter.vet import Gate, VetReport, run_vet_chain
from hunter.vet.types import GateResult


def _result(tic: int = 1, period: float = 3.0) -> TransitSearchResult:
    return TransitSearchResult(
        period_days=period,
        t0_bjd=1.0,
        depth=0.003,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=5,
        tic_id=tic,
        sector=1,
    )


def _gate(name: str, passed: bool, severity: str = "hard") -> Gate:
    def impl(_lc, _r):
        return GateResult(name=name, passed=passed, severity=severity, reason=f"mock {name}={passed}")
    impl.__name__ = f"mock_{name}"
    return impl


def test_all_pass_chain_has_passed_report() -> None:
    lc = synthetic(tic_id=1, noise_ppm=100)
    report = run_vet_chain(
        lc, _result(),
        gates=(_gate("a", True), _gate("b", True), _gate("c", True)),
    )
    assert report.passed
    assert len(report.gate_results) == 3
    assert report.hard_failures == []
    assert report.soft_failures == []


def test_stops_on_first_hard_fail() -> None:
    lc = synthetic(tic_id=1, noise_ppm=100)
    report = run_vet_chain(
        lc, _result(),
        gates=(_gate("a", True), _gate("b", False, "hard"), _gate("c", True)),
    )
    # Only ran 2 gates.
    assert len(report.gate_results) == 2
    assert not report.passed
    assert len(report.hard_failures) == 1
    assert report.hard_failures[0].name == "b"


def test_soft_fail_does_not_stop_chain() -> None:
    lc = synthetic(tic_id=1, noise_ppm=100)
    report = run_vet_chain(
        lc, _result(),
        gates=(_gate("a", False, "soft"), _gate("b", True), _gate("c", False, "soft")),
    )
    assert report.passed  # no hard fails
    assert len(report.gate_results) == 3  # all ran
    assert len(report.soft_failures) == 2
    assert [g.name for g in report.soft_failures] == ["a", "c"]


def test_stop_on_hard_fail_flag_honored() -> None:
    lc = synthetic(tic_id=1, noise_ppm=100)
    report = run_vet_chain(
        lc, _result(),
        gates=(_gate("a", False, "hard"), _gate("b", True), _gate("c", True)),
        stop_on_hard_fail=False,
    )
    # All 3 ran because we disabled short-circuit.
    assert len(report.gate_results) == 3
    assert not report.passed


def test_crashing_gate_becomes_hard_fail() -> None:
    def exploder(_lc, _r):
        raise RuntimeError("kaboom")
    exploder.__name__ = "exploder"

    lc = synthetic(tic_id=1, noise_ppm=100)
    report = run_vet_chain(
        lc, _result(),
        gates=(exploder,),
    )
    assert not report.passed
    assert len(report.hard_failures) == 1
    assert "RuntimeError" in report.hard_failures[0].reason
    assert "kaboom" in report.hard_failures[0].reason


def test_default_gates_run_against_clean_synthetic() -> None:
    """Smoke test: run the real default chain against a clean synthetic LC.

    Expect it to pass most gates (some may soft-skip due to limited phase
    coverage). The chain should complete without exceptions and not hard-fail.
    """
    # Moderate-length LC with a real periodic transit injection so odd/even
    # and secondary have something to chew on.
    lc = synthetic(
        tic_id=9999999,  # unlikely to be in TOI catalog
        sector=1,
        duration_days=25,
        noise_ppm=150,
        period_days=3.5,
        depth=0.003,
        transit_duration_days=0.1,
        t0_days=1.0,
        rng_seed=111,
    )
    r = TransitSearchResult(
        period_days=3.5,
        t0_bjd=1.0,
        depth=0.003,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=6,
        tic_id=9999999,
        sector=1,
    )
    report = run_vet_chain(lc, r)
    # With the clean synthetic + nonexistent TIC + no Gaia cache, we expect
    # odd_even to pass hard, secondary to pass hard, ephemeris "no match"
    # (soft pass), gaia_ruwe soft-skip (no lookup/cache available).
    assert report.passed, f"clean synthetic should pass, got {[g.reason for g in report.hard_failures]}"


def test_vet_report_aggregates_correctly() -> None:
    rep = VetReport()
    rep.add(GateResult(name="a", passed=True, severity="hard", reason="ok"))
    rep.add(GateResult(name="b", passed=False, severity="soft", reason="warn"))
    rep.add(GateResult(name="c", passed=False, severity="hard", reason="block"))
    assert not rep.passed
    assert [g.name for g in rep.hard_failures] == ["c"]
    assert [g.name for g in rep.soft_failures] == ["b"]
