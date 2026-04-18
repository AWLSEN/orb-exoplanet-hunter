"""Shared types for the per-candidate vetting chain.

Each gate is a pure function: (candidate, lightcurve) -> GateResult.
The chain short-circuits on the first hard fail, just like orb-async-dev's
verifier. Soft fails become warnings on the vetting page.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["hard", "soft"]


@dataclass
class GateResult:
    """Output of a single vetting gate."""

    name: str
    passed: bool
    severity: Severity
    reason: str
    # Structured per-gate metrics that get written into the vetting report.
    # Keys are gate-specific; dashboard UI renders them verbatim.
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocker(self) -> bool:
        return not self.passed and self.severity == "hard"


@dataclass
class VetReport:
    """Aggregate of all gate results for one candidate."""

    gate_results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True iff no hard-severity gate failed."""
        return not any(g.is_blocker for g in self.gate_results)

    @property
    def hard_failures(self) -> list[GateResult]:
        return [g for g in self.gate_results if g.is_blocker]

    @property
    def soft_failures(self) -> list[GateResult]:
        return [g for g in self.gate_results if not g.passed and g.severity == "soft"]

    def add(self, result: GateResult) -> None:
        self.gate_results.append(result)
