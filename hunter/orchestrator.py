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

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from hunter.output.candidate import list_candidates
from verification.orchestrator import is_halted, load_last_report, run_all

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

DATA_DIR = Path(os.environ.get("HUNTER_DATA_DIR", "data"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8000"))
BACKGROUND_ENABLED = os.environ.get("HUNTER_BACKGROUND", "1") != "0"
HEALTH_INTERVAL_S = int(os.environ.get("HEALTH_INTERVAL_S", "3600"))  # hourly cheap checks


async def background_loop(stop: asyncio.Event) -> None:
    """Minimal scheduler: runs cheap health checks every HEALTH_INTERVAL_S.

    The per-sector hunt loop is invoked separately (by the operator or a
    scheduled external trigger) via the /hunt/sector endpoint — we don't
    want to trigger ~50K light-curve downloads automatically on boot.
    """
    while not stop.is_set():
        try:
            run_all(health_dir=DATA_DIR, enable_expensive=False)
        except Exception as e:
            log.exception("background health check failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEALTH_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background loop on app startup, cancel on shutdown."""
    stop = asyncio.Event()
    task: Optional[asyncio.Task] = None
    if BACKGROUND_ENABLED:
        task = asyncio.create_task(background_loop(stop))
        log.info("background health loop started (interval %ds)", HEALTH_INTERVAL_S)
    yield
    if task:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()


app = FastAPI(title="orb-exoplanet-hunter", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    """Basic liveness + halt status."""
    return {
        "ok": True,
        "halted": is_halted(DATA_DIR),
        "data_dir": str(DATA_DIR),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
