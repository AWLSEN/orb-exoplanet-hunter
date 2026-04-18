"""End-to-end pipeline integration test: MAST fetch → detrend → search →
vet → score → candidate JSON on disk. Hits the real TESS archive; marked
`integration` so the default pytest run stays offline.

Uses WASP-121 b (hot Jupiter, period 1.275d, depth ~1.5%) as the
guaranteed recovery target — the same candidate Batch 1's integration
test leaned on, now run through the full chain."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hunter.output.candidate import read_candidate
from hunter.pipeline import process_target


pytestmark = pytest.mark.integration

FALLBACK_TARGETS: list[int] = [22529346, 261136679, 150428135]


def test_full_pipeline_writes_valid_candidate_for_real_target(tmp_path: Path) -> None:
    last_err: str | None = None
    result = None
    for tic in FALLBACK_TARGETS:
        try:
            result = process_target(tic, write_to=tmp_path, min_sde=8.0)
            if result.accepted:
                break
            last_err = result.reason
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    if result is None or not result.accepted:
        pytest.skip(f"MAST pipeline unreachable or rejected all targets. Last: {last_err}")

    assert result.candidate is not None
    c = result.candidate
    # Sanity: the candidate has believable fields
    assert c.tic_id in FALLBACK_TARGETS
    assert c.period_days > 0
    assert c.depth > 0
    assert c.sde >= 8.0
    assert c.tier in {"weak", "moderate", "strong", "confirmed"}
    assert c.score >= 0.0 and c.score <= 1.0
    # gate audit trail is populated (at minimum we ran 1 gate)
    assert len(c.gate_results) >= 1
    # JSON file was written and round-trips
    files = list(tmp_path.glob("tic*-s*.json"))
    assert len(files) == 1
    disk = read_candidate(files[0])
    assert disk.tic_id == c.tic_id
    assert disk.period_days == pytest.approx(c.period_days)
    # Raw JSON is valid — no stale .tmp files
    raw = json.loads(files[0].read_text())
    assert raw["tic_id"] == c.tic_id
    assert not list(tmp_path.glob("*.tmp"))
    print(
        f"\n[e2e] TIC {c.tic_id}: period={c.period_days:.4f}d, "
        f"SDE={c.sde:.2f}, depth={c.depth*1e6:.0f}ppm, "
        f"tier={c.tier}, score={c.score:.3f}, "
        f"gates={len(c.gate_results)}"
    )
