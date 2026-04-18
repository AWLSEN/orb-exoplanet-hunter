"""End-to-end hunter pipeline: TIC + sector -> Candidate on disk.

One function, `process_target`, walks the full chain:
  ingest -> detrend -> TLS -> vet -> score -> output.

Every step can be short-circuited for tests (inject your own LightCurve
or skip the write). The orchestrator (batch 5) calls this in a loop.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from hunter.detrend.wotan_wrap import detrend
from hunter.ingest.tess import LightCurve, fetch_tic
from hunter.multisector.recurrence import (
    CandidateEphemeris,
    RecurrenceCluster,
    cluster_recurrences,
    n_sectors_for,
)
from hunter.output.activity import log_accepted, log_processing, log_rejected
from hunter.output.candidate import Candidate, write_candidate
from hunter.output.current_state import mark_idle, write_current
from hunter.score.composite import score_candidate
from hunter.search.tls_search import TransitSearchResult, search
from hunter.vet import run_vet_chain
from hunter.vet.types import VetReport

log = logging.getLogger(__name__)

# Allow tests to patch in a pre-fetched LC without going through MAST.
IngestFn = Callable[[int, int | None], LightCurve]

DEFAULT_MIN_SDE = 8.0


@dataclass
class PipelineResult:
    """What happens end-to-end for one target."""

    tic_id: int
    sector: int | None
    accepted: bool
    reason: str
    candidate: Optional[Candidate] = None
    search: Optional[TransitSearchResult] = None
    vet: Optional[VetReport] = None


def _default_ingest(tic_id: int, sector: int | None) -> LightCurve:
    return fetch_tic(tic_id, sector=sector)


def process_target(
    tic_id: int,
    *,
    sector: int | None = None,
    min_sde: float = DEFAULT_MIN_SDE,
    ingest_fn: IngestFn = _default_ingest,
    known_candidates: list[Candidate] | None = None,
    write_to: Path | str | None = None,
) -> PipelineResult:
    """Run the full chain on one target.

    `known_candidates` is the existing on-disk candidate set used for
    multi-sector recurrence. `write_to` is the candidate output directory
    (or None to skip persistence — tests do this).

    Returns a PipelineResult explaining what happened. Early returns
    (rejected) carry the most-informative reason so the orchestrator can
    log and move on.
    """
    # Stage breadcrumbs: the dashboard watches /current + /activity to animate.
    # These writes are idempotent + cheap; failing to write them must never
    # break the pipeline.
    def _stage(stage: str) -> None:
        try:
            write_current(tic_id=tic_id, stage=stage)
            log_processing(tic_id=tic_id, stage=stage)
        except Exception:
            pass

    def _reject(reason: str) -> None:
        try:
            log_rejected(tic_id=tic_id, reason=reason)
        except Exception:
            pass

    try:
        _stage("fetch")
        lc = ingest_fn(tic_id, sector)
    except Exception as e:
        log.warning("ingest failed for TIC %d: %s", tic_id, e)
        _reject(f"ingest: {type(e).__name__}")
        return PipelineResult(
            tic_id=tic_id,
            sector=sector,
            accepted=False,
            reason=f"ingest failed: {type(e).__name__}: {e}",
        )

    try:
        _stage("detrend")
        flat = detrend(lc, window_length_days=0.5).flat
    except Exception as e:
        log.warning("detrend failed for TIC %d: %s", tic_id, e)
        _reject(f"detrend: {type(e).__name__}")
        return PipelineResult(
            tic_id=tic_id,
            sector=lc.sector,
            accepted=False,
            reason=f"detrend failed: {type(e).__name__}: {e}",
        )

    try:
        _stage("search")
        result = search(
            flat,
            period_min_days=0.5,
            period_max_days=min(15.0, flat.duration_days / 2),
            oversampling_factor=2,
        )
    except Exception as e:
        log.warning("search failed for TIC %d: %s", tic_id, e)
        _reject(f"search: {type(e).__name__}")
        return PipelineResult(
            tic_id=tic_id,
            sector=flat.sector,
            accepted=False,
            reason=f"search failed: {type(e).__name__}: {e}",
        )

    if result.sde < min_sde:
        _reject(f"SDE {result.sde:.2f} below floor {min_sde}")
        return PipelineResult(
            tic_id=tic_id,
            sector=flat.sector,
            accepted=False,
            reason=f"SDE {result.sde:.2f} below floor {min_sde}",
            search=result,
        )

    _stage("vet")
    vet = run_vet_chain(flat, result)
    if not vet.passed:
        hard = vet.hard_failures[0]
        _reject(f"vet: {hard.name} — {hard.reason[:60]}")
        return PipelineResult(
            tic_id=tic_id,
            sector=flat.sector,
            accepted=False,
            reason=f"vet blocked at gate '{hard.name}': {hard.reason}",
            search=result,
            vet=vet,
        )

    # Compute multi-sector recurrence from the known candidate set.
    fresh_ephem = CandidateEphemeris(
        tic_id=result.tic_id,
        sector=result.sector,
        period_days=result.period_days,
        t0_bjd=result.t0_bjd,
    )
    existing_ephems = [
        CandidateEphemeris(
            tic_id=c.tic_id,
            sector=c.sector,
            period_days=c.period_days,
            t0_bjd=c.t0_bjd,
        )
        for c in (known_candidates or [])
    ]
    clusters = cluster_recurrences([fresh_ephem, *existing_ephems])
    n_conf = n_sectors_for(fresh_ephem, clusters)

    score = score_candidate(result, vet, n_sectors_confirmed=n_conf)
    sectors_seen = [result.sector]
    for cl in clusters:
        if cl.tic_id == result.tic_id and fresh_ephem in cl.members:
            sectors_seen = sorted(set(cl.sectors))
            break

    candidate = Candidate.from_components(
        result,
        vet,
        score,
        n_sectors_confirmed=n_conf,
        sectors_seen=sectors_seen,
        source=lc.source,
    )

    _stage("write")
    if write_to is not None:
        write_candidate(candidate, directory=write_to)

    # Broadcast the accepted result to the activity log so the dashboard
    # can render a "TIC 12345 → MODERATE (period 3.2d)" row instantly.
    try:
        log_accepted(
            tic_id=tic_id,
            tier=score.tier,
            period_days=result.period_days,
            depth=result.depth,
            sde=result.sde,
        )
    except Exception:
        pass
    try:
        mark_idle()
    except Exception:
        pass

    return PipelineResult(
        tic_id=tic_id,
        sector=result.sector,
        accepted=True,
        reason=f"accepted (tier={score.tier}, score={score.value:.3f})",
        candidate=candidate,
        search=result,
        vet=vet,
    )
