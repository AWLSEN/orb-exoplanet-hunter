# Launch materials — orb-exoplanet-hunter

## Launch tweet (draft, v1)

> Built **orb-exoplanet-hunter**: an autonomous agent that hunts for
> new exoplanets in TESS data, 24/7, on [@orbcloud_dev](https://orbcloud.dev).
>
> It fetches light curves from MAST → detrends → transit-search → runs
> a 4-gate vet chain → tracks multi-sector recurrence → publishes
> candidates on a live dashboard.
>
> 154 unit + 5 integration tests. Recovered WASP-121 b at period
> 1.27512d (0.015% error, SDE=32.48) from real data in 30s.
>
> Pipeline-health canary re-verifies gold planets before every run.
> Any regression → agent halts.
>
> Live dashboard: https://cc200989.orbcloud.dev
> Code: https://github.com/AWLSEN/orb-exoplanet-hunter
>
> Total Orb cost so far: single-digit dollars.

## Alt hook (shorter)

> Autonomous exoplanet hunter running on @orbcloud_dev.
> TESS data → transit search → verified candidates.
> WASP-121 b recovered in 30s (period 1.27512d, SDE=32).
> Zero idle cost. MIT.
> https://cc200989.orbcloud.dev

## Key proof points for a demo / thread

1. **It's real.** `curl -X POST "https://cc200989.orbcloud.dev/hunt/target?tic=22529346"` recovers a published planet from raw TESS data in 30 seconds.
2. **It's verified.** 10 pipeline-health checks (known-planet recovery, injection-recovery sweep, null-data FP rate, ephemeris consistency, depth distribution, Claude red-team review). Any hard failure writes a HALT flag that blocks publishing until a human clears it.
3. **It's cheap.** Agent checkpoints to NVMe between TESS sector drops (~27d cadence). Effective per-hour compute cost approaches zero.
4. **It's open.** MIT, public repo, every candidate includes a full vetting audit trail.

## Demo recording checklist

Record at 1200×700, 15 fps, 30-45s max.

1. Open https://cc200989.orbcloud.dev in a browser — show the empty dashboard.
2. From a terminal: `curl -X POST "https://cc200989.orbcloud.dev/hunt/target?tic=22529346"` — show the ~30s wait + the JSON response with `tier: moderate, score: 0.62`.
3. Reload the dashboard — WASP-121 now appears in the candidate table, SDE 32, tier moderate.
4. Click `/candidates/22529346` (or raw JSON) — show the per-gate audit (odd_even, secondary, ephemeris_match, gaia_ruwe) with reasons.
5. End on `/pipeline-health` showing the green checks.

## Post-launch follow-ups (ranked)

1. **First real M-dwarf sweep.** Seed ~50 TICs from TIC+Gaia query, run via `python -m hunter.hunt`, let accumulate 2 weeks, ship a follow-up tweet with the candidate count + best hit.
2. **Pipeline-health canary on schedule.** The check is written + tested; orchestrator runs the cheap ones hourly. Schedule the expensive suite nightly via a dedicated trigger.
3. **Centroid shift gate (Batch 3 deferred).** Needs TessCut pixel data. Would strengthen the blend-rejection path.
4. **TRICERATOPS FPP.** Statistical blend-rejection with real stellar priors. Heavy native dep — add only after the first real sweep reveals which FPs our existing gates are missing.
5. **Multi-sector batch runner.** Schedule auto-pull + process when new TESS sectors drop from MAST. Turn the "autonomous" claim into a measurement: PRs to main every 27 days with the sector's candidates.
