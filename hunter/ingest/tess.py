"""TESS light-curve ingestion — the single choke-point for MAST access.

All other code reads light curves through `fetch_tic` so we can cache on
disk, rate-limit, and swap implementations without touching the rest of
the pipeline.

The `LightCurve` shape is small on purpose — just (time, flux, flux_err,
metadata). Downstream tools convert to their own representations (numpy
arrays for `wotan`, `transitleastsquares`).
"""
from __future__ import annotations

import hashlib
import logging
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

# Default cache location — lives on the Orb volume, gitignored.
DEFAULT_CACHE_DIR = Path(os.environ.get("HUNTER_CACHE_DIR", "data/mast-cache"))


@dataclass
class LightCurve:
    """Normalized light curve: unit-median flux, NaN-scrubbed, sorted by time."""

    tic_id: int
    sector: int
    time: np.ndarray          # BJD - 2457000 (TESS convention)
    flux: np.ndarray          # unit-median normalized
    flux_err: np.ndarray      # relative units (same scale as flux)
    cadence_s: float          # 120 for 2-min, 600 for FFI cutout, etc.
    source: str = "unknown"   # e.g. "lightkurve:SPOC" or "synthetic"
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time.shape != self.flux.shape or self.time.shape != self.flux_err.shape:
            raise ValueError(
                f"time/flux/flux_err shape mismatch: {self.time.shape} vs "
                f"{self.flux.shape} vs {self.flux_err.shape}"
            )
        if self.time.size == 0:
            raise ValueError("light curve is empty")

    @property
    def n_points(self) -> int:
        return int(self.time.size)

    @property
    def duration_days(self) -> float:
        return float(self.time[-1] - self.time[0])

    @property
    def completeness(self) -> float:
        """Fraction of the span that's filled vs expected at the declared cadence."""
        expected = max(1, int(self.duration_days * 86400 / self.cadence_s))
        return min(1.0, self.n_points / expected)


def normalize(time: np.ndarray, flux: np.ndarray, flux_err: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop NaN/inf, sort by time, normalize flux to unit median.

    Returns filtered (time, flux, flux_err) arrays.
    """
    if time.size != flux.size or time.size != flux_err.size:
        raise ValueError("array shape mismatch")

    good = np.isfinite(time) & np.isfinite(flux) & np.isfinite(flux_err) & (flux_err > 0)
    time, flux, flux_err = time[good], flux[good], flux_err[good]
    if time.size == 0:
        raise ValueError("no finite points remain after filtering")

    order = np.argsort(time)
    time, flux, flux_err = time[order], flux[order], flux_err[order]

    median = float(np.median(flux))
    if median <= 0:
        raise ValueError(f"median flux must be positive, got {median}")
    flux = flux / median
    flux_err = flux_err / median
    return time, flux, flux_err


def _cache_path(cache_dir: Path | str, tic_id: int, sector: int, cadence_s: float) -> Path:
    """Cached file layout: data/mast-cache/<tic>/<sector>-<cadence>.npz."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tic_dir = cache_dir / f"tic{tic_id}"
    tic_dir.mkdir(exist_ok=True)
    return tic_dir / f"s{sector:02d}-c{int(cadence_s)}.npz"


def save_cached(lc: LightCurve, cache_dir: Path | str = DEFAULT_CACHE_DIR) -> Path:
    """Serialize a LightCurve to npz for later reuse."""
    path = _cache_path(Path(cache_dir), lc.tic_id, lc.sector, lc.cadence_s)
    np.savez_compressed(
        path,
        time=lc.time,
        flux=lc.flux,
        flux_err=lc.flux_err,
        tic_id=lc.tic_id,
        sector=lc.sector,
        cadence_s=lc.cadence_s,
        source=lc.source,
    )
    return path


def load_cached(tic_id: int, sector: int, cadence_s: float = 600, cache_dir: Path | str = DEFAULT_CACHE_DIR) -> Optional[LightCurve]:
    """Return a cached LightCurve or None if the file isn't there."""
    path = _cache_path(Path(cache_dir), tic_id, sector, cadence_s)
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as f:
        return LightCurve(
            tic_id=int(f["tic_id"]),
            sector=int(f["sector"]),
            time=f["time"].astype(np.float64),
            flux=f["flux"].astype(np.float64),
            flux_err=f["flux_err"].astype(np.float64),
            cadence_s=float(f["cadence_s"]),
            source=str(f["source"]),
        )


def fetch_tic(
    tic_id: int,
    sector: Optional[int] = None,
    cadence_s: float = 600,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    force: bool = False,
    author: str = "SPOC",
) -> LightCurve:
    """Fetch (and cache) a TESS light curve for a TIC ID.

    If `sector` is None, the first readable light curve is returned.
    `cadence_s`: 120 for 2-min SPOC, 600 for 10-min FFI. Default 600 because
    most M-dwarf coverage lives in FFIs.

    `author="SPOC"` by default — the official TESS pipeline with reliably
    formatted FITS. Other authors (T16, QLP, CDIPS, TESS-SPOC) are valid
    but produce heterogeneous products that sometimes break lightkurve's
    reader. Override only when you know what you're doing.
    """
    cache_dir = Path(cache_dir)
    if sector is not None:
        hit = load_cached(tic_id, sector, cadence_s, cache_dir)
        if hit and not force:
            log.debug("cache hit: TIC %d sector %d", tic_id, sector)
            return hit

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import lightkurve as lk

    search = lk.search_lightcurve(f"TIC {tic_id}", mission="TESS", author=author)
    if len(search) == 0:
        raise LookupError(
            f"no TESS light curves found for TIC {tic_id} (author={author})"
        )

    if sector is not None:
        mask = np.array([_extract_sector_from_mission(m) == sector for m in search.mission.tolist()])
        search = search[mask]
        if len(search) == 0:
            raise LookupError(f"no TESS sector {sector} light curve for TIC {tic_id}")

    # Download a single product at a time so one bad FITS doesn't nuke the
    # whole batch. Try longest-span candidate first; on read failure, delete
    # the cached FITS and fall through to the next.
    ordered = list(search)
    # Prefer rows with the longest estimated exposure baseline (sector length).
    # lightkurve SearchResult rows don't expose baseline directly, so we just
    # try each in turn.
    last_err: Exception | None = None
    for entry in ordered:
        try:
            lc_raw = entry.download(quality_bitmask="default")
        except Exception as e:
            last_err = e
            log.warning("lightkurve download failed for TIC %d: %s", tic_id, e)
            continue
        if lc_raw is None or len(lc_raw.time) == 0:
            last_err = LookupError("download returned empty LC")
            continue

        try:
            time_arr = np.asarray(lc_raw.time.value, dtype=np.float64)
            flux_arr = np.asarray(lc_raw.flux.value, dtype=np.float64)
            err_arr = np.asarray(lc_raw.flux_err.value, dtype=np.float64)
            time_arr, flux_arr, err_arr = normalize(time_arr, flux_arr, err_arr)
        except Exception as e:
            last_err = e
            continue

        resolved_sector = _sector_of(lc_raw)
        source = f"lightkurve:{getattr(lc_raw, 'author', author) or author}"
        lc = LightCurve(
            tic_id=tic_id,
            sector=resolved_sector,
            time=time_arr,
            flux=flux_arr,
            flux_err=err_arr,
            cadence_s=cadence_s,
            source=source,
        )
        save_cached(lc, cache_dir)
        return lc

    raise LookupError(
        f"could not read any TESS light curve for TIC {tic_id}; "
        f"last error: {last_err}"
    )


def _extract_sector_from_mission(mission: str) -> int:
    """Parse '"TESS Sector 03"' or similar into the int sector number."""
    if not mission:
        return -1
    parts = mission.strip().split()
    for p in reversed(parts):
        if p.isdigit():
            return int(p)
    return -1


def _sector_of(lk_lc) -> int:
    """Best-effort sector extraction from a lightkurve LightCurve."""
    sector = getattr(lk_lc, "sector", None)
    if sector is not None:
        return int(sector)
    mission = getattr(lk_lc, "mission", None)
    if isinstance(mission, list) and mission:
        return _extract_sector_from_mission(mission[0])
    if isinstance(mission, str):
        return _extract_sector_from_mission(mission)
    return -1


def synthetic(
    tic_id: int = -1,
    sector: int = 0,
    *,
    duration_days: float = 27.0,
    cadence_s: float = 600,
    noise_ppm: float = 1000.0,
    period_days: Optional[float] = None,
    depth: float = 0.001,
    transit_duration_days: float = 0.1,
    t0_days: float = 1.0,
    rng_seed: int = 42,
) -> LightCurve:
    """Build a synthetic light curve (optionally with an injected transit).

    Useful for unit tests — the pipeline has been tested with this shape so
    behavior is predictable without hitting MAST.
    """
    rng = np.random.default_rng(rng_seed)
    n = int(duration_days * 86400 / cadence_s)
    time = np.linspace(0.0, duration_days, n)
    flux = rng.normal(loc=1.0, scale=noise_ppm * 1e-6, size=n)
    flux_err = np.full_like(flux, noise_ppm * 1e-6)

    if period_days is not None:
        # Insert a simple box-shaped transit at each integer period + t0.
        half_w = transit_duration_days / 2
        phase = ((time - t0_days) % period_days) - period_days / 2
        in_transit = np.abs(phase - (-period_days / 2 + half_w)) < half_w
        # More correct: mark [t0, t0 + D] windows periodically.
        in_transit = np.zeros_like(time, dtype=bool)
        t = t0_days
        while t < duration_days:
            in_transit |= (time >= t) & (time <= t + transit_duration_days)
            t += period_days
        flux[in_transit] -= depth

    return LightCurve(
        tic_id=tic_id,
        sector=sector,
        time=time,
        flux=flux,
        flux_err=flux_err,
        cadence_s=cadence_s,
        source="synthetic",
    )


def fingerprint(lc: LightCurve) -> str:
    """Stable short hash of a light curve — used for cache keys + tests."""
    h = hashlib.sha256()
    h.update(lc.time.tobytes())
    h.update(lc.flux.tobytes())
    return h.hexdigest()[:12]
