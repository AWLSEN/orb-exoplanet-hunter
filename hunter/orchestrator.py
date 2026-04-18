"""Orchestrator entry point — Orb runs this via `lang=python`, `entry=hunter/orchestrator.py`.

Two concerns in one process:
  1. FastAPI dashboard (reads from data/ at request time).
  2. Background hunt loop (scheduled: per-sector deep scan, daily
     candidate-recurrence refresh, nightly health checks).

Kept intentionally small — heavy lifting lives in pipeline, vet, score,
multisector, verification modules. This file wires them into one
long-lived process Orb can checkpoint.
"""
from __future__ import annotations

import os
import sys
import time as _startup_time

# Audit-trail file we write to BEFORE any heavy imports, so a crash in the
# subsequent import chain still leaves a breadcrumb readable via the Orb
# /files API. stderr_tail is frequently empty on failed agents.
_STARTUP_LOG = os.environ.get("HUNTER_STARTUP_LOG", "/agent/data/startup.log")


def _breadcrumb(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(_STARTUP_LOG), exist_ok=True)
        with open(_STARTUP_LOG, "a") as fh:
            fh.write(f"[{_startup_time.strftime('%Y-%m-%dT%H:%M:%SZ', _startup_time.gmtime())}] {msg}\n")
    except Exception:
        pass


_breadcrumb(
    f"orchestrator.py starting; python={sys.executable} "
    f"argv={sys.argv} cwd={os.getcwd()} HTTP_PORT={os.environ.get('HTTP_PORT', 'UNSET')}"
)

try:
    import asyncio
    import json
    import logging
    import time
    from contextlib import asynccontextmanager
    from pathlib import Path
    from typing import Optional

    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse

    from hunter.output.activity import log_info, read_recent
    from hunter.output.candidate import list_candidates
    from hunter.output.current_state import mark_idle, read_current
    from hunter.pipeline import process_target
    from hunter.hunt import load_tics
    from verification.orchestrator import is_halted, load_last_report, run_all
    _breadcrumb("imports ok")
except Exception as _e:
    import traceback as _tb
    _breadcrumb(f"IMPORT FAILED: {type(_e).__name__}: {_e}")
    _breadcrumb("traceback:\n" + _tb.format_exc())
    raise

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

DATA_DIR = Path(os.environ.get("HUNTER_DATA_DIR", "data"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8000"))
BACKGROUND_ENABLED = os.environ.get("HUNTER_BACKGROUND", "1") != "0"
AUTOHUNT_ENABLED = os.environ.get("HUNTER_AUTOHUNT", "1") != "0"
HEALTH_INTERVAL_S = int(os.environ.get("HEALTH_INTERVAL_S", "3600"))
AUTOHUNT_INTERVAL_S = int(os.environ.get("AUTOHUNT_INTERVAL_S", "300"))  # 5 min between targets
AUTOHUNT_MIN_SDE = float(os.environ.get("AUTOHUNT_MIN_SDE", "8.0"))


async def health_loop(stop: asyncio.Event) -> None:
    """Hourly cheap pipeline-health pass."""
    while not stop.is_set():
        try:
            run_all(health_dir=DATA_DIR, enable_expensive=False)
        except Exception as e:
            log.exception("background health check failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEALTH_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def autohunt_loop(stop: asyncio.Event) -> None:
    """Autonomous hunter: walks the seed TIC list, processing one every
    AUTOHUNT_INTERVAL_S. Loops around when it reaches the end.

    The whole point of this page is to SHOW an agent working; a process
    that only fires on POST /hunt/target silently isn't showing anything.
    """
    try:
        tics = load_tics(None, None)
    except Exception as e:
        log.exception("autohunt: couldn't load seed TICs: %s", e)
        return
    if not tics:
        log.warning("autohunt: empty seed list; not starting")
        return

    log_info(f"autohunt loop started · {len(tics)} seed TICs · {AUTOHUNT_INTERVAL_S}s cadence")
    idx = 0
    while not stop.is_set():
        # Skip if the pipeline-health halt is set — never publish bad work.
        if is_halted(DATA_DIR):
            try:
                await asyncio.wait_for(stop.wait(), timeout=AUTOHUNT_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            continue

        tic = tics[idx % len(tics)]
        idx += 1

        # Cheap "already have this one" skip: don't re-process a TIC we
        # already published a candidate for. Re-enable later when we want
        # multi-sector recurrence sweeps.
        have = list_candidates(DATA_DIR / "candidates")
        if any(c.tic_id == tic for c in have):
            # Still step forward — keeps the loop moving visibly.
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            continue

        log.info("autohunt: processing TIC %d", tic)
        try:
            # Run in a thread — the pipeline does blocking MAST + TLS work.
            await asyncio.to_thread(
                process_target,
                tic,
                min_sde=AUTOHUNT_MIN_SDE,
                known_candidates=have,
                write_to=DATA_DIR / "candidates",
            )
        except Exception as e:
            log.exception("autohunt: TIC %d raised: %s", tic, e)
        finally:
            try:
                mark_idle()
            except Exception:
                pass

        # Wait the configured interval before pulling the next target.
        try:
            await asyncio.wait_for(stop.wait(), timeout=AUTOHUNT_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background loops on app startup, cancel on shutdown."""
    stop = asyncio.Event()
    tasks: list[asyncio.Task] = []
    if BACKGROUND_ENABLED:
        tasks.append(asyncio.create_task(health_loop(stop)))
        log.info("health loop started (interval %ds)", HEALTH_INTERVAL_S)
    if AUTOHUNT_ENABLED:
        tasks.append(asyncio.create_task(autohunt_loop(stop)))
        log.info("autohunt loop started (interval %ds)", AUTOHUNT_INTERVAL_S)
    yield
    if tasks:
        stop.set()
        for t in tasks:
            try:
                await asyncio.wait_for(t, timeout=5)
            except asyncio.TimeoutError:
                t.cancel()


app = FastAPI(title="orb-exoplanet-hunter", lifespan=lifespan)

# CORS: the Vercel-hosted visual dashboard fetches /candidates, /health, and
# /pipeline-health from this origin cross-origin, so browsers need the
# Access-Control-* headers. Allow everything read-only; POST /hunt/target is
# on the same origin so it doesn't need CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.get("/health")
def health() -> dict:
    """Basic liveness + halt status."""
    return {
        "ok": True,
        "halted": is_halted(DATA_DIR),
        "data_dir": str(DATA_DIR),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---------------------------------------------------------------------------
# Session + usage endpoints — surface what the dashboard needs to show
# ACCURATE "session length / active compute / checkpoint cycles" instead of
# guessing from candidate timestamps.
#
# `first_boot` is persisted to disk on first process start and never
# overwritten thereafter — so it survives Orb checkpoints and gives us an
# honest wall-clock session length.
# ---------------------------------------------------------------------------
FIRST_BOOT_FILE = DATA_DIR / "first-boot.txt"


def _ensure_first_boot() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if FIRST_BOOT_FILE.exists():
        return FIRST_BOOT_FILE.read_text().strip()
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    FIRST_BOOT_FILE.write_text(iso)
    return iso


@app.get("/session")
def session_info() -> dict:
    """Honest session stats: wall clock since the very first process start
    (persisted to disk so it survives checkpoints)."""
    first = _ensure_first_boot()
    first_t = time.mktime(time.strptime(first, "%Y-%m-%dT%H:%M:%SZ"))
    now_t = time.time()
    return {
        "first_boot": first,
        "uptime_seconds": max(0, int(now_t - first_t)),
    }


@app.get("/usage")
def usage_info() -> dict:
    """Proxy to Orb's /v1/usage so the dashboard can show real
    checkpoint_cycles / runtime_gb_hours without needing the api key in
    the browser. Reads ORB_API_KEY from env; returns a summary if set,
    or a helpful error otherwise.
    """
    import urllib.parse
    import urllib.request

    key = os.environ.get("ORB_API_KEY")
    if not key:
        return {"ok": False, "error": "ORB_API_KEY not set on agent"}

    # Last 30 days — the usage endpoint aggregates org-wide so this is
    # deliberately generous.
    end = time.gmtime()
    end_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", end)
    start_epoch = time.time() - 30 * 86400
    start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_epoch))

    qs = urllib.parse.urlencode({"start": start_iso, "end": end_iso})
    req = urllib.request.Request(
        f"https://api.orbcloud.dev/v1/usage?{qs}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "period": {"start": start_iso, "end": end_iso},
        "runtime_gb_hours": data.get("runtime_gb_hours"),
        "disk_gb_hours": data.get("disk_gb_hours"),
        "checkpoint_cycles": data.get("checkpoint_cycles"),
        "computers_created": data.get("computers_created"),
        # Org-wide; note to dashboard so it can label honestly.
        "scope": "org-wide",
    }


@app.get("/candidates")
def candidates(tier: str | None = None, min_score: float = 0.0) -> list[dict]:
    """Return all candidates, optionally filtered by tier/score."""
    from dataclasses import asdict
    all_c = list_candidates(DATA_DIR / "candidates")
    if tier is not None:
        all_c = [c for c in all_c if c.tier == tier]
    all_c = [c for c in all_c if c.score >= min_score]
    return [asdict(c) for c in sorted(all_c, key=lambda x: -x.score)]


@app.get("/candidates/{tic_id}")
def candidate_detail(tic_id: int) -> dict:
    """Full record for a specific TIC."""
    from dataclasses import asdict
    for c in list_candidates(DATA_DIR / "candidates"):
        if c.tic_id == tic_id:
            return asdict(c)
    raise HTTPException(status_code=404, detail=f"TIC {tic_id} not found")


@app.post("/hunt/target")
def hunt_target(tic: int, min_sde: float = 8.0) -> dict:
    """Process one TIC through the full pipeline.

    Synchronous and slow (~30s per target). Intended for live demos
    from the dashboard / curl — batch hunts should go through the
    hunter.hunt CLI instead.
    """
    if is_halted(DATA_DIR):
        raise HTTPException(status_code=503, detail="pipeline halted")
    known = list_candidates(DATA_DIR / "candidates")
    res = process_target(
        tic,
        min_sde=min_sde,
        known_candidates=known,
        write_to=DATA_DIR / "candidates",
    )
    return {
        "tic_id": tic,
        "accepted": res.accepted,
        "reason": res.reason,
        "sector": res.sector,
        "candidate": None if res.candidate is None else {
            "tic_id": res.candidate.tic_id,
            "sector": res.candidate.sector,
            "period_days": res.candidate.period_days,
            "depth": res.candidate.depth,
            "sde": res.candidate.sde,
            "score": res.candidate.score,
            "tier": res.candidate.tier,
            "n_sectors_confirmed": res.candidate.n_sectors_confirmed,
        },
    }


@app.get("/current")
def current_task() -> dict:
    """What the hunter is doing *right now*. The dashboard polls this every
    few seconds to render a pulsing "NOW: processing TIC X · stage Y" banner.
    """
    return read_current(DATA_DIR / "current-task.json")


@app.get("/activity")
def activity(limit: int = 30) -> list[dict]:
    """Last `limit` entries from the activity log (newest first).
    Each entry is an ActivityEvent row: ts / kind (processing|accepted|
    rejected|health|info) / tic_id / stage / reason / tier / metrics.
    """
    limit = max(1, min(200, limit))
    return read_recent(limit, DATA_DIR / "activity.jsonl")


@app.get("/pipeline-health")
def pipeline_health() -> dict:
    """Return the latest pipeline-health aggregate."""
    report = load_last_report(DATA_DIR)
    return report or {"ran_at": None, "passed": True, "results": []}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    """Single-page dashboard. Hero counter, candidate table, health panel."""
    return _render_dashboard()


def _render_dashboard() -> str:
    """Static HTML with a tiny bit of JS to fetch JSON endpoints."""
    # Inline template — no Jinja dep needed for this. Values populated via fetch().
    return DASHBOARD_HTML


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>orb-exoplanet-hunter</title>
<style>
  :root { color-scheme: dark; }
  body {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #0b0d10; color: #e2e8f0;
    margin: 0; padding: 24px; line-height: 1.45;
  }
  h1 { margin: 0 0 4px; font-size: 18px; color: #94a3b8; font-weight: 600; }
  h1 span.dot { color: #86efac; }
  .sub { color: #64748b; font-size: 12px; margin-bottom: 24px; }
  .heroes { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 28px; }
  .hero {
    background: #111826; border: 1px solid #1e293b; border-radius: 6px;
    padding: 14px;
  }
  .hero .label { color: #64748b; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
  .hero .value { font-size: 22px; color: #e2e8f0; margin-top: 4px; }
  h2 { font-size: 13px; color: #94a3b8; margin: 28px 0 10px; text-transform: uppercase; letter-spacing: 1px; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #1e293b; }
  th { color: #64748b; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; font-size: 10px; }
  .tier-confirmed { color: #86efac; }
  .tier-strong { color: #fbbf24; }
  .tier-moderate { color: #93c5fd; }
  .tier-weak { color: #64748b; }
  .tier-rejected { color: #fca5a5; text-decoration: line-through; }
  .halted { color: #fca5a5; font-weight: 600; }
  .clean { color: #86efac; }
  .check .ok::before { content: "✓ "; color: #86efac; }
  .check .fail::before { content: "✗ "; color: #fca5a5; }
  .check .warn::before { content: "⚠ "; color: #fbbf24; }
  code { color: #93c5fd; background: transparent; }
  a { color: #93c5fd; }
  pre { white-space: pre-wrap; word-break: break-word; margin: 0; }
</style>
</head>
<body>
  <h1>orb-exoplanet-hunter <span class="dot" id="status-dot">●</span></h1>
  <div class="sub" id="status-text">loading…</div>

  <div class="heroes">
    <div class="hero"><div class="label">Candidates</div><div class="value" id="n-cands">—</div></div>
    <div class="hero"><div class="label">Confirmed</div><div class="value" id="n-conf">—</div></div>
    <div class="hero"><div class="label">Strong</div><div class="value" id="n-strong">—</div></div>
    <div class="hero"><div class="label">Last health ran</div><div class="value" id="health-ts">—</div></div>
  </div>

  <h2>Candidates</h2>
  <table>
    <thead><tr>
      <th>TIC</th><th>Sector</th><th>Period (d)</th><th>Depth (ppm)</th>
      <th>SDE</th><th>Score</th><th>Tier</th><th>Sectors seen</th>
    </tr></thead>
    <tbody id="cands-rows"></tbody>
  </table>

  <h2>Pipeline health</h2>
  <table class="check">
    <thead><tr><th>Check</th><th>Status</th><th>Reason</th><th>Ran at</th></tr></thead>
    <tbody id="health-rows"></tbody>
  </table>

<script>
async function fetchJson(path){ const r = await fetch(path); if(!r.ok) throw new Error(path+" "+r.status); return r.json(); }
async function load(){
  const [hp, cands, health] = await Promise.all([
    fetchJson("/health"),
    fetchJson("/candidates"),
    fetchJson("/pipeline-health"),
  ]);

  document.getElementById("status-dot").style.color = hp.halted ? "#fca5a5" : "#86efac";
  document.getElementById("status-text").innerHTML =
    hp.halted ? "<span class='halted'>PIPELINE HALTED — operator intervention required</span>"
              : "<span class='clean'>pipeline healthy</span>";

  document.getElementById("n-cands").textContent = cands.length.toLocaleString();
  document.getElementById("n-conf").textContent = cands.filter(c=>c.tier==="confirmed").length;
  document.getElementById("n-strong").textContent = cands.filter(c=>c.tier==="strong").length;
  document.getElementById("health-ts").textContent = health.ran_at || "—";

  const tbody = document.getElementById("cands-rows");
  tbody.innerHTML = cands.slice(0,200).map(c => `
    <tr>
      <td><code>${c.tic_id}</code></td>
      <td>${c.sector}</td>
      <td>${c.period_days.toFixed(4)}</td>
      <td>${(c.depth*1e6).toFixed(0)}</td>
      <td>${c.sde.toFixed(1)}</td>
      <td>${c.score.toFixed(2)}</td>
      <td class="tier-${c.tier}">${c.tier}</td>
      <td>${(c.sectors_seen||[]).join(", ")||c.sector}</td>
    </tr>
  `).join("") || `<tr><td colspan="8" style="color:#64748b">No candidates yet — orchestrator will populate this once a sector has been processed.</td></tr>`;

  const hbody = document.getElementById("health-rows");
  hbody.innerHTML = (health.results||[]).map(r => {
    const cls = r.passed ? "ok" : (r.severity==="hard" ? "fail" : "warn");
    return `<tr><td><span class="${cls}">${r.name}</span></td>
                <td>${r.passed?"pass":r.severity}</td>
                <td>${r.reason||""}</td>
                <td>${r.ran_at||""}</td></tr>`;
  }).join("") || `<tr><td colspan="4" style="color:#64748b">Health checks haven't run yet.</td></tr>`;
}
load().catch(e => { document.getElementById("status-text").textContent = "dashboard error: " + e.message; });
setInterval(load, 60000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    _breadcrumb(f"about to uvicorn.run on port {HTTP_PORT}")
    try:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
        _breadcrumb("uvicorn.run returned cleanly (shouldn't happen unless shutdown)")
    except Exception as _e:
        import traceback as _tb
        _breadcrumb(f"UVICORN FAILED: {type(_e).__name__}: {_e}")
        _breadcrumb("traceback:\n" + _tb.format_exc())
        raise
