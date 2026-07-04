# System Design — Crop Suitability Engine

*Version 3.0 · Last updated April 2026*

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
- **Counties** (47 Kenyan counties) and **crops** (cotton, maize, coffee, and more) are onboarded entirely through JSON config files — no code changes. County geography and crop agronomy are split into separate config files so any crop can be analysed over any county.
- **Data** is fetched **on demand** from open sources (Microsoft Planetary Computer, NASA POWER, OpenStreetMap) the first time a county is analysed, then cached. Prepared layers are mirrored to Cloudflare R2 object storage so subsequent cold starts load in seconds instead of re-fetching.
- **Layers** are loaded into memory per county; once loaded, **analyses** are sub-second (weighted overlay on in-memory arrays).
- **Reports** are generated on demand and include an LLM-written narrative section, grounded in a retrieval-augmented (RAG) store of agronomic documents.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Client Browser                         │
│                                                               │
│ ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌─────────────┐ │
│ │County/Crop │ │WeightCtrls │ │  MapView  │ │Statistics + │ │
│ │ Selectors  │ │ (sliders)  │ │ (Leaflet) │ │ ReportPanel │ │
│ └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └──────┬──────┘ │
│       └──────────────┴──────────────┴──────────────┘        │
│                         App.js                               │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP (Axios / fetch)
┌──────────────────────────▼───────────────────────────────────┐
│                      FastAPI Backend                          │
│                                                               │
│  /ping /health /status/{county}   (readiness / progress)     │
│  /counties /crops /county /criteria /boundary-geojson        │
│  /analyze  →  weighted overlay  →  save GeoTIFF              │
│              →  render_all()    →  save PNGs                  │
│  /map-image →  render RGBA PNG from GeoTIFF                   │
│  /report    →  build_report()  →  RAG + LLM narrative → PDF  │
│  /admin/load-county → fetch + pipeline + load (background)    │
│  /admin/reload      → re-sync + reload layers                │
│                                                               │
│  Per-county in-memory cache: { county: {layer: np.ndarray} } │
└───────┬───────────────────┬──────────────────────┬──────────┘
        │ startup / cold     │ on-demand fetch      │ narrative
┌───────▼─────────┐ ┌────────▼──────────────┐ ┌─────▼──────────┐
│ Cloudflare R2   │ │  Open data sources    │ │ LLM Provider   │
│ (S3-compatible) │ │  · Planetary Computer │ │ Groq / Gemini  │
│ kenya/<county>/ │ │  · NASA POWER         │ │ / Anthropic /  │
│   normalized/   │ │  · OpenStreetMap      │ │ Ollama         │
│   boundaries/   │ └───────────────────────┘ │  + RAG store   │
│   preprocessed/ │                            │ (data/rag_docs)│
└─────────────────┘                            └────────────────┘
```

**Two data paths.** On a warm/cold start where a county has already been prepared, the API pulls its analysis-ready layers from **Cloudflare R2** in seconds. The first time a county is ever requested, it is fetched from the **open data sources**, run through the preprocessing pipeline, and then uploaded to R2 so every future start is fast.

### Component responsibilities

| Component | Responsibility |
|---|---|
| `config.py` | Single source of truth for all paths and settings. Merges county geography + crop agronomy configs. All other modules import from here. |
| `pc_fetcher.py` | On-demand fetch of raw raster inputs from Planetary Computer + NASA POWER, and county boundary from OpenStreetMap. Caches to disk; skips if already cached. |
| Pipeline scripts | Data preparation: reproject → align → normalize → clip. Idempotent — skip completed steps. |
| `upload_to_r2.py` | Mirrors prepared layers (normalized, boundary, constraint mask) to Cloudflare R2 so future cold starts are fast. |
| `api.py` | Serves county/crop metadata, orchestrates per-county fetch+pipeline+load in the background, runs weighted overlays, renders images, coordinates report generation. |
| `map_renderer.py` | Renders GeoTIFFs and statistics as publication-quality Matplotlib PNGs for reports. |
| `report_writer.py` | Assembles ReportLab PDF from rendered assets + LLM narrative. Builds a RAG store from `data/rag_docs/` and grounds the narrative in it. Falls back to a deterministic template if no LLM provider is available. |
| `App.js` | Orchestrates all frontend state (selected county, crop, weights, analysis result). Passes callbacks down to stateless child components. |
| `MapView.js` | Leaflet map with boundary GeoJSON overlay and suitability `ImageOverlay`. |

---

## 3. Data Pipeline

The pipeline transforms raw rasters from heterogeneous sources into a consistent, analysis-ready set of normalized layers. It runs once per county setup — after `pc_fetcher.py` has downloaded the raw inputs — and only needs to be re-run when source data or thresholds change.

### Stage 0 — Fetch (`pc_fetcher.py`)

**Input:** county bounding box + boundary (from config / OpenStreetMap)
**Output:** Raw GeoTIFFs in `data/counties/<county>/raw/`

Fetched on demand the first time a county is analysed: elevation and other layers from Microsoft Planetary Computer, climate normals from NASA POWER, and the county boundary from OpenStreetMap. Skipped entirely if the raw layers are already cached on disk.

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

### Startup and per-county layer loading

Startup returns immediately so Render's health check passes from the first second; all heavy work runs as background tasks. The sequence is:

1. **Phase 1 (synchronous, instant):** scan `data/` for counties whose normalized layers already exist on disk and load them into the cache. On a Render cold start (ephemeral `/tmp`) this finds nothing; locally it loads everything cached.
2. **Phase 2 (background):** for the active county, try an **R2 sync** first (fast); if R2 has no prepared data, fall back to an **on-demand fetch** from Planetary Computer / NASA POWER followed by the preprocessing pipeline. Progress is reported per county via `/status/{county}`.

Loaded layers live in a **per-county** in-memory cache, `COUNTY_CACHE: Dict[str, dict]`, where each entry holds `{ "layers": {name: np.ndarray}, "profile": ..., "bounds": ... }`. All subsequent `/analyze` calls for that county operate on these arrays. This means:

- Object-storage / network I/O happens once per county (at load or on `/admin/reload`), not per request
- `/analyze` response time is dominated by the weighted overlay computation (~milliseconds for typical county sizes) plus the PNG render (~1–2 seconds)
- Memory usage scales with raster size × number of layers × loaded counties; a typical Kenya county at 0.005° resolution is ~300×400 pixels per layer, well within acceptable bounds
- Additional counties can be loaded at runtime via `POST /admin/load-county?county=<name>` without a restart

### Analysis endpoint (`POST /analyze`)

1. Validate weights sum to 1.0 (normalize if within 1% tolerance)
2. Compute weighted sum: `suitability = Σ (layer_array × weight)`
3. Reproject and apply constraint mask (bilinear reproject to match layer CRS/transform)
4. Clip to [0, 100]
5. Compute statistics on valid pixels (> 0)
6. Save GeoTIFF to `api_results/` (keyed by county + crop + analysis id)
7. Call `render_all()` to generate suitability map PNG, criteria grid PNG, classification chart, weight chart
8. Save metadata JSON
9. Return `SuitabilityResponse`

Per-analysis results are written to the local (ephemeral) filesystem and served back by `analysis_id`; only the reusable prepared layers are mirrored to R2, not individual analysis outputs.

### Map image endpoint (`GET /map-image/{analysis_id}`)

Reads the saved GeoTIFF, applies a four-class colormap, and returns a transparent RGBA PNG. The color boundaries are hard-coded to match the dashboard legend (30/50/70 thresholds). Leaflet's `ImageOverlay` uses the `raster_bounds` from the analysis response to position the image correctly.

### R2 sync and on-demand fetch

Cloudflare R2 is an S3-compatible object store, so it is accessed with the same `boto3` client (pointed at the R2 endpoint). `sync_county_from_r2()` uses a paginator to list objects under each prefix, compares R2 `LastModified` timestamps against local file mtimes, and downloads only files that are missing or outdated. The sync map covers three prefixes:

```
kenya/<county>/normalized/     → normalized_dir     (required)
kenya/<county>/boundaries/     → boundary parent dir
kenya/<county>/preprocessed/   → constraint_mask parent dir
```

If R2 has no prepared data for the county (or R2 is not configured), the API falls back to `pc_fetcher.fetch_all_layers()`, which pulls raw rasters from Planetary Computer + NASA POWER and the boundary from OpenStreetMap, runs the four-stage pipeline, and then calls `upload_county_to_r2()` so the next start can take the fast path. If neither R2 nor the data sources are reachable, the county simply reports an `error` status via `/status/{county}` while other loaded counties keep serving.

---

## 5. Frontend

### State management

All application state lives in `App.js`. Child components are stateless and communicate only through props and callbacks. This keeps the component tree simple and avoids prop drilling for the report state, which needs to be shared between the right panel (`ReportPanel`) and the map area (PDF overlay) and footer controls.

Key state variables:

| State | Type | Purpose |
|---|---|---|
| `activeCounty` / `activeCrop` | `string \| null` | Currently selected county and crop; drive the `?county=`/`?crop=` params on API calls |
| `countyStatuses` | `{county: status}` | Per-county load progress polled from `/health` / `/status`, so the UI can show which counties are ready |
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

**RAG grounding.** At startup, `build_rag_store()` indexes any agronomic documents placed in `data/rag_docs/` (`.txt`, `.md`, and text-extractable `.pdf`). It prefers a ChromaDB vector store and falls back to a lightweight TF-IDF store if ChromaDB is unavailable, so the feature degrades gracefully rather than failing. When a report is generated, the most relevant passages are retrieved and injected into the narrative prompt, grounding the LLM's recommendations in source literature (e.g. FAO crop guides). If `data/rag_docs/` is empty, RAG is silently disabled and the narrative is produced from statistics alone.

---

## 7. Configuration System

Configuration is split into two independent dimensions:

- **`config/counties/<county>.json`** — geography only: boundary source, map centre/zoom, bounding box, resolution, and the raster layer filenames.
- **`config/crops/<crop>.json`** — agronomy only: the fuzzy `normalization` thresholds, criterion `weights`, and `criteria_info` descriptions.

`config.py` merges the active county and crop into a single config dict and attaches a `_paths` dict of fully resolved `pathlib.Path` objects, which every module uses to locate files. Because the two are orthogonal, **any crop can be analysed over any county** without duplicating configuration. Raster caches are keyed by county (shared across crops); analysis results are keyed by county + crop. No module contains hardcoded paths, county names, or crop names.

### Path resolution

`CONFIG_DIR` is resolved relative to `config.py`'s own location (`__file__`), not relative to the working directory. This ensures the API finds its config files whether run locally from the project root or deployed to Render where the repo is checked out at `/opt/render/project/src`.

`BASE_DIR` (where raster data lives) is controlled by the `SUITABILITY_DATA_DIR` environment variable, defaulting to `~/suitability-engine`. On Render, this is set to `/tmp/suitability-engine` since Render's filesystem is ephemeral and prepared layers are re-synced from R2 (or re-fetched from the open data sources) on each startup.

### Environment variable precedence

| Variable | Purpose | Fallback |
|---|---|---|
| `ACTIVE_COUNTY` | Default county name | `config/active_county.txt`, else first county alphabetically |
| `ACTIVE_CROP` | Default crop name | `config/active_crop.txt`, else `cotton` |
| `SUITABILITY_DATA_DIR` | Runtime data directory | `~/suitability-engine` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Cloudflare R2 credentials for the fast-start layer cache | If unset, skip R2 and fetch on demand from open sources |
| `LLM_PROVIDER` | LLM provider for narrative (`groq`/`gemini`/`anthropic`/`ollama`) | Auto-detect from available API keys |

Any endpoint that takes county-specific data also accepts `?county=` and `?crop=` query parameters, which override the defaults per request.

---

## 8. Storage & Deployment

### Local development

All data lives under `~/suitability-engine/data/` (or `SUITABILITY_DATA_DIR`). The `data/` directory is gitignored. Pipeline scripts write outputs to subdirectories of the county directory. The API reads from `normalized/` and `preprocessed/` and writes to `api_results/`.

### Cloud deployment (Render + R2 + open data sources)

```
GitHub push → Render build (pip install) → Render deploy
                                               ↓ startup (returns instantly)
                    ┌──────────────────────────┴───────────────────────┐
             R2 configured & county prepared            first ever request for county
                    ↓                                            ↓
             R2 sync (boto3, seconds)                 fetch from PC / NASA / OSM
                    ↓                                            ↓
                    └──────────────► pipeline (if fetched) ──► upload to R2
                                               ↓
                                     Load layers into memory
                                               ↓
                                          Serve traffic
```

Render's filesystem is ephemeral — it resets on each deploy or dyno restart. On startup the API re-hydrates prepared counties from R2 into `/tmp/suitability-engine` (fast path). A county never seen before is fetched from the open data sources, run through the pipeline, and then uploaded to R2 so the slow path only ever happens once per county.

The `/admin/reload` endpoint re-syncs and reloads layers without a full restart; `/admin/load-county?county=<name>` prepares an additional county on demand.

### Deployment options & cost

| Option | Where | Relative cost | Notes |
|---|---|---|---|
| **Render free/starter + R2** (current) | Cloud | ~$0 | Free web tier; R2 free tier is 10 GB storage + generous egress. Trade-off: dyno spins down when idle → cold-start latency. |
| Render paid instance + R2 | Cloud | Low (≈$7+/mo) | Removes spin-down; keeps counties warm for demos. |
| Self-host (VM/on-prem) + local disk | On-prem | Hardware + ops | No object-storage bill; data fetched once and kept on local disk. Suited to a county office with its own server. |

Because R2 is S3-compatible, the same code runs against AWS S3 or any S3-compatible store by changing only the endpoint and credentials — no vendor lock-in.

---

## 9. Key Design Decisions

**Config-driven county + crop onboarding.** County geography and crop agronomy live in separate JSON files under `config/counties/` and `config/crops/`. Adding a county or crop requires no code changes, and any crop can be run over any county. This was prioritized over a database-backed configuration because the number of configs is small, they change infrequently, and JSON files are easy to version control and diff.

**On-demand fetch with an R2 cache.** Rather than committing multi-MB rasters to the repo or requiring a manual data-prep step, layers are fetched from open sources the first time a county is needed and then mirrored to Cloudflare R2. This keeps the repository small, makes deployment reproducible from nothing, and still gives fast cold starts after the first fetch. R2 was chosen over AWS S3 for its free egress and zero-cost tier, while remaining S3-compatible.

**In-memory, per-county layer cache.** Normalized layers are loaded into NumPy arrays per county and cached in `COUNTY_CACHE`. This makes `/analyze` sub-second. Loading is asynchronous so the API is healthy immediately; `/health` and `/status/{county}` report per-county load progress so clients and monitoring can tell which counties are ready.

**Stateless analyses.** Each call to `/analyze` is independent. Results are identified by a timestamp-based `analysis_id` and stored as files. This avoids a database while still allowing retrieval of previous results.

**LLM-with-fallback pattern.** The report narrative is the only component with an external dependency (LLM API). By implementing a deterministic fallback that uses the same analysis data, the report subsystem is fully self-contained. A network failure or missing API key degrades quality gracefully rather than causing an error.

**Fuzzy normalization over hard thresholds.** Using fuzzy membership functions rather than binary suitability masks produces a continuous score that reflects partial suitability near threshold boundaries. This is more agronomically realistic and produces smoother maps that are easier to communicate to non-technical stakeholders.

**Protected area constraint masking.** Protected areas are treated as hard exclusions (score forced to 0) rather than as a weighted penalty. This reflects the regulatory reality that farming in national parks or game reserves is not permitted regardless of biophysical suitability.

---

## 10. Known Limitations & Future Work

### Current limitations

- **Single enabled crop.** Normalized layers are cached per county, not per county + crop, and the pipeline normalizes with the default crop's thresholds. Only **cotton** is calibrated and enabled (`GET /crops` is filtered by the `ENABLED_CROPS` env var, default `cotton`); the other crop configs are hidden until normalization is keyed by county + crop. See README → *Multi-crop: future work*.
- **Static weights per session.** The API processes each `/analyze` request independently with the weights provided. There is no server-side weight optimization or parameter search.
- **Temporal averaging.** All raster inputs are long-term averages/climatologies. Seasonal variability in rainfall or temperature is not captured.
- **No user authentication.** The API has no auth layer. `/admin/reload` and `/admin/load-county` are unprotected. For production use, these endpoints should be protected.
- **Cold start latency.** On Render's free/starter tier, the dyno spins down after inactivity. The first request after spin-up pays the R2 sync (seconds) or, for a never-prepared county, a full open-source fetch + pipeline (tens of seconds to a few minutes). Keeping a paid instance warm removes this for demos.

### Planned improvements

- Multi-crop comparison dashboard view (compare two crops over one county side by side)
- Seasonal analysis using multiple temporal raster inputs
- Sub-county administrative unit reporting
- QGIS plugin for offline use without the API
- Automated data refresh pipeline (CHIRPS and WorldClim publish updates regularly)
- API authentication and per-user analysis history