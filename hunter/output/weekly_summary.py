"""Claude-generated weekly narrative of the hunter's progress.

Runs weekly (via CLI or manual trigger), reads the candidate set + last
pipeline-health report, and asks Claude to write a short markdown
summary humans will actually read. Output goes to
data/summaries/<YYYY-WW>.md.

Model routing respects ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN so the
Z.AI GLM Coding Plan proxy works out of the box. Falls back to native
ANTHROPIC_API_KEY when no proxy is configured.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from hunter.output.candidate import Candidate

log = logging.getLogger(__name__)

DEFAULT_SUMMARY_DIR = Path(os.environ.get("HUNTER_SUMMARY_DIR", "data/summaries"))


SYSTEM_PROMPT = """You write weekly progress summaries for an autonomous
exoplanet-candidate hunter. You receive the current candidate database
and the most recent pipeline-health report as JSON. Output ONE markdown
document: a 2-3 paragraph human-readable narrative covering:

1. A headline number (candidates total, confirmed count, best tier
   achieved this week).
2. The most notable candidates (top 3 by score) with period, depth in
   ppm, SDE, and tier.
3. Pipeline health status — clean, soft-warned, or halted — in one
   sentence.

Rules:
- Never invent numbers. Only report figures present in the input JSON.
- If the database is empty, say so clearly. Do not fabricate finds.
- If the pipeline is halted, lead with that fact.
- Keep it tight. Under 250 words total.
- No opening pleasantries, no closing sign-off.
"""


AnthropicCaller = Callable[[str, str], str]  # (system, user) -> response text


def _default_anthropic_caller(system: str, user: str) -> str:
    """Live call via the anthropic SDK. Deferred import so unit tests
    can inject a pure-Python stub."""
    import anthropic

    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")

    kwargs: dict[str, Any] = {}
    if auth_token:
        kwargs["auth_token"] = auth_token
    elif api_key:
        kwargs["api_key"] = api_key
    else:
        raise RuntimeError("no ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY set")
    if base_url:
        kwargs["base_url"] = base_url

    client = anthropic.Anthropic(**kwargs)
    resp = client.messages.create(
        model=model,
        max_tokens=800,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n\n".join(parts).strip()


def build_input_json(candidates: list[Candidate], health: dict | None) -> str:
    """Serialize the current state for Claude."""
    import json
    top = sorted(candidates, key=lambda c: -c.score)[:3]
    summary_candidates = [asdict(c) for c in top]
    full_counts = {
        "total": len(candidates),
        "confirmed": sum(1 for c in candidates if c.tier == "confirmed"),
        "strong": sum(1 for c in candidates if c.tier == "strong"),
        "moderate": sum(1 for c in candidates if c.tier == "moderate"),
        "weak": sum(1 for c in candidates if c.tier == "weak"),
    }
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "counts": full_counts,
        "top3": summary_candidates,
        "pipeline_health": health or {},
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def generate_weekly_summary(
    candidates: list[Candidate],
    health: dict | None,
    *,
    caller: AnthropicCaller = _default_anthropic_caller,
    summary_dir: Path | str = DEFAULT_SUMMARY_DIR,
    week_tag: str | None = None,
) -> Path:
    """Call Claude, write markdown to disk, return the output path."""
    user = build_input_json(candidates, health)
    markdown = caller(SYSTEM_PROMPT, user)

    # Always prepend an audit header so the raw inputs can be re-derived.
    tag = week_tag or time.strftime("%Y-W%V", time.gmtime())
    header = f"# Weekly summary — {tag}\n_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}_\n\n"
    body = header + markdown

    summary_dir = Path(summary_dir)
    summary_dir.mkdir(parents=True, exist_ok=True)
    out = summary_dir / f"{tag}.md"
    out.write_text(body, encoding="utf-8")
    return out
