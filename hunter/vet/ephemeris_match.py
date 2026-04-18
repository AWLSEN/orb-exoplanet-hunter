"""Gate: ephemeris cross-match against the NASA Exoplanet Archive TOI table.

Purpose is twofold:

1. **Avoid re-claiming known planets.** If our candidate's (TIC, period)
   matches an existing TOI, we soft-fail with "already known" — a
   valuable signal ("our pipeline recovered this") but no need to
   publish as new.

2. **Catch ephemeris aliases.** TLS sometimes locks onto half or twice
   the true period; if the half/twice-period matches a known TOI, the
   match catches that too.

The catalog is downloaded once and cached on disk as CSV. Gate load is
O(1) after first fetch.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from hunter.ingest.tess import LightCurve
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult

log = logging.getLogger(__name__)

DEFAULT_CATALOG_PATH = Path(os.environ.get("HUNTER_TOI_CATALOG", "data/toi.csv"))


@dataclass(frozen=True)
class ToiEntry:
    """Minimal TOI record we care about for matching."""

    tic_id: int
    toi_name: str
    period_days: float
    t0_bjd: float
    depth_ppm: float | None = None
    disposition: str | None = None  # "CP" confirmed, "KP" known planet, "PC" candidate, "FP" false positive


def load_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> list[ToiEntry]:
    """Load cached TOI table from CSV.

    The on-disk CSV is whatever the NASA Exoplanet Archive's TOI query
    returns; we only read the columns we need. Missing file -> empty
    list (upstream decides whether to raise or soft-pass).
    """
    p = Path(path)
    if not p.exists():
        return []
    out: list[ToiEntry] = []
    with p.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                tic = int(row.get("tic_id") or row.get("TIC ID") or row.get("tic") or 0)
                if tic <= 0:
                    continue
                period = float(row.get("period_days") or row.get("pl_orbper") or row.get("period") or 0)
                t0 = float(row.get("t0_bjd") or row.get("pl_tranmid") or row.get("t0") or 0)
                if period <= 0:
                    continue
                toi_name = row.get("toi_name") or row.get("toi") or row.get("toi_id") or f"TIC {tic}"
                depth_raw = row.get("depth_ppm") or row.get("pl_trandep") or ""
                depth = float(depth_raw) if depth_raw else None
                disposition = row.get("disposition") or row.get("tfopwg_disp")
                out.append(
                    ToiEntry(
                        tic_id=tic,
                        toi_name=str(toi_name),
                        period_days=period,
                        t0_bjd=t0,
                        depth_ppm=depth,
                        disposition=disposition,
                    )
                )
            except (KeyError, ValueError, TypeError) as e:
                log.debug("skipping malformed TOI row: %s", e)
    return out


def _period_matches(candidate: float, known: float, rel_tol: float = 0.01) -> bool:
    """True if the two periods agree within rel_tol, OR one is ~2× the other."""
    if known <= 0:
        return False
    for ratio in (1.0, 0.5, 2.0):
        if abs(candidate - known * ratio) / (known * ratio) < rel_tol:
            return True
    return False


def check_ephemeris(
    lc: LightCurve,
    result: TransitSearchResult,
    *,
    catalog: Optional[Iterable[ToiEntry]] = None,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    period_tolerance: float = 0.01,
) -> GateResult:
    """Match candidate against the TOI catalog; soft-fail on known planets."""
    if catalog is None:
        catalog = load_catalog(catalog_path)

    matches: list[ToiEntry] = []
    for entry in catalog:
        if entry.tic_id != lc.tic_id:
            continue
        if _period_matches(result.period_days, entry.period_days, rel_tol=period_tolerance):
            matches.append(entry)

    if matches:
        best = matches[0]
        return GateResult(
            name="ephemeris_match",
            passed=False,
            severity="soft",  # known-planet = informational, not a disqualification
            reason=(
                f"matches known {best.toi_name} "
                f"(period {best.period_days:.4f}d vs our {result.period_days:.4f}d)"
            ),
            metrics={
                "matched_toi": best.toi_name,
                "matched_tic": best.tic_id,
                "matched_period": best.period_days,
                "matched_disposition": best.disposition or "",
                "match_count": len(matches),
            },
        )

    # No match — candidate is (so far) novel.
    return GateResult(
        name="ephemeris_match",
        passed=True,
        severity="soft",
        reason="no matching TOI",
        metrics={"match_count": 0, "catalog_size": len(list(catalog)) if isinstance(catalog, list) else 0},
    )
