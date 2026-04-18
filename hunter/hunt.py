"""Batch hunter CLI — processes a list of TICs through the full pipeline.

Usage (local):
    python -m hunter.hunt --tics 22529346,261136679,150428135
    python -m hunter.hunt --from-file data/targets.txt

On Orb this runs as a one-shot invocation from the operator or a
scheduled trigger; the FastAPI dashboard (hunter/orchestrator.py) is
the always-on process that serves what the hunt produces.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from hunter.output.candidate import list_candidates
from hunter.pipeline import process_target
from verification.orchestrator import is_halted

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

DEFAULT_DATA_DIR = Path(os.environ.get("HUNTER_DATA_DIR", "data"))


def load_tics(arg_tics: str | None, arg_from_file: str | None) -> list[int]:
    """Parse TICs from --tics CSV or --from-file (one per line)."""
    if arg_tics:
        return [int(x.strip()) for x in arg_tics.split(",") if x.strip()]
    if arg_from_file:
        p = Path(arg_from_file)
        if not p.exists():
            raise FileNotFoundError(f"target file not found: {p}")
        tics: list[int] = []
        for line in p.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            tics.append(int(s))
        return tics
    raise SystemExit("must supply --tics or --from-file")


def run_hunt(
    tics: list[int],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    min_sde: float = 8.0,
    skip_when_halted: bool = True,
) -> dict:
    """Iterate TICs; emit a summary dict the caller can log."""
    cand_dir = data_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)

    if skip_when_halted and is_halted(data_dir):
        log.warning("pipeline is halted; refusing to run the hunt")
        return {"skipped": True, "reason": "pipeline halted"}

    known = list_candidates(cand_dir)
    accepted = 0
    rejected = 0
    errors = 0
    per_target = []

    for tic in tics:
        try:
            res = process_target(tic, min_sde=min_sde, known_candidates=known, write_to=cand_dir)
            per_target.append(
                {"tic_id": tic, "accepted": res.accepted, "reason": res.reason, "sector": res.sector}
            )
            if res.accepted:
                accepted += 1
                # Re-read the candidate DB so later TICs see this cluster.
                known = list_candidates(cand_dir)
            else:
                rejected += 1
            log.info("TIC %d -> %s (%s)", tic, "accepted" if res.accepted else "rejected", res.reason)
        except Exception as e:
            errors += 1
            log.exception("TIC %d unhandled: %s", tic, e)
            per_target.append({"tic_id": tic, "accepted": False, "reason": f"unhandled: {e}"})

    return {
        "total": len(tics),
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "per_target": per_target,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="orb-exoplanet-hunter batch runner")
    parser.add_argument("--tics", help="comma-separated TIC IDs")
    parser.add_argument("--from-file", help="file with one TIC ID per line")
    parser.add_argument("--min-sde", type=float, default=8.0)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--ignore-halt", action="store_true", help="proceed even when HALT flag set")
    args = parser.parse_args(argv)

    tics = load_tics(args.tics, args.from_file)
    summary = run_hunt(
        tics,
        data_dir=Path(args.data_dir),
        min_sde=args.min_sde,
        skip_when_halted=not args.ignore_halt,
    )
    print(f"hunt summary: {summary['accepted']}/{summary.get('total', 0)} accepted, "
          f"{summary.get('rejected', 0)} rejected, {summary.get('errors', 0)} errors")
    return 0 if summary.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
