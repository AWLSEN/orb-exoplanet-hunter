# orb-exoplanet-hunter

**An autonomous agent that hunts for new exoplanets in TESS data, 24/7, for pennies.**

> Dashboard: https://orb-exoplanet-hunter.vercel.app
> API: https://6f96c5fc.orbcloud.dev

This repo runs a continuous pipeline on [Orb Cloud](https://orbcloud.dev):
ingest → detrend → transit-search → vet-chain → recurrence → publish. The agent sleeps to NVMe between TESS sector drops (~27 days) and wakes to process the fresh sector plus chase candidates seen in earlier sectors. Dashboard shows a live candidate list, pipeline-health panel, and a one-click hunt endpoint.

Not a replacement for peer review. An industrial-scale, continuously-running **candidate surfacer** — the same thing Planet Hunters and amateur astronomers do, at machine scale and open to anyone.

## Try it

```bash
curl -X POST "https://6f96c5fc.orbcloud.dev/hunt/target?tic=22529346"
```

That processes WASP-121 (TIC 22529346) end-to-end against real TESS data: fetch from MAST, detrend, transit-search, vet, score, write candidate JSON. ~30 seconds.

## Live endpoints

- `GET  /`                 — dashboard (HTML, auto-refresh)
- `GET  /health`           — liveness + halt flag
- `GET  /candidates`       — all candidates, filterable by `?tier=` and `?min_score=`
- `GET  /candidates/{tic}` — full candidate record including per-gate audit trail
- `GET  /pipeline-health`  — last health-check report
- `POST /hunt/target?tic=` — trigger synchronous hunt for one TIC

## Niche

**M-dwarf habitable-zone candidates.** The big pipelines (SPOC, QLP) are tuned for brighter targets and shorter periods; faint M-dwarfs in TESS Full-Frame Images are under-searched, and M-dwarf HZ planets are the most accessible "potentially habitable" regime (short orbital periods, favorable transit geometry, lots of data).

## Data source

[TESS](https://tess.mit.edu) via the [MAST archive](https://mast.stsci.edu). Open, free, no auth. Accessed with the community-standard [`lightkurve`](https://docs.lightkurve.org) Python library. Supplemented with Gaia DR3 (astrometry + stellar params) and the NASA Exoplanet Archive (known TOI cross-match).

## Pipeline

1. **Ingest** — M-dwarf FFI cutouts per sector via `lightkurve.TessTargetPixelFile`
2. **Detrend** — `wotan` biweight (or Gaussian Process for noisy stars)
3. **Search** — Transit Least Squares (`transitleastsquares`) with SDE ≥ 8 shortlist
4. **Vet** — 9-gate chain (odd/even depth, secondary, centroid, ephemeris, Gaia RUWE, …)
5. **Validate** — TRICERATOPS False Positive Probability (FPP < 0.1)
6. **Recur** — single-sector = `candidate`; 2+ sectors agreeing on ephemeris = `confirmed`
7. **Publish** — JSON + downloadable vetting report PDF on the dashboard

## Verification

Two concentric loops (runs in parallel to the hunt):

- **Per-candidate:** 10 hard gates; one fails = candidate dropped.
- **Pipeline-health (nightly):** known-planet recovery, injection-recovery sweep, null-data FP rate, ephemeris consistency, depth distribution sanity, weekly Claude red-team review. Any hard check fails → pipeline goes read-only and an alert fires.

See `verification/README.md` once batch 4 ships.

## Status

Pre-alpha; being built in batches of 5 commits with a push at 5. See [`learnings.txt`](learnings.txt) for the running build log.

## License

MIT. All code open, all data public, all findings freely reproducible.
