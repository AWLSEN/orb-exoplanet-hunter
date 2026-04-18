"""Unit tests for weekly summary generator — injected Claude caller, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.output.candidate import Candidate
from hunter.output.weekly_summary import build_input_json, generate_weekly_summary


def _c(tic: int, tier: str = "strong", score: float = 0.7) -> Candidate:
    return Candidate(
        tic_id=tic, sector=1, period_days=3.0, t0_bjd=1.0, depth=0.002,
        duration_days=0.09, sde=15.0, snr=15.0, n_transits=6, score=score,
        tier=tier, n_sectors_confirmed=1, sectors_seen=[1],
    )


def test_build_input_serializes_counts_and_top3() -> None:
    cands = [_c(i, tier="strong", score=0.5 + i * 0.1) for i in range(5)]
    cands.append(_c(99, tier="confirmed", score=0.9))
    payload = json.loads(build_input_json(cands, {"passed": True}))
    assert payload["counts"]["total"] == 6
    assert payload["counts"]["confirmed"] == 1
    assert payload["counts"]["strong"] == 5
    # top3 ordered by score desc.
    scores = [c["score"] for c in payload["top3"]]
    assert scores == sorted(scores, reverse=True)
    assert len(payload["top3"]) == 3


def test_build_input_empty_candidates() -> None:
    payload = json.loads(build_input_json([], None))
    assert payload["counts"]["total"] == 0
    assert payload["top3"] == []


def test_generate_writes_markdown(tmp_path: Path) -> None:
    called: list[tuple[str, str]] = []

    def fake(system: str, user: str) -> str:
        called.append((system, user))
        return "This week: 1 candidate. Pipeline clean."

    out = generate_weekly_summary(
        [_c(42)],
        {"passed": True, "results": []},
        caller=fake,
        summary_dir=tmp_path,
        week_tag="2026-W16",
    )
    assert out.exists()
    body = out.read_text()
    assert "Weekly summary — 2026-W16" in body
    assert "This week: 1 candidate. Pipeline clean." in body
    assert len(called) == 1


def test_generate_preserves_halted_signal_in_payload(tmp_path: Path) -> None:
    captured: dict = {}

    def fake(system: str, user: str) -> str:
        captured["user"] = user
        return "halted."

    generate_weekly_summary(
        [],
        {"passed": False, "hard_failures": [{"name": "known_planets", "reason": "failed"}]},
        caller=fake,
        summary_dir=tmp_path,
        week_tag="2026-W16",
    )
    payload = json.loads(captured["user"])
    assert payload["pipeline_health"]["passed"] is False


def test_filename_is_week_tag(tmp_path: Path) -> None:
    out = generate_weekly_summary(
        [], {"passed": True},
        caller=lambda s, u: "ok",
        summary_dir=tmp_path,
        week_tag="2030-W01",
    )
    assert out.name == "2030-W01.md"
