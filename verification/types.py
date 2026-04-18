"""Shared types for pipeline-health checks.

Each check is a pure function returning a HealthResult. An orchestrator
runs them on a schedule (nightly for expensive, hourly for cheap) and
writes the aggregate status into data/pipeline-health.json. If any
hard check fails, the orchestrator writes a PIPELINE_HALT file — no
new candidate is published until a human acknowledges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["hard", "soft"]


@dataclass
class HealthResult:
    """Outcome of one pipeline-health check."""

    name: str
    passed: bool
    severity: Severity
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    ran_at: str = ""  # ISO-8601 timestamp set by the runner

    @property
    def is_blocker(self) -> bool:
        return not self.passed and self.severity == "hard"


@dataclass
class HealthReport:
    """Aggregate of all health checks for one run."""

    results: list[HealthResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(r.is_blocker for r in self.results)

    @property
    def hard_failures(self) -> list[HealthResult]:
        return [r for r in self.results if r.is_blocker]

    @property
    def soft_failures(self) -> list[HealthResult]:
        return [r for r in self.results if not r.passed and r.severity == "soft"]

    def add(self, r: HealthResult) -> None:
        self.results.append(r)
