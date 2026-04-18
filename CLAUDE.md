# CLAUDE.md — session onboarding

Read `learnings.txt` first — it's the build log and the authoritative "what's shipped + why" for every batch.

## House rules

- **Python 3.10+**, pip + requirements.txt (no poetry, no uv — matches Orb's Python runtime exactly).
- **pytest** for everything. Mark MAST/Gaia-hitting tests with `@pytest.mark.integration`; default `pytest` runs unit-only.
- **Every per-candidate gate must have a test against a known-true positive AND a known-true negative fixture.** Never trust a gate that only has positive tests.
- **Pipeline-health halts hard**: if any of the six health checks fails, the orchestrator writes a `PIPELINE_HALT` flag and refuses to publish new candidates until a human acknowledges. No fail-silent.
- **Commit rhythm**: small commits, descriptive messages, push at 5.
- **No secrets in git.** `.env` is gitignored; `.env.example` lists every var the runtime reads.

## Key contracts

- **TESS data access goes through `hunter/ingest/tess.py`**, never direct `astroquery` or HTTP calls. Single choke-point for caching + rate-limiting.
- **Candidates only exist as files in `data/candidates/*.json`.** The dashboard reads them at request time; never a database.
- **`PIPELINE_HALT` flag in `data/` blocks every publisher.** It's a file, not a memory bit, so a crash can't bypass it.
- **Orb reserves ORB_PORT.** Dashboard listens on `HTTP_PORT` (default 8000). See deploy-gotchas in learnings.txt.

## When adding a new gate

1. Add `hunter/vet/<gate>.py` with a single `check(candidate) -> GateResult` function.
2. Add it to `hunter/vet/__init__.py`'s `ALL_GATES` in the right position (cheapest gates first; LLM calls last).
3. Add `tests/unit/test_vet_<gate>.py` with both positive (known planet) and negative (known EB / blend / FP) cases.
4. If the gate changes the vetting PDF, update `hunter/output/report.py` tests.

## When adding a new pipeline-health check

1. Add `verification/<check>.py` exposing `run() -> HealthResult`.
2. Register in `verification/orchestrator.py`'s `CHECKS` list.
3. Wire a hard vs soft severity (hard = halt pipeline; soft = warn only).
4. Add `tests/unit/test_verify_<check>.py`.

## Data that never goes in git

`data/sectors/`, `data/candidates/`, `data/vetting/`, `data/mast-cache/`, `data/injection-tests/`, `data/null-tests/`, `logs/`. All gitignored. They live on the Orb volume.
