"""Per-candidate vetting chain — the 9-gate filter before a candidate
leaves the hunter and hits the dashboard.

Run gates in order, cheapest first, stop on the first hard fail.
Soft fails accumulate into warnings on the vetting page but don't block.

Batch 2 ships 4 gates: odd_even, secondary, ephemeris_match, gaia_ruwe.
Centroid shift (needs pixel data via TessCut) lives in batch 3; the
statistical blend rejection is covered there by TRICERATOPS's FPP path,
which is a strictly stronger test than a centroid heuristic.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from hunter.ingest.tess import LightCurve
from hunter.search.tls_search import TransitSearchResult
from hunter.vet.ephemeris_match import check_ephemeris
from hunter.vet.gaia_ruwe import check_gaia_ruwe
from hunter.vet.odd_even import check_odd_even
from hunter.vet.secondary import check_secondary
from hunter.vet.types import GateResult, Severity, VetReport

log = logging.getLogger(__name__)

Gate = Callable[[LightCurve, TransitSearchResult], GateResult]


# Order matters: cheapest + most decisive first. Chain short-circuits on
# first hard fail, so putting expensive gates (Gaia lookup, TRICERATOPS
# later in batch 3) behind cheap ones means failures return quickly.
DEFAULT_GATES: tuple[Gate, ...] = (
    check_odd_even,          # pure math on the LC
    check_secondary,         # pure math on the LC
    check_ephemeris,         # CSV lookup (cached)
    check_gaia_ruwe,         # HTTP unless cached
)


def run_vet_chain(
    lc: LightCurve,
    result: TransitSearchResult,
    *,
    gates: tuple[Gate, ...] = DEFAULT_GATES,
    stop_on_hard_fail: bool = True,
) -> VetReport:
    """Execute gates in order. Short-circuits on first hard fail by default.

    Soft fails accumulate but don't stop the chain — they become warnings
    on the vetting page. Every gate sees the same (lc, result); gates that
    need extra config (catalogs, lookups) read from env / disk.
    """
    report = VetReport()
    for gate in gates:
        try:
            gr = gate(lc, result)
        except Exception as e:
            # A gate that crashes is a bug — fail hard with a clear marker so
            # we never accidentally publish a candidate that skipped a gate.
            log.exception("gate %s crashed", getattr(gate, "__name__", repr(gate)))
            gr = GateResult(
                name=getattr(gate, "__name__", "unknown_gate"),
                passed=False,
                severity="hard",
                reason=f"gate raised {type(e).__name__}: {e}",
            )
        report.add(gr)
        if stop_on_hard_fail and gr.is_blocker:
            break
    return report


__all__ = [
    "DEFAULT_GATES",
    "Gate",
    "GateResult",
    "Severity",
    "VetReport",
    "check_ephemeris",
    "check_gaia_ruwe",
    "check_odd_even",
    "check_secondary",
    "run_vet_chain",
]
