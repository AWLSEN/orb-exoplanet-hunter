"""Unit tests for verification.orchestrator — full end-to-end report
persistence + HALT semantics.

Uses tmp dirs + small health-check inputs so no MAST / no real
TLS runs happen in this test file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hunter.output.candidate import Candidate, write_candidate
from verification.orchestrator import (
    HALT_FILENAME,
    REPORT_FILENAME,
    clear_halt,
    is_halted,
    load_last_report,
    run_all,
    run_cheap,
)
from verification.types import HealthResult


def _cand(tic: int, sector: int, period: float, t0: float, depth: float) -> Candidate:
    return Candidate(
        tic_id=tic, sector=sector, period_days=period, t0_bjd=t0, depth=depth,
        duration_days=0.1, sde=15.0, snr=15.0, n_transits=5, score=0.7,
        tier="strong", n_sectors_confirmed=1,
    )


def test_run_cheap_with_clean_candidates_passes() -> None:
    cands = [
        _cand(1, 1, 3.0, 1.0, 0.00003),
        _cand(2, 1, 5.0, 1.0, 0.0001),
        _cand(3, 1, 7.0, 1.0, 0.0005),
        _cand(4, 1, 9.0, 1.0, 0.003),
        _cand(5, 1, 11.0, 1.0, 0.01),
        _cand(6, 1, 13.0, 1.0, 0.03),
    ]
    report = run_cheap(cands)
    assert report.passed
    # ran_at stamped.
    assert all(r.ran_at for r in report.results)


def test_run_all_persists_report_without_expensive(tmp_path: Path) -> None:
    # Empty candidate DB → cheap checks soft-skip, no hard fails.
    os.environ["HUNTER_CANDIDATE_DIR"] = str(tmp_path / "candidates")
    try:
        report = run_all(health_dir=tmp_path, enable_expensive=False)
    finally:
        os.environ.pop("HUNTER_CANDIDATE_DIR", None)

    assert report.passed
    report_file = tmp_path / REPORT_FILENAME
    assert report_file.exists()
    payload = json.loads(report_file.read_text())
    assert payload["passed"] is True
    assert "results" in payload
    assert not (tmp_path / HALT_FILENAME).exists()


def test_halt_file_written_on_hard_failure(tmp_path: Path, monkeypatch) -> None:
    # Patch one check to hard-fail; empty candidate DB.
    monkeypatch.setenv("HUNTER_CANDIDATE_DIR", str(tmp_path / "candidates"))

    def _bad(_cands):
        return HealthResult(name="depth_distribution", passed=False, severity="hard", reason="injected fail")

    with patch("verification.orchestrator.check_depth_distribution", _bad):
        report = run_all(health_dir=tmp_path, enable_expensive=False)

    assert not report.passed
    assert len(report.hard_failures) >= 1
    halt = tmp_path / HALT_FILENAME
    assert halt.exists()
    halt_payload = json.loads(halt.read_text())
    assert "halted_at" in halt_payload
    assert any("injected fail" in r for r in halt_payload["reasons"])


def test_existing_halt_is_not_overwritten_on_subsequent_run(tmp_path: Path) -> None:
    halt = tmp_path / HALT_FILENAME
    halt.write_text(json.dumps({"halted_at": "2026-04-18T00:00:00Z", "reasons": ["previous fail"]}))

    def _bad(_cands):
        return HealthResult(name="depth_distribution", passed=False, severity="hard", reason="new fail")

    with patch("verification.orchestrator.check_depth_distribution", _bad):
        run_all(health_dir=tmp_path, enable_expensive=False)

    # Halt file still carries the ORIGINAL reason — no overwrite.
    assert "previous fail" in halt.read_text()


def test_is_halted_and_clear_halt_round_trip(tmp_path: Path) -> None:
    assert is_halted(tmp_path) is False
    (tmp_path / HALT_FILENAME).write_text(json.dumps({"halted_at": "now", "reasons": []}))
    assert is_halted(tmp_path) is True
    assert clear_halt(tmp_path) is True
    assert is_halted(tmp_path) is False
    # Clearing again is a no-op returning False.
    assert clear_halt(tmp_path) is False


def test_load_last_report_missing_returns_none(tmp_path: Path) -> None:
    assert load_last_report(tmp_path) is None


def test_load_last_report_round_trip(tmp_path: Path) -> None:
    os.environ["HUNTER_CANDIDATE_DIR"] = str(tmp_path / "candidates")
    try:
        run_all(health_dir=tmp_path, enable_expensive=False)
    finally:
        os.environ.pop("HUNTER_CANDIDATE_DIR", None)
    loaded = load_last_report(tmp_path)
    assert loaded is not None
    assert loaded["passed"] is True
    assert "results" in loaded


def test_pass_does_not_clear_existing_halt(tmp_path: Path) -> None:
    halt = tmp_path / HALT_FILENAME
    halt.write_text(json.dumps({"halted_at": "earlier", "reasons": []}))

    os.environ["HUNTER_CANDIDATE_DIR"] = str(tmp_path / "candidates")
    try:
        report = run_all(health_dir=tmp_path, enable_expensive=False)
    finally:
        os.environ.pop("HUNTER_CANDIDATE_DIR", None)
    assert report.passed
    # Halt is sticky — a passing run doesn't auto-clear.
    assert halt.exists()
