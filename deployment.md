# Deployment Guide — Suitability Engine

## Overview

```
GitHub repo ──► Render (FastAPI)  ◄── Cloudflare R2 (prepared-layer cache, optional)
                     ▲   │
                     │   └──► Open data sources (Planetary Computer / NASA POWER / OSM)
                     │             (on-demand fetch, first time a county is used)
              React frontend
              (GitHub Pages)
```

The API loads county layers into memory **per county**. Startup returns
immediately (so Render's health check passes at once); the active county's
data is prepared in the background. There is no database.

**Two data paths:**
- **Fast path** — if a county's prepared layers already exist in Cloudflare R2, they are synced down in seconds.
- **Cold path** — the first time a county is ever requested, raw data is fetched from the open sources, run through the pipeline, and then uploaded to R2 so every future start uses the fast path.

R2 is **optional**: without R2 credentials the API simply fetches from the open
sources on first use (slower cold start, but fully functional).

---

## Step 1 — Verify your repo structure

```
suitability-engine/
├── config/
│   ├── counties/                ← 47 county configs (geography)
│   └── crops/                   ← 10 crop configs (agronomy)
├── src/
│   ├── api.py                   ← FastAPI app (R2 sync + PC fetch)
│   ├── config.py                ← __file__-relative CONFIG_DIR, county × crop merge
│   ├── pc_fetcher.py            ← on-demand data fetch
│   ├── upload_to_r2.py          ← mirror prepared layers to R2
│   └── ... (pipeline scripts)
├── frontend/                    ← React app (deployed to GitHub Pages)
├── render.yaml                  ← Render config (src.api:app, /ping health check)
├── requirements.txt
├── deploy_check.py              ← run this before every deploy
└── .gitignore                   ← data/ and *.tif excluded
```

Run the pre-deploy check:
```bash
python deploy_check.py
```
All checks must be ✅ before continuing. This same check also runs automatically
in CI (the **Deployment readiness** job in `.github/workflows/ci.yml`) on every
push and pull request, so `main` is always known to be deploy-ready.

---

## Step 2 — (Optional) Prepare the Cloudflare R2 cache

R2 gives fast cold starts by caching prepared layers. Skip this step to run
purely on on-demand fetch.

1. Create an R2 bucket (e.g. `suitability-engine`) in the Cloudflare dashboard.
2. Create an R2 API token with **Object Read & Write** on that bucket; note the
   **Account ID**, **Access Key ID**, and **Secret Access Key**.
3. Prepare and upload one or more counties from your machine:
   ```bash
   export ACTIVE_COUNTY=kitui
   python src/pc_fetcher.py --fetch        # fetch raw layers
   python src/preprocess.py
   python src/realign_to_boundary.py
   python src/normalize.py
   python src/clip_to_boundary.py
   python src/upload_to_r2.py --county kitui
   ```

The API also uploads to R2 automatically after preparing any county at runtime,
so this manual step only front-loads the work.

**R2 layout** (created automatically):
```
<bucket>/
└── kenya/<county>/
    ├── normalized/       normalized_*.tif
    ├── boundaries/       <county>_boundary.gpkg
    └── preprocessed/     <county>_constraints_mask.tif
```

---

## Step 3 — Push to GitHub

```bash
git add -A
git commit -m "deploy: <what changed>"
git push origin main
```

Make sure these are NOT committed (check `.gitignore`):
- `data/` directory (rasters) — except `data/rag_docs/`
- `*.tif`, `*.gpkg`, `*.geojson` files
- `venv/`, `node_modules/`, `frontend/build/`

---

## Step 4 — Deploy the API on Render

### 4a. Create the Web Service

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub repo
3. Render auto-detects `render.yaml` — confirm the settings:

| Setting | Value |
|---|---|
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn src.api:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/ping` |

### 4b. Set secret environment variables

In the Render dashboard → **Environment** tab, add these manually
(they are marked `sync: false` in `render.yaml`, so they are never stored in git):

| Key | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq API key (for the LLM narrative) |
| `R2_ACCOUNT_ID` | Cloudflare account ID *(only if using R2)* |
| `R2_ACCESS_KEY_ID` | R2 access key *(only if using R2)* |
| `R2_SECRET_ACCESS_KEY` | R2 secret *(only if using R2)* |

Already set in `render.yaml` (non-secret):

| Key | Value |
|---|---|
| `SUITABILITY_DATA_DIR` | `/tmp/suitability-engine` |
| `ACTIVE_COUNTY` | `baringo` |
| `ACTIVE_CROP` | `cotton` |
| `LLM_PROVIDER` | `groq` |
| `R2_BUCKET` | `suitability-engine` |

> Without the R2 keys the service still runs — it fetches from the open data
> sources on first use instead of syncing from R2.

---

## Step 5 — Verify the deploy

Once Render shows **Live** (replace the URL with your service's URL):

```bash
# 1. Liveness — responds instantly, even during startup
curl https://suitability-engine.onrender.com/ping
# → {"status": "ok"}

# 2. Per-county health — the active county moves idle → fetching/pipeline → loaded
curl https://suitability-engine.onrender.com/health

# 3. Poll a single county's preparation progress
curl https://suitability-engine.onrender.com/status/baringo

# 4. Once a county reports "loaded", run a test analysis
curl -X POST "https://suitability-engine.onrender.com/analyze?county=baringo&crop=cotton" \
  -H "Content-Type: application/json" \
  -d '{
    "weights": { "rainfall": 0.30, "elevation": 0.15,
                 "temperature": 0.20, "soil": 0.20, "slope": 0.15 },
    "apply_constraints": true
  }'
```

On a cold start with no R2 cache, the first county can take tens of seconds to a
few minutes to fetch + prepare. Watch the Render logs — every fetch, sync, and
pipeline step is logged.

---

## Step 6 — Deploy the frontend (GitHub Pages)

1. Point the frontend at your API in `frontend/.env`:
   ```
   REACT_APP_API_URL=https://suitability-engine.onrender.com
   ```
2. Build and publish to the `gh-pages` branch:
   ```bash
   cd frontend
   npm install
   npm run deploy        # gh-pages -d build
   ```
3. In the GitHub repo → **Settings → Pages**, confirm the source is the
   `gh-pages` branch. The app is served at the `homepage` URL in
   `frontend/package.json` (e.g. `https://James-Ngei.github.io/Suitability-Engine`).

---

## Ongoing operations

### Switching the default county / crop

Change `ACTIVE_COUNTY` / `ACTIVE_CROP` in the Render env vars → redeploy. At
runtime, users switch county/crop in the dashboard, or clients pass
`?county=&crop=` per request — no redeploy needed.

### Preparing / refreshing a county

```bash
# Prepare an additional county on the running service (background):
curl -X POST "https://suitability-engine.onrender.com/admin/load-county?county=bungoma"

# Re-sync + reload the active county after updating its R2 data:
curl -X POST "https://suitability-engine.onrender.com/admin/reload"
```

### Cold-start behaviour

Render's free/starter tier spins down after inactivity. On the next request:
- `/ping` responds immediately; the county loads in the background
- Fast path (R2 cache present): layers sync in seconds
- Cold path (never prepared): full fetch + pipeline (tens of seconds to minutes), then cached to R2

Use Render's **paid tier** or a **cron job pinging `/ping`** every ~10 minutes to
keep the service warm for demos.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| County stuck `fetching` / `error` in `/status` | Data source unreachable, or R2 keys wrong | Check Render logs; verify R2 creds or that PC/NASA/OSM are reachable |
| `status: degraded` on `/health` | No county loaded yet | Normal right after cold start — wait for the active county to reach `loaded` |
| `/analyze` returns 404/503 | County not loaded yet | Poll `/status/{county}`; `POST /admin/load-county?county=<name>` to prepare it |
| Frontend CORS / no data | `REACT_APP_API_URL` wrong or empty | Set it in `frontend/.env` and re-run `npm run deploy` |
| Render deploy fails | Wrong start command | Must be `uvicorn src.api:app ...`, not `uvicorn api:app ...` |
| Narrative is the fallback template | No `GROQ_API_KEY` (or all LLM providers failing) | Set `GROQ_API_KEY` in Render env vars |
