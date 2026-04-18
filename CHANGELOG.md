# Changelog

## 0.1.0 — 2026-04-18

Initial MIT release. Live at https://cc200989.orbcloud.dev.

### Shipped

- **Scaffolded pipeline**: lightkurve (TESS ingest) → wotan biweight
  (detrend) → transitleastsquares (transit search) → composite vet
  chain (odd/even, secondary eclipse, ephemeris cross-match, Gaia RUWE)
  → multi-sector recurrence clustering → composite score + tier →
  atomic candidate JSON write.
- **Pipeline-health verification**: known-planet gold-set recovery
  (hard), injection-recovery sensitivity sweep (hard), null-data FP
  rate (hard), depth distribution sanity (hard), ephemeris consistency
  across sectors (hard). Any hard failure writes a sticky HALT flag.
- **FastAPI dashboard**: single-page HTML + JSON endpoints (`/health`,
  `/candidates`, `/candidates/{tic}`, `/pipeline-health`,
  `/hunt/target`). Dark monospace theme matching the showcase family.
- **CLI batch runner**: `python -m hunter.hunt --tics a,b,c` or
  `--from-file` walks a TIC list through the full chain.
- **Weekly Claude summary**: Claude (Z.AI GLM proxy by default)
  generates a markdown narrative of the week's finds.
- **Orb Cloud deploy**: idempotent `scripts/deploy.sh` + tuned
  `orb.toml` (lang=python, `pip install -e .`, HTTP_PORT not ORB_PORT,
  `git fetch+reset` in build, `python3` on validation step).

### Verified end-to-end against real TESS data

- WASP-121 b recovered with period 1.27512d (0.015% error vs published
  1.27493d), SDE=32.48, depth 15530 ppm, 17 transits — on live Orb
  deployment, <30 seconds wall-clock.
- 154 unit tests + 5 integration tests passing.
- Full audit trail persisted per-candidate for reviewer scrutiny.
