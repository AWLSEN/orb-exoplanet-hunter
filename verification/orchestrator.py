"""Pipeline-health orchestrator.

Runs every health check, persists the aggregate report, and if any HARD
check fails writes a HALT flag file. Every publisher (candidate
writer, dashboard) must call `is_halted()` before accepting new output.

A halt is sticky: it stays in place until a human deletes the flag
file. Never auto-clear, never retry silently — if the pipeline is
broken, pause and alert.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from hunter.output.candidate import Candidate, list_candidates
from verification.cheap_checks import (
    check_depth_distribution,
    check_ephemeris_consistency,
)
from verification.injection import check_injection_recovery
from verification.known_planets import check_known_planets
from verification.null_rate import check_null_rate
from verification.types import HealthReport, HealthResult

log = logging.getLogger(__name__)

DEFAULT_HEALTH_DIR = Path(os.environ.get("HUNTER_HEALTH_DIR", "data"))
HALT_FILENAME = "PIPELINE_HALT"
REPORT_FILENAME = "pipeline-health.json"


# Cheap checks need the current candidate DB; expensive ones do not.
CheckCheap = Callable[[Iterable[Candidate]], HealthResult]
CheckExpensive = Callable[[], HealthResult]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tag(result: HealthResult) -> HealthResult:
    """Stamp ran_at if not already set."""
    if not result.ran_at:
        result.ran_at = _now_iso()
    return result


def run_cheap(candidates: Iterable[Candidate]) -> HealthReport:
    """Cheap checks — safe to run hourly."""
    report = HealthReport()
    cands = list(candidates)
    for check in (check_depth_distribution, check_ephemeris_consistency):
        report.add(_tag(check(cands)))
    return report


def run_expensive(
    *,
    enable_known_planets: bool = True,
    enable_injection_recovery: bool = True,
    enable_null_rate: bool = True,
) -> HealthReport:
    """Expensive checks — nightly cadence."""
    report = HealthReport()
    if enable_known_planets:
        report.add(_tag(check_known_planets()))
    if enable_injection_recovery:
        report.add(_tag(check_injection_recovery()))
    if enable_null_rate:
        report.add(_tag(check_null_rate()))
    return report


def run_all(
    *,
    health_dir: Path | str = DEFAULT_HEALTH_DIR,
    enable_expensive: bool = True,
) -> HealthReport:
    """Run cheap + (optionally) expensive, persist the aggregate, apply HALT."""
    health_dir = Path(health_dir)
    health_dir.mkdir(parents=True, exist_ok=True)

    candidates = list_candidates()
    report = run_cheap(candidates)
    if enable_expensive:
        exp = run_expensive()
        for r in exp.results:
            report.add(r)

    # Persist aggregate.
    out = health_dir / REPORT_FILENAME
    payload = {
        "ran_at": _now_iso(),
        "passed": report.passed,
        "hard_failures": [asdict(r) for r in report.hard_failures],
        "soft_failures": [asdict(r) for r in report.soft_failures],
        "results": [asdict(r) for r in report.results],
    }
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(out)

    # Apply halt if any hard failed.
    halt = health_dir / HALT_FILENAME
    if report.passed:
        # Don't clear an existing halt automatically — that's an operator decision.
        pass
    else:
        if not halt.exists():
            halt_payload = {
                "halted_at": _now_iso(),
                "reasons": [f"{r.name}: {r.reason}" for r in report.hard_failures],
            }
            halt.write_text(json.dumps(halt_payload, indent=2), encoding="utf-8")
            log.warning("HALT engaged: %s", halt_payload["reasons"])

    return report


def is_halted(health_dir: Path | str = DEFAULT_HEALTH_DIR) -> bool:
    """True iff the HALT flag file exists. Publishers must check this."""
    return (Path(health_dir) / HALT_FILENAME).exists()


def clear_halt(health_dir: Path | str = DEFAULT_HEALTH_DIR) -> bool:
    """Manually clear the halt. Returns True if a halt was present + cleared."""
    path = Path(health_dir) / HALT_FILENAME
    if not path.exists():
        return False
    path.unlink()
    log.info("HALT cleared by operator action")
    return True


def load_last_report(health_dir: Path | str = DEFAULT_HEALTH_DIR) -> Optional[dict]:
    """Read the persisted report for the dashboard."""
    path = Path(health_dir) / REPORT_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
