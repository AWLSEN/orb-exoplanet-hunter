"""Activity log — append-only JSONL of everything the hunter does.

Two surfaces read this:
  - `/activity` endpoint → dashboard "recent activity" feed.
  - humans debugging post-mortem via `data/activity.jsonl`.

Writes are append-only + fsynced so a crash can't corrupt the stream.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_ACTIVITY_LOG = Path(os.environ.get("HUNTER_ACTIVITY_LOG", "data/activity.jsonl"))

_write_lock = threading.Lock()


@dataclass
class ActivityEvent:
    """One thing that happened. Designed for ~100-byte rows."""

    ts: str                         # ISO-8601 UTC
    kind: str                       # "processing" | "accepted" | "rejected" | "health" | "info"
    tic_id: Optional[int] = None
    stage: Optional[str] = None     # for in-flight: "fetch" | "detrend" | "search" | "vet"
    reason: Optional[str] = None    # why accepted/rejected
    tier: Optional[str] = None      # for accepted candidates
    period_days: Optional[float] = None
    depth_ppm: Optional[float] = None
    sde: Optional[float] = None
    extra: dict = field(default_factory=dict)


def _iso(now: Optional[float] = None) -> str:
    t = now if now is not None else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def append_event(event: ActivityEvent, path: Path | str = DEFAULT_ACTIVITY_LOG) -> None:
    """Atomically append a single JSONL row."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = json.dumps(asdict(event), sort_keys=True) + "\n"
    with _write_lock, p.open("a", encoding="utf-8") as fh:
        fh.write(row)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            # Some filesystems don't support fsync; the write itself is enough.
            pass


def log_processing(tic_id: int, stage: str, *, path: Path | str = DEFAULT_ACTIVITY_LOG) -> None:
    append_event(ActivityEvent(ts=_iso(), kind="processing", tic_id=tic_id, stage=stage), path)


def log_accepted(
    tic_id: int,
    *,
    tier: str,
    period_days: float,
    depth: float,
    sde: float,
    path: Path | str = DEFAULT_ACTIVITY_LOG,
) -> None:
    append_event(
        ActivityEvent(
            ts=_iso(),
            kind="accepted",
            tic_id=tic_id,
            tier=tier,
            period_days=period_days,
            depth_ppm=round(depth * 1_000_000, 1) if depth > 0 else None,
            sde=round(sde, 2),
            reason=f"tier={tier}",
        ),
        path,
    )


def log_rejected(tic_id: int, reason: str, *, path: Path | str = DEFAULT_ACTIVITY_LOG) -> None:
    append_event(
        ActivityEvent(ts=_iso(), kind="rejected", tic_id=tic_id, reason=reason),
        path,
    )


def log_info(message: str, *, path: Path | str = DEFAULT_ACTIVITY_LOG, **extra) -> None:
    append_event(
        ActivityEvent(ts=_iso(), kind="info", reason=message, extra=dict(extra)),
        path,
    )


def read_recent(limit: int = 50, path: Path | str = DEFAULT_ACTIVITY_LOG) -> list[dict]:
    """Return the most recent `limit` events (newest first)."""
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out: list[dict] = []
    # Walk from the end backwards for efficiency on large files.
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out
