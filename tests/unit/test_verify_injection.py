"""Unit tests for verification.injection.

Uses a small 2-cell grid with 1 trial each → 2 TLS runs (~2 seconds).
Full DEFAULT_GRID (18 TLS runs, 1+ min) is exercised by the integration
test file to keep unit runtime sane."""
from __future__ import annotations

from verification.injection import (
    CellResult,
    GridCell,
    check_injection_recovery,
)


def test_small_grid_recovers_expected_cells() -> None:
    # Deep + short transits should recover with a single trial.
    grid = (
        GridCell(period_days=2.0, depth=0.01, n_trials=1),
        GridCell(period_days=3.0, depth=0.01, n_trials=1),
    )
    result = check_injection_recovery(grid=grid, min_recovery_rate=0.5)
    assert result.passed, f"deep + short injections should recover; reason: {result.reason}"
    assert result.metrics["overall_rate"] == 1.0
    assert len(result.metrics["cells"]) == 2


def test_fails_when_unrecoverable_depth() -> None:
    # 10 ppm depth is well below any pipeline's sensitivity; sweep must fail.
    grid = (GridCell(period_days=2.0, depth=0.00001, n_trials=1),)
    result = check_injection_recovery(grid=grid, min_recovery_rate=0.5)
    assert not result.passed
    assert "below" in result.reason


def test_cell_result_rate_handles_zero_trials() -> None:
    c = CellResult(period_days=1.0, depth=0.001, trials=0, recoveries=0)
    assert c.rate == 0.0


def test_metrics_cells_carry_rate() -> None:
    grid = (GridCell(period_days=2.5, depth=0.01, n_trials=1),)
    result = check_injection_recovery(grid=grid, min_recovery_rate=0.0)  # always passes
    cell_metric = result.metrics["cells"][0]
    assert cell_metric["period_days"] == 2.5
    assert cell_metric["depth"] == 0.01
    assert "rate" in cell_metric
    assert 0.0 <= cell_metric["rate"] <= 1.0
