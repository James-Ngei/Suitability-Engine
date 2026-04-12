# 🌿 Crop Suitability Engine

A multi-criteria decision support system (MCDSS) for mapping crop suitability across Kenyan counties. Built for agricultural researchers, county governments, and NGOs who need spatially explicit suitability analysis without GIS expertise.

Currently configured for **cotton** across **Kitui** and **Bungoma** counties, with an architecture designed to onboard any crop and any county from a single JSON config file.

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
- [Deployment](#deployment)
- [Data Sources](#data-sources)
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
- PDF report generation (summary or full) with LLM-generated narrative
- Sensitivity analysis to identify which criteria drive results most
- S3-backed data storage for cloud deployment on Render

**Analysis pipeline:**

```
Raw rasters → Preprocess → Realign → Normalize → Clip → API → Dashboard
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, Leaflet / react-leaflet, Axios |
| Backend API | FastAPI, Uvicorn |
| Geospatial | Rasterio, GeoPandas, NumPy, Shapely |
| Visualization | Pillow (PNG tiles), Matplotlib, matplotlib-scalebar |
| Report Generation | ReportLab (PDF), LLM narrative (Groq / Gemini / Anthropic / Ollama) |
| Storage | AWS S3 (raster data), local filesystem (results) |
| Config | JSON per county, plain-text active county pointer |
| Deployment | Render (API), S3 (data), static site or local (frontend) |

---

## Project Structure

```
suitability-engine/
│
├── config/
│   ├── active_county.txt        # Set this to switch counties
│   ├── kitui.json               # Kitui county config
│   └── bungoma.json             # Bungoma county config
│
├── src/
│   ├── config.py                # Config loader — all scripts import from here
│   ├── preprocess.py            # Reproject, clip raw rasters to boundary
│   ├── realign_to_boundary.py   # Snap all rasters to a shared pixel grid
│   ├── normalize.py             # Apply fuzzy membership functions (0–100)
│   ├── clip_to_boundary.py      # Final clip + regenerate constraints mask
│   ├── suitability.py           # Weighted overlay engine + statistics
│   ├── sensitivity_analysis.py  # One-at-a-time weight sensitivity tests
│   ├── map_renderer.py          # Static PNG map/chart rendering for reports
│   ├── report_writer.py         # PDF report builder (ReportLab + LLM)
│   └── api.py                   # FastAPI backend
│
├── frontend/
│   ├── public/
│   │   └── index.html
│   └── src/
│       ├── App.js               # Root component, 3-column layout
│       ├── App.css              # All styles
│       └── components/
│           ├── MapView.js       # Leaflet map + overlay + legend
│           ├── WeightControls.js # Criterion weight sliders
│           ├── Statistics.js    # Score cards + classification bars
│           └── ReportPanel.js   # PDF report controls (depth, generate, view)
│
├── data/                        # Created at runtime — not committed
│   ├── counties/
│   │   └── kitui/
│   │       ├── boundaries/      # County boundary (.gpkg)
│   │       ├── raw/             # Downloaded rasters
│   │       ├── preprocessed/    # Clipped & reprojected
│   │       ├── processed/       # Aligned to shared grid
│   │       ├── normalized/      # 0–100 fuzzy scores
│   │       ├── results/         # CLI analysis outputs
│   │       ├── sensitivity/     # Sensitivity analysis outputs
│   │       └── api_results/     # Per-request GeoTIFFs, PNGs, PDFs, metadata
│   └── shared/
│       └── protected_areas_kenya.gpkg
│
├── deploy_check.py              # Pre-deploy validation script
├── render.yaml                  # Render deployment config
├── requirements.txt
└── README.md
```

> **Note:** the `data/` directory is excluded from version control via `.gitignore`. All raster inputs must be sourced and placed locally — see [Data Requirements](#data-requirements).

---

## Data Requirements

Each county needs five raster layers (GeoTIFF format) placed in `data/counties/<county>/raw/`:

| File | Description | Recommended Source |
|---|---|---|
| `<county>_elevation.tif` | Digital Elevation Model (metres) | SRTM 30m via [OpenTopography](https://opentopography.org) |
| `<county>_rainfall.tif` | Mean annual rainfall (mm/year) | [CHIRPS](https://www.chc.ucsb.edu/data/chirps) |
| `<county>_temperature.tif` | Mean annual temperature (°C) | [WorldClim v2](https://www.worldclim.org/data/worldclim21.html) |
| `<county>_soil.tif` | Soil clay content (g/kg) | [SoilGrids 250m](https://soilgrids.org) |
| `<county>_slope.tif` | Terrain slope (degrees) | Derived from DEM using GDAL or QGIS |

You also need:

- **County boundary**: `data/counties/<county>/boundaries/<county>_boundary.gpkg` — Kenya county boundaries available from [GADM](https://gadm.org) or the [Kenya Open Data portal](https://opendata.go.ke).
- **Protected areas** *(optional)*: `data/shared/protected_areas_kenya.gpkg` — download from [Protected Planet](https://www.protectedplanet.net). If absent, the constraint mask uses the county boundary only.

Raw files can be named either `elevation.tif` or `kitui_elevation.tif` — the pipeline auto-detects both.

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/suitability-engine.git
cd suitability-engine
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

### 4. Set the active county

```bash
echo "kitui" > config/active_county.txt
```

---

## Running the Pipeline

Run these steps **once** after placing raw data for a county. Re-run only if source data or normalization thresholds change.

```bash
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

Open `http://localhost:3000`. The map will centre on the active county and load the boundary outline. Adjust the sliders and click **Run Analysis** to generate a suitability overlay.

### Switching counties

```bash
echo "bungoma" > config/active_county.txt
# Restart the API — frontend picks up the new config automatically
python src/api.py
```

---

## API Reference

All endpoints are prefixed with `http://localhost:8000`.

### `GET /`
Returns a summary of all available endpoints.

### `GET /health`
Returns API status, loaded layer count, raster bounds, and S3 bucket info.

### `GET /county`
Returns active county metadata for the frontend.

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

### `POST /admin/reload`
Re-syncs from S3 and reloads normalized layers into memory without restarting.

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

### RAG upgrade slot

`generate_narrative()` accepts a `rag_context` string. Pass retrieved methodology or agronomic context chunks to ground the LLM in domain-specific material. The rest of the pipeline is unchanged.

---

## Adding a New County

1. **Create a config file** at `config/<county>.json`. Copy an existing one as a template and update all fields (see [Configuration Reference](#configuration-reference)).

2. **Place raw rasters** in `data/counties/<county>/raw/`

3. **Place the boundary** at `data/counties/<county>/boundaries/<county>_boundary.gpkg`

4. **Set as active and run the pipeline:**
   ```bash
   echo "<county>" > config/active_county.txt
   python src/preprocess.py
   python src/realign_to_boundary.py
   python src/normalize.py
   python src/clip_to_boundary.py
   ```

5. **Restart the API** — the frontend updates automatically.

No code changes are required. Everything is driven by the JSON config.

---

## Configuration Reference

```jsonc
{
  "county": "kitui",            // Unique ID (lowercase, no spaces)
  "display_name": "Kitui County",
  "country": "Kenya",
  "crop": "Cotton",
  "map_center": [-1.37, 38.01], // [lat, lng]
  "map_zoom": 9,
  "resolution": 0.005,          // Pixel size in degrees (~500m at equator)

  "layers": {
    "elevation": "kitui_elevation.tif"  // Filename in raw/ directory
  },

  "normalization": {
    "elevation": {
      "type": "trapezoidal",    // "trapezoidal" | "gaussian" | "linear_descending"
      "params": { "a": 200, "b": 400, "c": 1000, "d": 1500 },
      "description": "ASAL lowland cotton 400–1000m optimal"
    },
    "temperature": {
      "type": "gaussian",
      "params": { "optimal": 27, "spread": 5 }
    },
    "slope": {
      "type": "linear_descending",
      "params": { "min_val": 0, "max_val": 15 }
    }
  },

  "weights": {
    "rainfall": 0.3             // Must sum to 1.0
  },

  "criteria_info": {
    "rainfall": {
      "description": "Annual rainfall in mm/year",
      "optimal_range": "600–900 mm"
    }
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

## Deployment

The API is designed to deploy on [Render](https://render.com) with raster data stored in AWS S3. See `deployment.md` for the full step-by-step guide.

**Quick summary:**

1. Run `python deploy_check.py` — all checks must pass before pushing.
2. Upload county data to S3 following the required folder structure.
3. Push to GitHub — Render auto-deploys from `render.yaml`.
4. Set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` as secrets in the Render dashboard.
5. Verify with `curl https://your-service.onrender.com/health`.

**S3 bucket layout:**
```
suitability-engine/
└── kitui/
    ├── normalized/      normalized_*.tif
    ├── boundary/        kitui_boundary.gpkg
    ├── constraints/     protected_areas_kenya.gpkg
    ├── preprocessed/    kitui_constraints_mask.tif
    └── results/         ← written back after each analysis
```

To reload data without restarting: `POST /admin/reload`

---

## Data Sources

| Dataset | Source | License |
|---|---|---|
| SRTM Elevation | [OpenTopography](https://opentopography.org) | CC BY 4.0 |
| CHIRPS Rainfall | [UCSB CHC](https://www.chc.ucsb.edu/data/chirps) | Public domain |
| WorldClim Temperature | [WorldClim](https://www.worldclim.org) | CC BY 4.0 |
| SoilGrids Clay Content | [ISRIC](https://soilgrids.org) | CC BY 4.0 |
| Kenya County Boundaries | [GADM](https://gadm.org) | Academic use |
| Protected Areas | [Protected Planet](https://www.protectedplanet.net) | See terms |

---

## Contributing

Contributions are welcome. Useful areas to extend:

- Additional crop configs (maize, sorghum, cassava, sunflower)
- More county or cross-border region configs
- Alternative normalization functions (sigmoid, piecewise linear)
- RAG integration for LLM narrative (connect agronomic knowledge base)
- Multi-crop comparison view in the dashboard
- Export to PDF report from the frontend without API round-trip
- Time-series analysis using multiple rainfall/temperature rasters

Please open an issue before submitting a pull request for significant changes.

---

*Built with FastAPI · React · Rasterio · Leaflet · ReportLab*