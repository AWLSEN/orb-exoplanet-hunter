"""Unit tests for hunter.hunt — CLI parsing + batch runner with mocked pipeline."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hunter.hunt import load_tics, run_hunt
from hunter.pipeline import PipelineResult


def test_load_tics_from_csv() -> None:
    assert load_tics("1,2,3", None) == [1, 2, 3]
    assert load_tics("42 , 99 ,", None) == [42, 99]


def test_load_tics_from_file(tmp_path: Path) -> None:
    f = tmp_path / "tics.txt"
    f.write_text("# comment\n42\n\n99\n100\n# trailing comment\n")
    assert load_tics(None, str(f)) == [42, 99, 100]


def test_load_tics_missing_source_raises() -> None:
    with pytest.raises(SystemExit):
        load_tics(None, None)


def test_load_tics_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_tics(None, "/does/not/exist.txt")


def test_run_hunt_skips_when_halted(tmp_path: Path) -> None:
    (tmp_path / "PIPELINE_HALT").write_text('{}')
    out = run_hunt([1, 2], data_dir=tmp_path)
    assert out["skipped"] is True


def test_run_hunt_ignores_halt_when_disabled(tmp_path: Path) -> None:
    (tmp_path / "PIPELINE_HALT").write_text('{}')

    fake_result = PipelineResult(
        tic_id=1, sector=1, accepted=False, reason="noop", candidate=None, search=None, vet=None,
    )
    with patch("hunter.hunt.process_target", return_value=fake_result):
        out = run_hunt([1], data_dir=tmp_path, skip_when_halted=False)
    assert "skipped" not in out
    assert out["total"] == 1


def test_run_hunt_counts_accepted_rejected_errors(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake(tic: int, **kw):
        calls.append(tic)
        if tic == 999:
            raise RuntimeError("boom")
        return PipelineResult(
            tic_id=tic, sector=1,
            accepted=(tic % 2 == 0),
            reason="accepted" if tic % 2 == 0 else "rejected",
        )

    with patch("hunter.hunt.process_target", side_effect=fake):
        out = run_hunt([2, 3, 4, 999], data_dir=tmp_path)
    assert out["total"] == 4
    assert out["accepted"] == 2
    assert out["rejected"] == 1
    assert out["errors"] == 1
    assert calls == [2, 3, 4, 999]
    # per_target has one entry per input.
    assert len(out["per_target"]) == 4
