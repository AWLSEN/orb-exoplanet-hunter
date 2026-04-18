# web — public-facing Vercel dashboard

A single self-contained `index.html`: fetches live JSON from the Orb-hosted API (`https://cc200989.orbcloud.dev`) and renders a visually intuitive dashboard for visitors with zero context.

Design echoes the `orb-antibiotic-scientist.vercel.app` pattern — Instrument Serif headlines, JetBrains Mono data, dark background with soft cyan glow — and adds the two pieces that specifically motivate Orb Cloud:

1. **"What it's looking for"** — an annotated SVG of a synthetic folded light curve so a non-astronomer visitor understands what a "transit" even is.
2. **"Billed for working, not waiting"** — a 10-minute-slice grid of the last 16 hours showing agent-active windows (from candidate `discovered_at` + health-check `ran_at` timestamps) versus frozen slices. The visualization **is** the cost pitch.

## Deploy

```bash
cd web
vercel --prod
```

That's the whole story — `index.html` + `vercel.json` + this README. No build step, no framework, no npm install.

## Dev-loop

```bash
# Serve locally (any static server works):
python3 -m http.server 5000 --directory web
# then open http://localhost:5000
```

Change the `API` constant at the top of `<script>` in `index.html` to point at a different API origin (e.g. a local orchestrator) during development.

## CORS

The FastAPI orchestrator adds `Access-Control-Allow-Origin: *` for `GET` + `POST` via `fastapi.middleware.cors.CORSMiddleware`, so this page can fetch `/candidates`, `/health`, `/pipeline-health` directly from the browser without a proxy.
