# 🌿 Crop Suitability Engine

A multi-criteria decision support system (MCDSS) for mapping crop suitability across Kenyan counties. Built for agricultural researchers, county governments, and NGOs who need spatially explicit suitability analysis without GIS expertise.

Covers all **47 Kenyan counties**. Geography and agronomy are configured independently (a county × crop model), so biophysical data is fetched **on demand** from open sources the first time a county is analysed — nothing needs to be downloaded by hand.

> **Crop scope:** **cotton** is fully calibrated and enabled in the app. Nine further crop configs (maize, coffee, beans, sorghum, cassava, sunflower, sugarcane, millet, tea) ship in `config/crops/`, but per-crop normalization is not yet county+crop keyed, so they are **hidden from the selector** for now — see [Multi-crop: future work](#multi-crop-future-work). Re-enable them via the `ENABLED_CROPS` env var once calibrated.

## 🔗 Live demo & project links

| | |
|---|---|
| **Live app** | https://James-Ngei.github.io/Suitability-Engine |
| **Backend API** | https://suitability-engine.onrender.com ( [/docs](https://suitability-engine.onrender.com/docs) ) |
| **Project board** | https://trello.com/b/3HnieYVN/crop-suitability-engine |
| **Design & testing doc** | [DESIGN_AND_TESTING.md](DESIGN_AND_TESTING.md) — Part I: architecture, patterns, deployment & cost · Part II: testing & validation |
| **Demo video** | _15–20 min walkthrough — link to be added_ |

> **Note on cold starts:** the backend runs on Render's free tier and spins down when idle. The first request after inactivity takes ~20–60s while the dyno wakes and the active county's layers load; subsequent requests are fast.

---

## Table of Contents

- [Overview](#overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Data Requirements](#data-requirements)
- [Installation](#installation)
- [Running the Pipeline](#running-the-pipeline)
- [Running the App](#running-the-app)
- [API Reference](#api-reference)
- [PDF Report Generation](#pdf-report-generation)
- [Adding a New County](#adding-a-new-county)
- [Configuration Reference](#configuration-reference)
- [Testing](#testing)
- [Deployment](#deployment)
- [Data Sources](#data-sources)
- [Multi-crop: future work](#multi-crop-future-work)
- [Contributing](#contributing)

---

## Overview

The engine combines five biophysical criteria — elevation, rainfall, temperature, soil clay content, and slope — into a single 0–100 suitability score using **weighted overlay analysis**. Users can adjust criterion weights interactively and re-run the analysis in real time via the dashboard.

**Key capabilities:**

- Fuzzy membership normalization (trapezoidal, Gaussian, linear descending)
- Protected area constraint masking
- Interactive weight adjustment with instant re-analysis
- County boundary clipping and spatial alignment
- REST API serving analysis results as GeoTIFF and map-ready PNG tiles
- PDF report generation (summary or full) with RAG-grounded LLM narrative
- Sensitivity analysis to identify which criteria drive results most
- On-demand data fetch (Planetary Computer / NASA POWER / OpenStreetMap) with a Cloudflare R2 cache for fast cloud cold starts

**End-to-end pipeline:**

```
Fetch (PC/NASA/OSM) → Preprocess → Realign → Normalize → Clip → API → Dashboard
```

The fetch + pipeline runs automatically the first time a county is requested; prepared layers are then cached to R2 so future runs skip straight to serving.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Leaflet / react-leaflet, Axios |
| Backend API | FastAPI, Uvicorn |
| Geospatial | Rasterio, GeoPandas, NumPy, Shapely |
| Data sources | Microsoft Planetary Computer (COP-DEM GLO-30), NASA POWER (climate), OpenStreetMap (boundaries), fetched on demand |
| Visualization | Pillow (PNG tiles), Matplotlib, matplotlib-scalebar |
| Report Generation | ReportLab (PDF), RAG (ChromaDB → TF-IDF fallback), LLM narrative (Groq / Gemini / Anthropic / Ollama) |
| Storage | Cloudflare R2 (S3-compatible layer cache), local filesystem (results) |
| Config | Split JSON — `config/counties/` (geography) + `config/crops/` (agronomy) |
| Deployment | Render (API), Cloudflare R2 (data cache), GitHub Pages (frontend) |
| Testing / CI | pytest, GitHub Actions |

---

## Project Structure

```
suitability-engine/
│
├── config/
│   ├── counties/                # 47 county configs — geography only
│   │   ├── kitui.json
│   │   ├── bungoma.json
│   │   └── … (45 more)
│   └── crops/                   # 10 crop configs — agronomy (weights + thresholds)
│       ├── cotton.json
│       ├── maize.json
│       └── … (8 more)
│
├── src/
│   ├── config.py                # Loads + merges county × crop config; all scripts import from here
│   ├── pc_fetcher.py            # On-demand fetch: Planetary Computer + NASA POWER + OSM
│   ├── preprocess.py            # Reproject, clip raw rasters to boundary
│   ├── realign_to_boundary.py   # Snap all rasters to a shared pixel grid
│   ├── normalize.py             # Apply fuzzy membership functions (0–100)
│   ├── clip_to_boundary.py      # Final clip + regenerate constraints mask
│   ├── suitability.py           # Weighted overlay engine + statistics
│   ├── sensitivity_analysis.py  # One-at-a-time weight sensitivity tests
│   ├── map_renderer.py          # Static PNG map/chart rendering for reports
│   ├── report_writer.py         # PDF report builder (ReportLab + RAG + LLM)
│   ├── upload_to_r2.py          # Mirror prepared layers to Cloudflare R2
│   └── api.py                   # FastAPI backend
│
├── frontend/
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── App.js               # Root component + all app state
│       ├── App.css              # All styles
│       ├── hooks/
│       │   └── useSmoothedProgress.js
│       └── components/
│           ├── AnalysisSetup.js    # County + crop pickers + run button
│           ├── CountySelector.js   # County dropdown with load status
│           ├── CropSelector.js     # Crop dropdown
│           ├── FetchProgressBar.js # Live fetch/pipeline progress
│           ├── MapView.js          # Leaflet map + overlay + legend
│           ├── WeightControls.js   # Criterion weight sliders
│           ├── Statistics.js       # Score cards + classification bars
│           └── ReportPanel.js      # PDF report controls (depth, generate, view)
│
├── data/                        # Created at runtime — not committed
│   ├── counties/<county>/       # One dir per fetched county
│   │   ├── boundaries/          # County boundary (.gpkg, from OSM)
│   │   ├── raw/                 # Fetched rasters
│   │   ├── preprocessed/        # Clipped & reprojected
│   │   ├── processed/           # Aligned to shared grid
│   │   ├── normalized/          # 0–100 fuzzy scores
│   │   ├── results/<crop>/      # CLI analysis outputs
│   │   ├── sensitivity/<crop>/  # Sensitivity analysis outputs
│   │   └── api_results/<crop>/  # Per-request GeoTIFFs, PNGs, PDFs, metadata
│   ├── rag_docs/                # Agronomic docs for RAG (committed)
│   └── shared/
│       └── protected_areas_kenya.gpkg
│
├── tests/                       # pytest suite (see Testing)
├── .github/workflows/ci.yml     # GitHub Actions — runs tests on push/PR
├── deploy_check.py              # Pre-deploy validation script
├── render.yaml                  # Render deployment config
├── requirements.txt
└── README.md
```

> **Note:** the `data/` directory (except `data/rag_docs/`) is excluded from version control. Raster inputs are fetched automatically at runtime — see [Data Requirements](#data-requirements).

---

## Data Requirements

**No manual data download is required.** The first time a county is analysed, `pc_fetcher.py` fetches everything it needs and caches it under `data/counties/<county>/`:

| Layer | Description | Source (fetched automatically) |
|---|---|---|
| elevation | Digital Elevation Model (metres) | Copernicus DEM GLO-30 via [Planetary Computer](https://planetarycomputer.microsoft.com) |
| slope | Terrain slope (degrees) | Derived from the DEM |
| rainfall | Mean annual rainfall (mm/year) | [NASA POWER](https://power.larc.nasa.gov) (`PRECTOTCORR` × 365) |
| temperature | Mean annual temperature (°C) | [NASA POWER](https://power.larc.nasa.gov) (`T2M`) |
| soil | Soil clay content | [ISRIC SoilGrids](https://soilgrids.org) |
| boundary | County boundary polygon | [OpenStreetMap](https://www.openstreetmap.org) |

- **Protected areas** *(optional)*: `data/shared/protected_areas_kenya.gpkg` — download from [Protected Planet](https://www.protectedplanet.net). If absent, the constraint mask uses the county boundary only.

You may still drop pre-sourced rasters into `data/counties/<county>/raw/` (named `elevation.tif` or `<county>_elevation.tif` — both are auto-detected); if present, the fetch step is skipped for those layers.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/James-Ngei/Suitability-Engine.git
cd Suitability-Engine
```

### 2. Python environment

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### 4. (Optional) Choose the default county and crop

The app defaults to the first county alphabetically (`baringo`) and `cotton`. Override the defaults with environment variables (or `config/active_county.txt` / `config/active_crop.txt`):

```bash
export ACTIVE_COUNTY=kitui
export ACTIVE_CROP=cotton
```

At runtime you can also switch county/crop directly in the dashboard, or per request via the `?county=` / `?crop=` query parameters.

---

## Running the Pipeline

**You normally don't need to run this manually** — the API fetches data and runs the pipeline automatically the first time a county is requested (or via `POST /admin/load-county`). Run it by hand only to prepare a county offline or to re-generate after changing thresholds.

```bash
export ACTIVE_COUNTY=kitui         # county to prepare

# Step 0 — Fetch raw layers (Planetary Computer / NASA POWER / OSM)
python src/pc_fetcher.py --fetch

# Step 1 — Reproject, clip, and build constraint mask
python src/preprocess.py

# Step 2 — Snap all layers to a shared pixel grid
python src/realign_to_boundary.py

# Step 3 — Apply fuzzy membership functions (produces 0–100 scores)
python src/normalize.py

# Step 4 — Clip normalized layers to county boundary
python src/clip_to_boundary.py
```

Each script prints a progress summary and flags any missing files. Completed steps are skipped automatically.

**Optional — standalone CLI weighted overlay:**

```bash
python src/suitability.py
```

**Optional — sensitivity analysis:**

```bash
python src/sensitivity_analysis.py
# Outputs to data/counties/<county>/sensitivity/
```

---

## Running the App

### Start the API

```bash
python src/api.py
# → http://localhost:8000
# → Docs at http://localhost:8000/docs
```

### Start the frontend

```bash
cd frontend
npm start
# → http://localhost:3000
```

Open `http://localhost:3000`. The map centres on the active county and loads the boundary outline. Pick a county and crop, adjust the sliders, and click **Run Analysis** to generate a suitability overlay.

### Switching counties / crops

Use the **county and crop selectors** in the dashboard — the first time a county is selected, the API fetches and prepares its data in the background (progress is shown in the UI). No restart required. Programmatically, pass `?county=<name>&crop=<name>` to any data endpoint, or `POST /admin/load-county?county=<name>` to pre-warm a county.

---

## API Reference

Base URL: `http://localhost:8000` locally, or `https://suitability-engine.onrender.com` in production.
Data endpoints accept optional `?county=<name>&crop=<name>` query parameters to override the defaults per request.

### `GET /ping`
Lightweight liveness check (`{"status": "ok"}`) — used by the Render health check.

### `GET /`
Returns the API version, loaded counties, and available counties.

### `GET /health`
Overall status plus **per-county** load state (`idle` / `fetching` / `pipeline` / `loaded`) and layer counts.

### `GET /status/{county}`
Poll this to track fetch/pipeline progress for a single county.

### `GET /counties`
Lists all available counties with their current load status.

### `GET /crops`
Lists all available crops.

### `GET /county`
Returns county + crop metadata for the frontend.

```json
{
  "county": "kitui",
  "display_name": "Kitui County",
  "country": "Kenya",
  "crop": "Cotton",
  "map_center": [-1.37, 38.01],
  "map_zoom": 9,
  "weights": { "rainfall": 0.3, "elevation": 0.15, "temperature": 0.2, "soil": 0.2, "slope": 0.15 }
}
```

### `GET /criteria`
Returns per-criterion descriptions and optimal ranges.

### `GET /boundary-geojson`
Returns the county boundary as GeoJSON for the Leaflet overlay.

### `POST /analyze`
Runs weighted overlay and returns statistics plus an `analysis_id` for map/report assets.

**Request:**
```json
{
  "weights": {
    "rainfall": 0.30, "elevation": 0.15,
    "temperature": 0.20, "soil": 0.20, "slope": 0.15
  },
  "apply_constraints": true
}
```

**Response:**
```json
{
  "analysis_id": "20240115_143022",
  "county": "Kitui County",
  "raster_bounds": [[-3.2, 36.8], [0.4, 39.6]],
  "statistics": { "min": 0.0, "max": 98.4, "mean": 54.2, "std": 18.7, "median": 56.1 },
  "classification": {
    "highly_suitable_pct": 22.4,
    "moderately_suitable_pct": 35.1,
    "marginally_suitable_pct": 18.9,
    "not_suitable_pct": 8.3,
    "excluded_pct": 15.3
  },
  "weights_used": { "rainfall": 0.3 },
  "timestamp": "2024-01-15T14:30:22"
}
```

### `GET /map-image/{analysis_id}`
Returns the suitability raster as a transparent RGBA PNG for Leaflet's `ImageOverlay`.

| Score | Colour | Class |
|---|---|---|
| ≥ 70 | `#2d7a1b` dark green | Highly suitable |
| 50–70 | `#74b83e` light green | Moderate |
| 30–50 | `#e0a020` amber | Marginal |
| < 30 | `#d04030` red | Not suitable |
| 0 | Transparent | Excluded / no data |

### `POST /report/{analysis_id}?depth=full`
Generates and returns a PDF report. `depth` = `summary` (2 pages) or `full` (4 pages, default).

### `GET /report-assets/{analysis_id}/{asset_name}`
Serves rendered PNG assets: `suitability_map`, `criteria_grid`, `classification_chart`, `weight_chart`.

### `POST /render/{analysis_id}`
Re-renders all report assets for an existing analysis.

### `GET /results/{analysis_id}`
Returns the full metadata JSON for a previous analysis.

### `GET /download/{analysis_id}`
Downloads the suitability GeoTIFF.

### `POST /admin/load-county?county=<name>`
Fetches (if needed), runs the pipeline, and loads a county into memory in the background. Poll `GET /status/{county}` for progress.

### `POST /admin/reload`
Re-syncs from R2 (or re-fetches) and reloads normalized layers into memory without restarting.

---

## PDF Report Generation

Reports are generated via `POST /report/{analysis_id}` and built by `src/report_writer.py` using ReportLab.

### Report depths

| Depth | Pages | Contents |
|---|---|---|
| `summary` | 2 | Suitability map, score statistics, classification chart, LLM narrative, weights table |
| `full` | 4 | All of summary + individual criterion layer grid, weight chart, methodology section, data sources |

### LLM narrative providers

The narrative section is generated by an LLM. Provider is selected via the `LLM_PROVIDER` environment variable:

| Provider | Env var | Free tier |
|---|---|---|
| `groq` | `GROQ_API_KEY` | Yes — generous free tier (default) |
| `gemini` | `GEMINI_API_KEY` | Yes — 15 req/min |
| `anthropic` | `ANTHROPIC_API_KEY` | No — pay per token |
| `ollama` | `OLLAMA_BASE_URL` | Yes — local, no key needed |

If no provider is configured or all calls fail, the report falls back to a deterministic template — **the PDF never fails to build**.

### RAG-grounded narrative

At startup, `build_rag_store()` indexes any agronomic documents in `data/rag_docs/` (`.txt`, `.md`, or text-extractable `.pdf`) into a vector store — ChromaDB if available, otherwise a lightweight TF-IDF fallback. When a report is generated, the most relevant passages are retrieved and injected into the narrative prompt, grounding the LLM's recommendations in source literature (e.g. FAO crop guides). If `data/rag_docs/` is empty, RAG is silently disabled and the narrative is produced from the analysis statistics alone.

---

## Adding a New County

For a **new county**, you usually only need step 1 — data is fetched automatically:

1. **Create a county config** at `config/counties/<county>.json` (geography: boundary, map centre/zoom, bbox, resolution, layer filenames). Copy an existing one as a template.

2. Select the county in the dashboard (or `POST /admin/load-county?county=<county>`). The API fetches data, runs the pipeline, and loads it — no restart needed.

For a **new crop**, add `config/crops/<crop>.json` (agronomy: `normalization` thresholds, `weights` summing to 1.0, `criteria_info`). It immediately becomes selectable over any county.

No code changes are required — everything is driven by the split JSON configs.

---

## Configuration Reference

Configuration is split into two files. A **county config** (`config/counties/<county>.json`) holds geography only:

```jsonc
{
  "county": "kitui",            // Unique ID (lowercase, no spaces)
  "display_name": "Kitui County",
  "country": "Kenya",
  "map_center": [-1.37, 38.01], // [lat, lng]
  "map_zoom": 9,
  "resolution": 0.005,          // Pixel size in degrees (~500m at equator)
  "bbox": [37.0, -3.2, 39.0, -0.4],

  "layers": {                   // Raster filenames (in raw/)
    "elevation": "kitui_elevation.tif",
    "rainfall": "kitui_rainfall.tif",
    "temperature": "kitui_temperature.tif",
    "soil": "kitui_soil.tif",
    "slope": "kitui_slope.tif"
  }
}
```

A **crop config** (`config/crops/<crop>.json`) holds agronomy only — reused across all counties:

```jsonc
{
  "crop_id": "cotton",
  "display_name": "Cotton",

  "normalization": {
    "elevation": {
      "type": "trapezoidal",    // "trapezoidal" | "gaussian" | "linear_descending"
      "params": { "a": 200, "b": 400, "c": 1000, "d": 1500 },
      "description": "ASAL lowland cotton 400–1000m optimal"
    },
    "temperature": { "type": "gaussian", "params": { "optimal": 27, "spread": 5 } },
    "slope":       { "type": "linear_descending", "params": { "min_val": 0, "max_val": 15 } }
  },

  "weights": {                  // Must sum to 1.0
    "rainfall": 0.3, "elevation": 0.15, "temperature": 0.2, "soil": 0.2, "slope": 0.15
  },

  "criteria_info": {
    "rainfall": { "description": "Annual rainfall in mm/year", "optimal_range": "600–900 mm" }
  }
}
```

**Fuzzy function reference:**

| Type | Shape | Use when |
|---|---|---|
| `trapezoidal` | Flat top, linear shoulders | Criterion has a clear optimal range (e.g. rainfall, elevation) |
| `gaussian` | Bell curve | Criterion has a single optimal point (e.g. temperature) |
| `linear_descending` | Falls from min to max | Lower is always better (e.g. slope) |

---

## Testing

Automated tests live in `tests/` and run with **pytest**:

```bash
pip install -r requirements-dev.txt
pytest
```

Coverage focuses on the deterministic core of the engine:

| Area | What's covered |
|---|---|
| `normalize.py` | Fuzzy membership functions — plateaus, edges, symmetry, 0–100 clamping |
| `suitability.py` | Weighted-overlay arithmetic, classification thresholds, statistics, empty-raster safety |
| `config.py` | County/crop loading + merge; every crop's weights sum to 1.0 and use valid normalization types |
| `api.py` | FastAPI metadata endpoints and `/analyze` request validation via `TestClient` (offline — no data fetch) |

Every push and pull request runs two jobs via **GitHub Actions** (`.github/workflows/ci.yml`): the pytest suite on Python 3.11 and 3.12, and a **Deployment readiness** job that runs `deploy_check.py` to validate `render.yaml`, the config JSON, and the R2/fetch wiring before anything can reach production.

---

## Deployment

The API deploys on [Render](https://render.com); the frontend deploys to GitHub Pages. Prepared layers are cached in **Cloudflare R2** (S3-compatible) for fast cold starts — but R2 is optional: without it, the API just fetches from the open data sources on first use. See `deployment.md` for the full guide.

**Backend (Render):**

1. Push to GitHub — Render auto-deploys from `render.yaml` (`uvicorn src.api:app`).
2. Set these as secrets in the Render dashboard: `GROQ_API_KEY`, and (optional, for the R2 cache) `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
3. Verify: `curl https://suitability-engine.onrender.com/ping`.

**Frontend (GitHub Pages):** set `REACT_APP_API_URL` in `frontend/.env` to the Render URL, then:

```bash
cd frontend && npm run deploy   # builds and pushes to the gh-pages branch
```

**R2 cache layout** (populated automatically after a county is first prepared):
```
<bucket>/
└── kenya/<county>/
    ├── normalized/      normalized_*.tif
    ├── boundaries/      <county>_boundary.gpkg
    └── preprocessed/    <county>_constraints_mask.tif
```

To reload data without restarting: `POST /admin/reload`

---

## Data Sources

| Dataset | Source | License |
|---|---|---|
| Copernicus DEM GLO-30 (elevation) | [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com) | ESA / free & open |
| Rainfall & Temperature | [NASA POWER](https://power.larc.nasa.gov) | Free & open |
| SoilGrids Clay Content | [ISRIC](https://soilgrids.org) | CC BY 4.0 |
| County Boundaries | [OpenStreetMap](https://www.openstreetmap.org) | ODbL |
| Protected Areas | [Protected Planet](https://www.protectedplanet.net) | See terms |

---

## Multi-crop: future work

The system is architected around a **county × crop** model — county geography and crop agronomy live in separate configs, and any crop *can* in principle be analysed over any county. **Cotton is fully calibrated and is the only crop enabled in the deployed app.**

The remaining nine crop configs are intentionally hidden from the selector because normalized rasters are currently cached per **county**, not per **county + crop**: the preprocessing pipeline normalizes each county's layers with the default crop's (cotton's) fuzzy thresholds, so selecting another crop would reuse cotton's normalization with only the weights swapped — agronomically incorrect. Enabling them correctly requires:

- Keying normalized layers (and the R2 cache prefix) by county + crop
- Passing the selected crop into the preprocessing pipeline (`_run_pipeline`)
- Keying the in-memory `COUNTY_CACHE` by (county, crop)

Until then, `GET /crops` returns only the crops listed in the `ENABLED_CROPS` env var (default `cotton`; set to `all` or a comma-separated list to expose more). This keeps the deployed app correct and demonstrable while leaving the multi-crop groundwork in place.

---

## Contributing

Contributions are welcome. Useful areas to extend:

- More county or cross-border region configs
- Additional crop configs beyond the current 10
- Alternative normalization functions (sigmoid, piecewise linear)
- Multi-crop comparison view in the dashboard (compare two crops side by side)
- Time-series analysis using multiple rainfall/temperature rasters
- API authentication for the `/admin/*` endpoints

Please open an issue before submitting a pull request for significant changes.

---

*Built with FastAPI · React · Rasterio · Leaflet · ReportLab*