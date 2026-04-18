"""Gate: Gaia DR3 RUWE cross-match.

RUWE (Renormalised Unit Weight Error) is Gaia's single-star goodness-of-
fit metric. Values near 1.0 mean a single, well-behaved astrometric
source. Values > 1.4 typically indicate excess astrometric noise —
often an unresolved binary companion. For exoplanet vetting a high
RUWE means the host "star" is probably two stars, and our transit
detection is likely a blended eclipsing binary pulling its companion
across itself.

Gate fires hard when RUWE > threshold (default 1.4 — the conventional
Gaia team guidance since DR3).

Gaia lookup uses astroquery.gaia; result is cached by TIC so we don't
re-query the archive for the same star on every sector's re-processing.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from hunter.ingest.tess import LightCurve
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult

log = logging.getLogger(__name__)

DEFAULT_RUWE_CACHE = Path(os.environ.get("HUNTER_RUWE_CACHE", "data/ruwe-cache.csv"))

RuweLookup = Callable[[int], Optional[float]]
_cache_lock = threading.Lock()


def load_ruwe_cache(path: Path | str = DEFAULT_RUWE_CACHE) -> dict[int, float]:
    """Load TIC -> RUWE mapping from a CSV file."""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[int, float] = {}
    with p.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                tic = int(row.get("tic_id") or row.get("TIC") or 0)
                ruwe = float(row.get("ruwe") or row.get("RUWE") or 0)
                if tic > 0 and ruwe > 0:
                    out[tic] = ruwe
            except (ValueError, KeyError, TypeError):
                continue
    return out


def save_ruwe_cache(cache: dict[int, float], path: Path | str = DEFAULT_RUWE_CACHE) -> None:
    """Persist the cache back to disk."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _cache_lock, p.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["tic_id", "ruwe"])
        for tic, ruwe in sorted(cache.items()):
            writer.writerow([tic, f"{ruwe:.4f}"])


def gaia_lookup_via_astroquery(tic_id: int) -> Optional[float]:
    """Live RUWE lookup via astroquery (crossmatches TIC -> Gaia DR3).

    Deferred import — this function only runs when the cache is missing;
    unit tests pass their own lookup and don't touch the network at all.
    """
    try:
        from astroquery.gaia import Gaia  # type: ignore
        from astroquery.mast import Catalogs  # type: ignore
    except Exception as e:
        log.warning("astroquery not importable: %s", e)
        return None

    try:
        # TIC -> (RA, Dec, Gaia DR3 ID). Catalogs.query_criteria is the path
        # that returns the Gaia ID directly when available.
        tic_row = Catalogs.query_criteria(catalog="TIC", ID=str(tic_id))
        if len(tic_row) == 0:
            return None
        gaia_id = tic_row["GAIA"][0] if "GAIA" in tic_row.colnames else None
        if not gaia_id:
            return None
        query = f"SELECT ruwe FROM gaiadr3.gaia_source WHERE source_id = {int(gaia_id)}"
        job = Gaia.launch_job_async(query)
        result = job.get_results()
        if len(result) == 0:
            return None
        return float(result["ruwe"][0])
    except Exception as e:
        log.warning("Gaia RUWE lookup failed for TIC %d: %s", tic_id, e)
        return None


def check_gaia_ruwe(
    lc: LightCurve,
    result: TransitSearchResult,
    *,
    ruwe_threshold: float = 1.4,
    lookup: Optional[RuweLookup] = None,
    cache: Optional[dict[int, float]] = None,
    cache_path: Path | str = DEFAULT_RUWE_CACHE,
) -> GateResult:
    """Check the host's Gaia DR3 RUWE; hard-fail when >= threshold."""
    del result  # not needed here; gate keys off the star itself

    if cache is None:
        cache = load_ruwe_cache(cache_path)

    ruwe: Optional[float] = cache.get(lc.tic_id)
    cache_hit = ruwe is not None

    if ruwe is None:
        fn = lookup or gaia_lookup_via_astroquery
        ruwe = fn(lc.tic_id)
        if ruwe is not None and lookup is None:
            # Only persist when using the live lookup; test mocks shouldn't write.
            cache[lc.tic_id] = ruwe
            try:
                save_ruwe_cache(cache, cache_path)
            except Exception as e:
                log.warning("failed to save RUWE cache: %s", e)

    if ruwe is None:
        # Could not get a Gaia value (no Gaia DR3 crossmatch, API down, etc.).
        # Soft-skip — unknown should not block; we'll still propagate via
        # TRICERATOPS later which has its own stellar-params path.
        return GateResult(
            name="gaia_ruwe",
            passed=True,
            severity="soft",
            reason="no Gaia DR3 RUWE available for this star; skipped",
            metrics={"tic_id": lc.tic_id, "cache_hit": False},
        )

    passed = ruwe < ruwe_threshold
    return GateResult(
        name="gaia_ruwe",
        passed=passed,
        severity="hard",
        reason=(
            f"Gaia DR3 RUWE = {ruwe:.3f} "
            f"({'<' if passed else '>='} threshold {ruwe_threshold}); "
            f"{'single-star consistent' if passed else 'likely unresolved binary'}"
        ),
        metrics={"tic_id": lc.tic_id, "ruwe": ruwe, "threshold": ruwe_threshold, "cache_hit": cache_hit},
    )
