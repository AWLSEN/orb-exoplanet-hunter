"""Unit tests for hunter.vet.ephemeris_match."""
from __future__ import annotations

from pathlib import Path

import pytest

from hunter.ingest.tess import synthetic
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.ephemeris_match import ToiEntry, _period_matches, check_ephemeris, load_catalog


def _mock_result(tic_id: int, period: float) -> TransitSearchResult:
    return TransitSearchResult(
        period_days=period,
        t0_bjd=1.0,
        depth=0.003,
        duration_days=0.1,
        sde=15.0,
        snr=15.0,
        n_transits=5,
        tic_id=tic_id,
        sector=1,
    )


def test_period_matches_within_tolerance() -> None:
    assert _period_matches(1.0, 1.005, rel_tol=0.01)
    assert _period_matches(1.0, 0.995, rel_tol=0.01)
    assert not _period_matches(1.0, 1.1, rel_tol=0.01)


def test_period_matches_detects_2x_and_half_aliases() -> None:
    # Our candidate locked onto twice the true period.
    assert _period_matches(2.0, 1.0)
    # Our candidate locked onto half the true period.
    assert _period_matches(0.5, 1.0)


def test_period_match_rejects_nonpositive_known() -> None:
    assert not _period_matches(1.0, 0.0)
    assert not _period_matches(1.0, -5.0)


def test_check_matches_known_toi_soft_fails() -> None:
    lc = synthetic(tic_id=123456, sector=1, noise_ppm=100)
    catalog = [
        ToiEntry(tic_id=123456, toi_name="TOI-123.01", period_days=3.0, t0_bjd=1.5),
        ToiEntry(tic_id=999999, toi_name="TOI-X.01", period_days=2.5, t0_bjd=1.0),
    ]
    r = _mock_result(123456, 3.002)  # matches within 1%
    gate = check_ephemeris(lc, r, catalog=catalog)
    assert not gate.passed
    assert gate.severity == "soft"
    assert gate.metrics["matched_toi"] == "TOI-123.01"
    assert "TOI-123.01" in gate.reason


def test_check_no_match_for_novel_candidate_passes() -> None:
    lc = synthetic(tic_id=111, sector=1, noise_ppm=100)
    catalog = [
        ToiEntry(tic_id=222, toi_name="TOI-222.01", period_days=5.0, t0_bjd=1.0),
    ]
    r = _mock_result(111, 3.0)
    gate = check_ephemeris(lc, r, catalog=catalog)
    assert gate.passed
    assert gate.metrics["match_count"] == 0


def test_check_matches_2x_alias_against_known_period() -> None:
    # Our candidate found 6.0 days; true planet is 3.0 days. Still a match.
    lc = synthetic(tic_id=55555, sector=1, noise_ppm=100)
    catalog = [ToiEntry(tic_id=55555, toi_name="TOI-5.01", period_days=3.0, t0_bjd=1.0)]
    r = _mock_result(55555, 6.0)
    gate = check_ephemeris(lc, r, catalog=catalog)
    assert not gate.passed  # still matches (aliased)


def test_check_ignores_different_tic() -> None:
    # Same period but wrong TIC — not a match (different star).
    lc = synthetic(tic_id=111, sector=1, noise_ppm=100)
    catalog = [ToiEntry(tic_id=222, toi_name="TOI-222.01", period_days=3.0, t0_bjd=1.0)]
    r = _mock_result(111, 3.0)
    gate = check_ephemeris(lc, r, catalog=catalog)
    assert gate.passed


def test_load_catalog_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_catalog(tmp_path / "does-not-exist.csv") == []


def test_load_catalog_reads_tic_id_period_t0(tmp_path: Path) -> None:
    p = tmp_path / "toi.csv"
    p.write_text(
        "tic_id,toi_name,period_days,t0_bjd,depth_ppm,disposition\n"
        "123456,TOI-123.01,3.5,1.0,500,CP\n"
        "789,TOI-789.02,0.8,2.5,,PC\n"
    )
    out = load_catalog(p)
    assert len(out) == 2
    assert out[0].tic_id == 123456
    assert out[0].toi_name == "TOI-123.01"
    assert out[0].period_days == 3.5
    assert out[0].depth_ppm == 500
    assert out[0].disposition == "CP"
    assert out[1].depth_ppm is None


def test_load_catalog_tolerates_nasa_archive_column_names(tmp_path: Path) -> None:
    # NASA Exoplanet Archive TOI table uses different column names.
    p = tmp_path / "toi.csv"
    p.write_text(
        "TIC ID,toi,pl_orbper,pl_tranmid,pl_trandep,tfopwg_disp\n"
        "42,TOI-42.01,6.28,3.0,420,CP\n"
    )
    out = load_catalog(p)
    assert len(out) == 1
    assert out[0].tic_id == 42
    assert out[0].period_days == 6.28


def test_load_catalog_skips_malformed_rows(tmp_path: Path) -> None:
    p = tmp_path / "toi.csv"
    p.write_text(
        "tic_id,toi_name,period_days,t0_bjd\n"
        "abc,bad_row,not_a_number,0\n"
        "123,TOI-123.01,3.5,1.0\n"
        "456,TOI-456.01,-1.0,2.0\n"  # negative period — skipped
    )
    out = load_catalog(p)
    assert len(out) == 1
    assert out[0].tic_id == 123
