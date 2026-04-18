"""Candidate record: what survives the vet chain + what the dashboard reads.

A Candidate is the canonical on-disk artifact of the hunter. One JSON
file per surviving (TIC, sector) pair. The FastAPI dashboard reads
these at request time — no DB.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hunter.score.composite import Score
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.types import GateResult, VetReport

log = logging.getLogger(__name__)

DEFAULT_CANDIDATE_DIR = Path(os.environ.get("HUNTER_CANDIDATE_DIR", "data/candidates"))


@dataclass
class Candidate:
    """The canonical record for a candidate planet."""

    tic_id: int
    sector: int
    period_days: float
    t0_bjd: float
    depth: float
    duration_days: float
    sde: float
    snr: float
    n_transits: int
    score: float
    tier: str
    n_sectors_confirmed: int
    sectors_seen: list[int] = field(default_factory=list)
    gate_results: list[dict[str, Any]] = field(default_factory=list)
    # ISO-8601 timestamps.
    discovered_at: str = ""
    updated_at: str = ""
    source: str = ""

    @classmethod
    def from_components(
        cls,
        result: TransitSearchResult,
        vet: VetReport,
        score: Score,
        *,
        n_sectors_confirmed: int = 1,
        sectors_seen: list[int] | None = None,
        source: str = "",
        now: float | None = None,
    ) -> "Candidate":
        iso = _iso(now)
        return cls(
            tic_id=result.tic_id,
            sector=result.sector,
            period_days=result.period_days,
            t0_bjd=result.t0_bjd,
            depth=result.depth,
            duration_days=result.duration_days,
            sde=result.sde,
            snr=result.snr,
            n_transits=result.n_transits,
            score=score.value,
            tier=score.tier,
            n_sectors_confirmed=n_sectors_confirmed,
            sectors_seen=sectors_seen if sectors_seen is not None else [result.sector],
            gate_results=[_gate_to_dict(g) for g in vet.gate_results],
            discovered_at=iso,
            updated_at=iso,
            source=source,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=_json_default)

    def filename(self) -> str:
        return f"tic{self.tic_id}-s{self.sector:02d}.json"


def write_candidate(
    candidate: Candidate,
    *,
    directory: Path | str = DEFAULT_CANDIDATE_DIR,
) -> Path:
    """Atomically write a Candidate to disk."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    target = d / candidate.filename()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(candidate.to_json(), encoding="utf-8")
    tmp.replace(target)
    return target


def read_candidate(path: Path | str) -> Candidate:
    """Parse a Candidate from disk; raises on malformed."""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return Candidate(**raw)


def list_candidates(directory: Path | str = DEFAULT_CANDIDATE_DIR) -> list[Candidate]:
    """Read every candidate under `directory`, skipping malformed files."""
    d = Path(directory)
    if not d.exists():
        return []
    out: list[Candidate] = []
    for path in sorted(d.glob("tic*-s*.json")):
        try:
            out.append(read_candidate(path))
        except Exception as e:
            log.warning("skipping malformed candidate %s: %s", path, e)
    return out


def _gate_to_dict(g: GateResult) -> dict[str, Any]:
    return {
        "name": g.name,
        "passed": g.passed,
        "severity": g.severity,
        "reason": g.reason,
        "metrics": g.metrics,
    }


def _iso(now: float | None) -> str:
    t = now if now is not None else time.time()
    # UTC ISO-8601; drops sub-second for human-readability.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _json_default(obj: Any) -> Any:
    # numpy scalars, Path, etc. — be permissive so the write never crashes.
    try:
        import numpy as np  # local import so tests that don't use numpy still work

        if isinstance(obj, (np.integer, np.floating, np.bool_)):
            return obj.item()
    except Exception:
        pass
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"unserializable: {type(obj).__name__}")
