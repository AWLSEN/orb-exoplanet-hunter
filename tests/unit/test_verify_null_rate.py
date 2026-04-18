"""Unit tests for verification.null_rate.

A small number of trials (3) keeps runtime ~25 seconds. Real scheduled
runs use the default 20 trials.
"""
from __future__ import annotations

from verification.null_rate import check_null_rate


def test_null_data_does_not_trigger_false_positives_at_high_threshold() -> None:
    # SDE threshold 15.0 is very strict — almost no pure-noise LC should cross.
    # With only 3 trials the rate will be 0/3 → 0%, well within the 0.05 budget.
    result = check_null_rate(
        n_trials=3,
        sde_threshold=15.0,
        max_fp_rate=0.34,
        seed_base=1000,
    )
    assert result.passed, f"clean null data at SDE>=15 should pass (reason: {result.reason})"
    assert result.metrics["triggers"] == 0
    assert result.metrics["fp_rate"] == 0.0
    assert result.metrics["effective"] == 3


def test_low_threshold_likely_fails_budget() -> None:
    # SDE>=1 is so permissive every TLS run produces something.
    result = check_null_rate(
        n_trials=3,
        sde_threshold=1.0,
        max_fp_rate=0.01,
        seed_base=2000,
    )
    assert not result.passed
    assert "budget" in result.reason or "rate" in result.reason


def test_metrics_populated() -> None:
    result = check_null_rate(
        n_trials=2,
        sde_threshold=15.0,
        max_fp_rate=0.5,
        seed_base=3000,
    )
    for k in ("n_trials", "effective", "triggers", "errors", "fp_rate", "max_sde_seen"):
        assert k in result.metrics
