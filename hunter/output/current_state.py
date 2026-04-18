"""Live "what's the agent doing RIGHT NOW" state.

Written from TaskRunner as it steps through the pipeline (fetch, detrend,
search, vet, write). Read by the `/current` endpoint so the dashboard
can render a pulsing "NOW: processing TIC 12345 · vet" banner.

One small JSON file on disk — overwritten atomically on every stage
change. No history (that's what activity.jsonl is for).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

DEFAULT_CURRENT_FILE = Path(os.environ.get("HUNTER_CURRENT_FILE", "data/current-task.json"))


def _iso(now: Optional[float] = None) -> str:
    t = now if now is not None else time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def write_current(
    *,
    tic_id: Optional[int],
    stage: str,
    path: Path | str = DEFAULT_CURRENT_FILE,
) -> None:
    """Atomically overwrite the current-state file.

    `stage` is one of: "idle", "fetch", "detrend", "search", "vet",
    "write", or a custom string (passed through verbatim).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": _iso(),
        "tic_id": tic_id,
        "stage": stage,
    }
    # Atomic replace so concurrent readers never see half-written content.
    fd, tmp = tempfile.mkstemp(prefix=".current-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_current(path: Path | str = DEFAULT_CURRENT_FILE) -> dict:
    """Read the current-state file or return an idle stub."""
    p = Path(path)
    if not p.exists():
        return {"ts": _iso(), "tic_id": None, "stage": "idle"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"ts": _iso(), "tic_id": None, "stage": "idle"}


def mark_idle(path: Path | str = DEFAULT_CURRENT_FILE) -> None:
    write_current(tic_id=None, stage="idle", path=path)
