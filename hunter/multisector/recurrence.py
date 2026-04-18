"""Multi-sector recurrence detector.

Two candidates, one from sector N and one from sector N+k, are a
"recurrence" when:
  1. Same TIC ID.
  2. Periods agree within rel_tol (default 1%).
  3. The ephemeris prediction is consistent: phase_offset = T0_B - T0_A
     (mod period) is near 0 or near the period (within tol × period).

Recurrence across K sectors lifts a candidate's tier to "confirmed" in
the composite scorer. Recurrence is the single strongest signal a
candidate is a real planet short of follow-up observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class CandidateEphemeris:
    """Minimal shape for recurrence checking."""

    tic_id: int
    sector: int
    period_days: float
    t0_bjd: float


@dataclass
class RecurrenceCluster:
    """A set of candidates across sectors agreeing on the same ephemeris."""

    tic_id: int
    period_days: float           # averaged across the cluster
    t0_bjd: float                # first-sector T0
    sectors: list[int] = field(default_factory=list)
    members: list[CandidateEphemeris] = field(default_factory=list)

    @property
    def n_sectors(self) -> int:
        return len(self.sectors)


def _ephemeris_matches(a: CandidateEphemeris, b: CandidateEphemeris, rel_tol: float) -> bool:
    """True when a and b share ephemeris within tolerance."""
    if a.tic_id != b.tic_id:
        return False
    if a.period_days <= 0 or b.period_days <= 0:
        return False
    # Accept direct, 2×, 0.5× matches — TLS aliases are common.
    for ratio in (1.0, 0.5, 2.0):
        if abs(a.period_days - b.period_days * ratio) / (b.period_days * ratio) < rel_tol:
            # Periods match; now phase-offset consistency. Scale down to a
            # single-period reference for the check.
            common_period = min(a.period_days, b.period_days)
            dt = abs(b.t0_bjd - a.t0_bjd)
            phase = (dt % common_period) / common_period
            phase = min(phase, 1 - phase)
            if phase < rel_tol:
                return True
    return False


def cluster_recurrences(
    candidates: Iterable[CandidateEphemeris],
    *,
    rel_tol: float = 0.01,
) -> list[RecurrenceCluster]:
    """Bin candidates into recurrence clusters.

    Clusters form greedily: for each candidate, attach to the first
    compatible existing cluster; otherwise start a new cluster. The
    result is a list of clusters, one per TIC/ephemeris seen.
    """
    clusters: list[RecurrenceCluster] = []
    # Sort so earlier sectors seed each cluster — keeps reporting stable.
    ordered = sorted(candidates, key=lambda c: (c.tic_id, c.sector))
    for cand in ordered:
        attached = False
        for cl in clusters:
            if cl.tic_id != cand.tic_id:
                continue
            # Compare to the first member — stable even if internal averaging
            # drifts slightly with more members.
            if _ephemeris_matches(cl.members[0], cand, rel_tol):
                cl.members.append(cand)
                cl.sectors.append(cand.sector)
                # Update representative period as running mean.
                cl.period_days = sum(m.period_days for m in cl.members) / len(cl.members)
                attached = True
                break
        if not attached:
            clusters.append(
                RecurrenceCluster(
                    tic_id=cand.tic_id,
                    period_days=cand.period_days,
                    t0_bjd=cand.t0_bjd,
                    sectors=[cand.sector],
                    members=[cand],
                )
            )
    return clusters


def n_sectors_for(candidate: CandidateEphemeris, clusters: Iterable[RecurrenceCluster]) -> int:
    """Look up how many sectors back this candidate's cluster spans.

    Used by the orchestrator when scoring a fresh candidate: cluster-in,
    then ask "how many sectors confirm this one?" → feed to scorer.
    """
    for cl in clusters:
        if cl.tic_id != candidate.tic_id:
            continue
        if candidate in cl.members:
            return cl.n_sectors
    return 1  # candidate not yet clustered → solo sector
