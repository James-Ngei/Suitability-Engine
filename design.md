# System Design — Crop Suitability Engine

*Version 2.1 · Last updated April 2026*

---

## Table of Contents

- [1. System Overview](#1-system-overview)
- [2. Architecture](#2-architecture)
- [3. Data Pipeline](#3-data-pipeline)
- [4. Backend API](#4-backend-api)
- [5. Frontend](#5-frontend)
- [6. Report Generation](#6-report-generation)
- [7. Configuration System](#7-configuration-system)
- [8. Storage & Deployment](#8-storage--deployment)
- [9. Key Design Decisions](#9-key-design-decisions)
- [10. Known Limitations & Future Work](#10-known-limitations--future-work)

---

## 1. System Overview

The Crop Suitability Engine is a multi-criteria decision analysis (MCDA) system that produces georeferenced suitability maps for agricultural planning. Given a set of biophysical raster layers and user-defined criterion weights, it computes a continuous 0–100 suitability score across a county's land area, classifies that score into four suitability tiers, and exposes the results via a REST API consumed by an interactive dashboard.

The system is designed for a specific operational context:

- **Users** are agricultural analysts and county government officers, not GIS specialists.
- **Counties** are onboarded entirely through JSON config files — no code changes.
- **Data** is large (multi-MB rasters) and static between analyses; it lives in S3 and is loaded into memory at API startup.
- **Analyses** are fast (sub-second weighted overlay on in-memory arrays) once data is loaded.
- **Reports** are generated on demand and include an LLM-written narrative section.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Client Browser                         │
│                                                               │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│  │WeightControls│   │   MapView    │   │Statistics +      │  │
│  │  (sliders)  │   │  (Leaflet)   │   │ReportPanel       │  │
│  └──────┬──────┘   └──────┬───────┘   └────────┬─────────┘  │
│         └─────────────────┴────────────────────┘            │
│                         App.js                               │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP (Axios / fetch)
┌──────────────────────────▼───────────────────────────────────┐
│                      FastAPI Backend                          │
│                                                               │
│  /county  /criteria  /boundary-geojson                       │
│  /analyze  →  weighted overlay  →  save GeoTIFF              │
│              →  render_all()    →  save PNGs                  │
│  /map-image  →  render RGBA PNG from GeoTIFF                  │
│  /report     →  build_report()  →  LLM narrative  →  PDF     │
│  /admin/reload  →  S3 sync  →  reload layers                  │
│                                                               │
│  In-memory layer cache: { layer_name: np.ndarray }           │
└──────────────┬───────────────────────────────────────────────┘
               │                               │
┌──────────────▼──────────┐     ┌──────────────▼──────────────┐
│        AWS S3           │     │      LLM Provider API        │
│                         │     │  (Groq / Gemini / Anthropic  │
│  kitui/normalized/*.tif │     │   / Ollama)                  │
│  kitui/boundary/*.gpkg  │     └─────────────────────────────┘
│  kitui/constraints/     │
│  kitui/preprocessed/    │
│  kitui/results/         │
└─────────────────────────┘
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| `config.py` | Single source of truth for all paths and settings. All other modules import from here. |
| Pipeline scripts | One-time data preparation: reproject → align → normalize → clip. Idempotent — skip completed steps. |
| `api.py` | Serves county metadata, runs weighted overlays, renders images, coordinates report generation. Stateless between requests (all state in arrays or files). |
| `map_renderer.py` | Renders GeoTIFFs and statistics as publication-quality Matplotlib PNGs for reports. |
| `report_writer.py` | Assembles ReportLab PDF from rendered assets + LLM narrative. Falls back to template if LLM unavailable. |
| `App.js` | Orchestrates all frontend state. Passes callbacks down to stateless child components. |
| `MapView.js` | Leaflet map with boundary GeoJSON overlay and suitability `ImageOverlay`. |

---

## 3. Data Pipeline

The pipeline transforms raw rasters from heterogeneous sources into a consistent, analysis-ready set of normalized layers. It runs once per county setup and only needs to be re-run when source data or thresholds change.

### Stage 1 — Preprocess (`preprocess.py`)

**Input:** Raw GeoTIFFs in `data/counties/<county>/raw/`  
**Output:** Reprojected, county-clipped GeoTIFFs in `preprocessed/`

- Auto-detects plain or county-prefixed filenames (`elevation.tif` or `kitui_elevation.tif`)
- Reprojects all layers to `EPSG:4326`
- Clips to county boundary polygon using `rasterio.mask`
- Builds the constraints mask: a binary raster marking pixels inside the county boundary and outside any protected areas
- Elevation is processed first as the reference for the constraint mask dimensions

### Stage 2 — Realign (`realign_to_boundary.py`)

**Input:** Preprocessed layers  
**Output:** Pixel-aligned GeoTIFFs in `processed/aligned_*.tif`

All layers are reprojected to a shared pixel grid snapped to the county boundary extent at the configured resolution (default `0.005°` ≈ 500 m for Kitui, `0.01°` for Bungoma). Snapping is done by flooring/ceiling the boundary extent to resolution boundaries before computing the target transform. This ensures every pixel in every layer represents exactly the same geographic area, which is a hard requirement for weighted overlay.

### Stage 3 — Normalize (`normalize.py`)

**Input:** Aligned layers  
**Output:** 0–100 float32 GeoTIFFs in `normalized/`

Each layer is transformed through a fuzzy membership function defined in the county config:

**Trapezoidal** — for criteria with a defined optimal range (e.g. rainfall 600–900 mm):
```
0 before a | linear rise a→b | plateau at 100 b→c | linear fall c→d | 0 after d
```

**Gaussian** — for criteria with a single optimum (e.g. temperature at 27°C):
```
score = 100 × exp(-((value - optimal)² / (2 × spread²)))
```

**Linear descending** — for criteria where lower is always better (e.g. slope):
```
score = 100 × (max_val - value) / (max_val - min_val), clamped to [0, 100]
Pixels above max_val are hard-zeroed.
```

Nodata pixels (value == 0) are preserved as 0 throughout.

### Stage 4 — Clip (`clip_to_boundary.py`)

**Input:** Normalized layers  
**Output:** County-clipped normalized layers (in-place) + regenerated constraint mask

A final clip using the boundary polygon removes any pixels that leaked outside the county during the alignment step. The constraint mask is regenerated from the clipped reference layer dimensions to ensure exact spatial correspondence.

### Pipeline idempotency

Every pipeline script checks whether its output files already exist and skips completed layers. This means partial runs are safe to re-run — only missing outputs are regenerated.

---

## 4. Backend API

### Layer loading and caching

At startup, `api.py` syncs data from S3, then loads all normalized layers into a single in-memory dict `NORMALIZED_LAYERS: Dict[str, np.ndarray]`. All subsequent `/analyze` calls operate on these arrays. This means:

- S3 I/O happens once at startup (or on `/admin/reload`), not per request
- `/analyze` response time is dominated by the weighted overlay computation (~milliseconds for typical county sizes) plus the PNG render (~1–2 seconds)
- Memory usage scales with raster size × number of layers; typical Kenya county at 0.005° resolution is ~300×400 pixels per layer, well within acceptable bounds

### Analysis endpoint (`POST /analyze`)

1. Validate weights sum to 1.0 (normalize if within 1% tolerance)
2. Compute weighted sum: `suitability = Σ (layer_array × weight)`
3. Reproject and apply constraint mask (bilinear reproject to match layer CRS/transform)
4. Clip to [0, 100]
5. Compute statistics on valid pixels (> 0)
6. Save GeoTIFF to `api_results/`
7. Call `render_all()` to generate suitability map PNG, criteria grid PNG, classification chart, weight chart
8. Save metadata JSON
9. Upload GeoTIFF result to S3 (best-effort, non-blocking)
10. Return `SuitabilityResponse`

### Map image endpoint (`GET /map-image/{analysis_id}`)

Reads the saved GeoTIFF, applies a four-class colormap, and returns a transparent RGBA PNG. The color boundaries are hard-coded to match the dashboard legend (30/50/70 thresholds). Leaflet's `ImageOverlay` uses the `raster_bounds` from the analysis response to position the image correctly.

### S3 sync

`sync_county_from_s3()` uses a boto3 paginator to list objects under each S3 prefix, compares S3 `LastModified` timestamps against local file mtimes, and downloads only files that are missing or outdated. The sync map covers four prefixes:

```
<county>/normalized/    → normalized_dir
<county>/boundary/      → boundary parent dir
<county>/constraints/   → shared_dir (protected areas)
<county>/preprocessed/  → constraint_mask parent dir
```

If `AWS_S3_BUCKET` is not set, the sync is skipped and local files are used — enabling fully local development without any AWS configuration.

---

## 5. Frontend

### State management

All application state lives in `App.js`. Child components are stateless and communicate only through props and callbacks. This keeps the component tree simple and avoids prop drilling for the report state, which needs to be shared between the right panel (`ReportPanel`) and the map area (PDF overlay) and footer controls.

Key state variables:

| State | Type | Purpose |
|---|---|---|
| `weights` | `{criterion: float}` | Current slider values, auto-normalized on change |
| `analysisResult` | `object \| null` | Last `/analyze` response; drives map overlay and statistics |
| `pdfBlobUrl` | `string \| null` | Object URL for the generated PDF blob |
| `reportOverlay` | `bool` | Whether the full-screen PDF viewer is visible |
| `reportDepth` | `'summary' \| 'full'` | Selected report depth |

### Weight normalization

When a user moves a slider, the remaining weight is redistributed proportionally among all other criteria:

```javascript
const remaining = 1.0 - newValue;
others.forEach(k => { newWeights[k] = (weights[k] / otherSum) * remaining; });
```

This keeps weights summing to 1.0 at all times without requiring manual adjustment of other sliders. The "Run Analysis" button is disabled if the total deviates by more than 1%.

### Map rendering

The Leaflet map uses `ImageOverlay` to render the suitability PNG positioned by `raster_bounds` (as `[[south, west], [north, east]]`). The `FitToBoundary` component uses `useMap()` to auto-zoom to the county boundary GeoJSON after load. The boundary itself is rendered as a dashed `GeoJSON` layer with no fill.

A cache-busting `?t=Date.now()` query parameter is appended to the image URL on each new analysis, preventing the browser from serving a cached PNG for a new analysis ID.

### PDF viewer

When a report is ready, the PDF blob URL is displayed in a full-screen overlay `<iframe>` over the map. This avoids opening a new browser tab (which may be blocked) while keeping the report easily dismissible. The overlay closes on Escape key or by clicking the backdrop.

---

## 6. Report Generation

### Asset pipeline

Before the PDF is assembled, `render_all()` in `map_renderer.py` produces four Matplotlib PNGs:

| Asset | Description |
|---|---|
| `suitability_map` | Main 4-class map with county boundary, north arrow, scale bar, and legend positioned outside the axes |
| `criteria_grid` | 2×N grid of individual normalized criterion layers, each with its own colormap and a mini colorbar |
| `classification_chart` | Horizontal bar chart of suitability class percentages |
| `weight_chart` | Horizontal bar chart of criterion weight distribution |

All assets are saved to `api_results/` alongside the GeoTIFF and referenced by filename in the metadata JSON. The report builder loads them from disk; if any are missing (e.g. older analyses before the renderer was added), it handles the absence gracefully and omits the image rather than failing.

### PDF assembly (`report_writer.py`)

ReportLab Platypus is used for layout. The document is assembled as a list of flowables (paragraphs, tables, images, spacers, page breaks) and passed to `SimpleDocTemplate.build()`. Key structural elements:

- **Cover block**: a green `Table` cell with the county name and crop overlaid — ReportLab's trick for a colored background behind text
- **Score cards**: a 2-row outer table (value row + label row) with green background, avoiding nested-table rendering issues
- **Classification table**: uses custom `HorizontalBar` flowables (a `Flowable` subclass that draws directly on the canvas) for the progress bars
- **Section headers**: green `Table` cells with white bold text, matching the dashboard color scheme

Page headers and footers are rendered via `onFirstPage` / `onLaterPages` callbacks on `SimpleDocTemplate`.

### LLM narrative

`generate_narrative()` builds a structured prompt from the analysis statistics and routes it to the configured LLM provider. The prompt requests exactly three paragraphs (overall assessment, spatial interpretation, recommendations) with specific instructions to avoid markdown, bullet points, or headers. If every provider fails, `_narrative_fallback()` produces a deterministic template using the same data — ensuring the PDF always builds successfully.

The provider routing logic auto-detects available providers by checking which API keys are set, falling back through groq → gemini → anthropic → ollama. The `LLM_PROVIDER` env var forces a specific provider.

---

## 7. Configuration System

All county-specific parameters are encoded in `config/<county>.json`. `config.py` loads the active county's JSON and attaches a `_paths` dict of fully resolved `pathlib.Path` objects, which every module uses to locate files. No module contains hardcoded paths or county names.

### Path resolution

`CONFIG_DIR` is resolved relative to `config.py`'s own location (`__file__`), not relative to the working directory. This ensures the API finds its config files whether run locally from the project root or deployed to Render where the repo is checked out at `/opt/render/project/src`.

`BASE_DIR` (where raster data lives) is controlled by the `SUITABILITY_DATA_DIR` environment variable, defaulting to `~/suitability-engine`. On Render, this is set to `/tmp/suitability-engine` since Render's filesystem is ephemeral and data is re-synced from S3 on each startup.

### Environment variable precedence

| Variable | Purpose | Fallback |
|---|---|---|
| `ACTIVE_COUNTY` | Active county name | `config/active_county.txt` |
| `SUITABILITY_DATA_DIR` | Runtime data directory | `~/suitability-engine` |
| `AWS_S3_BUCKET` | S3 bucket for raster sync | Skip S3 sync, use local |
| `LLM_PROVIDER` | LLM provider for narrative | Auto-detect from available keys |

---

## 8. Storage & Deployment

### Local development

All data lives under `~/suitability-engine/data/` (or `SUITABILITY_DATA_DIR`). The `data/` directory is gitignored. Pipeline scripts write outputs to subdirectories of the county directory. The API reads from `normalized/` and `preprocessed/` and writes to `api_results/`.

### Cloud deployment (Render + S3)

```
GitHub push → Render build (pip install) → Render deploy
                                               ↓ startup
                                          S3 sync (boto3)
                                               ↓
                                          Load layers into memory
                                               ↓
                                          Serve traffic
```

Render's filesystem is ephemeral — it resets on each deploy or dyno restart. The S3 sync on startup re-downloads all county data to `/tmp/suitability-engine`. Analysis results are written locally and also uploaded back to `<county>/results/` in S3 for persistence.

The `/admin/reload` endpoint allows re-syncing from S3 and reloading layers without a full restart, which is useful after uploading updated rasters.

### IAM permissions required

```json
{
  "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
  "Resource": ["arn:aws:s3:::suitability-engine", "arn:aws:s3:::suitability-engine/*"]
}
```

---

## 9. Key Design Decisions

**Config-driven county onboarding.** Every county-specific parameter (thresholds, weights, filenames, map center) lives in a single JSON file. Adding a new county requires no code changes. This was prioritized over a database-backed configuration because the number of counties is small, configs change infrequently, and JSON files are easy to version control and diff.

**In-memory layer cache.** Normalized layers are loaded into NumPy arrays at API startup. This makes `/analyze` sub-second but means S3 sync must complete before the API is healthy. The `/health` endpoint reports `layers_loaded` count so load balancers and monitoring can detect a failed sync.

**Stateless analyses.** Each call to `/analyze` is independent. Results are identified by a timestamp-based `analysis_id` and stored as files. This avoids a database while still allowing retrieval of previous results.

**LLM-with-fallback pattern.** The report narrative is the only component with an external dependency (LLM API). By implementing a deterministic fallback that uses the same analysis data, the report subsystem is fully self-contained. A network failure or missing API key degrades quality gracefully rather than causing an error.

**Fuzzy normalization over hard thresholds.** Using fuzzy membership functions rather than binary suitability masks produces a continuous score that reflects partial suitability near threshold boundaries. This is more agronomically realistic and produces smoother maps that are easier to communicate to non-technical stakeholders.

**Protected area constraint masking.** Protected areas are treated as hard exclusions (score forced to 0) rather than as a weighted penalty. This reflects the regulatory reality that farming in national parks or game reserves is not permitted regardless of biophysical suitability.

---

## 10. Known Limitations & Future Work

### Current limitations

- **Single crop per county config.** The config schema supports one crop per county. Comparing two crops over the same county requires maintaining two separate configs and running two API instances or adding a `crop` query parameter to all endpoints.
- **Static weights per session.** The API processes each `/analyze` request independently with the weights provided. There is no server-side weight optimization or parameter search.
- **Temporal averaging.** All raster inputs are long-term averages. Seasonal variability in rainfall or temperature is not captured.
- **No user authentication.** The API has no auth layer. `/admin/reload` is unprotected. For production use, this endpoint should be protected.
- **Cold start latency.** On Render's free/starter tier, the dyno spins down after inactivity. S3 sync on first request after spin-up adds 10–30 seconds of latency.

### Planned improvements

- Multi-crop comparison dashboard view
- Seasonal analysis using multiple temporal raster inputs
- RAG integration: connect a vector store of agronomic literature to ground the LLM narrative in peer-reviewed sources
- Sub-county administrative unit reporting
- QGIS plugin for offline use without the API
- Automated data refresh pipeline (CHIRPS and WorldClim publish updates regularly)
- API authentication and per-user analysis history