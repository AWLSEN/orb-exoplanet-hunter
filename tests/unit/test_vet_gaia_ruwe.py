"""Unit tests for hunter.vet.gaia_ruwe — no Gaia archive calls; injected
lookup functions + cache dicts exercise every path."""
from __future__ import annotations

from pathlib import Path

import pytest

from hunter.ingest.tess import synthetic
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.gaia_ruwe import check_gaia_ruwe, load_ruwe_cache, save_ruwe_cache


def _mock_result(tic_id: int) -> TransitSearchResult:
    return TransitSearchResult(
        period_days=3.0,
        t0_bjd=1.0,
        depth=0.003,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=5,
        tic_id=tic_id,
        sector=1,
    )


def test_passes_single_star_low_ruwe() -> None:
    lc = synthetic(tic_id=11111, sector=1, noise_ppm=100)
    gate = check_gaia_ruwe(
        lc, _mock_result(11111),
        cache={11111: 1.02},  # clean single star
    )
    assert gate.passed
    assert gate.severity == "hard"
    assert gate.metrics["ruwe"] == 1.02
    assert gate.metrics["cache_hit"] is True


def test_fails_unresolved_binary_high_ruwe() -> None:
    lc = synthetic(tic_id=22222, sector=1, noise_ppm=100)
    gate = check_gaia_ruwe(
        lc, _mock_result(22222),
        cache={22222: 2.3},  # clearly non-single
    )
    assert not gate.passed
    assert gate.severity == "hard"
    assert "unresolved binary" in gate.reason


def test_boundary_exactly_at_threshold_fails() -> None:
    """RUWE == threshold should fail (threshold is a strict less-than)."""
    lc = synthetic(tic_id=3, sector=1, noise_ppm=100)
    gate = check_gaia_ruwe(lc, _mock_result(3), cache={3: 1.4})
    assert not gate.passed


def test_missing_from_cache_uses_lookup() -> None:
    lc = synthetic(tic_id=44444, sector=1, noise_ppm=100)
    lookups: list[int] = []

    def fake_lookup(tic: int):
        lookups.append(tic)
        return 1.08

    gate = check_gaia_ruwe(
        lc, _mock_result(44444),
        cache={},
        lookup=fake_lookup,
    )
    assert lookups == [44444]
    assert gate.passed
    assert gate.metrics["cache_hit"] is False
    # Lookups from tests should NOT be written back (lookup arg overrides).
    # No disk side-effect to verify in this path because tmp paths aren't used.


def test_lookup_returns_none_soft_skips() -> None:
    lc = synthetic(tic_id=55555, sector=1, noise_ppm=100)
    gate = check_gaia_ruwe(
        lc, _mock_result(55555),
        cache={},
        lookup=lambda _tic: None,
    )
    assert gate.passed
    assert gate.severity == "soft"
    assert "no Gaia" in gate.reason


def test_load_ruwe_cache_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_ruwe_cache(tmp_path / "missing.csv") == {}


def test_load_ruwe_cache_parses_valid_rows(tmp_path: Path) -> None:
    p = tmp_path / "ruwe.csv"
    p.write_text("tic_id,ruwe\n100,1.02\n200,1.55\n300,0.95\n")
    out = load_ruwe_cache(p)
    assert out == {100: 1.02, 200: 1.55, 300: 0.95}


def test_load_ruwe_cache_accepts_uppercase_columns(tmp_path: Path) -> None:
    p = tmp_path / "ruwe.csv"
    p.write_text("TIC,RUWE\n42,1.12\n")
    assert load_ruwe_cache(p) == {42: 1.12}


def test_save_and_reload_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "ruwe.csv"
    data = {1: 1.001, 2: 2.5, 3: 0.98}
    save_ruwe_cache(data, p)
    assert load_ruwe_cache(p) == data


def test_load_ruwe_cache_skips_malformed(tmp_path: Path) -> None:
    p = tmp_path / "ruwe.csv"
    p.write_text("tic_id,ruwe\nabc,def\n7,1.3\n-1,2.0\n8,not_a_float\n")
    out = load_ruwe_cache(p)
    # Only the 7->1.3 row survives.
    assert out == {7: 1.3}
