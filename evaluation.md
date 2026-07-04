# Evaluation — Crop Suitability Engine

*Version 3.0 · Last updated April 2026*

---

## Table of Contents

- [1. Evaluation Framework](#1-evaluation-framework)
- [2. Analytical Validity](#2-analytical-validity)
- [3. Pipeline Correctness](#3-pipeline-correctness)
- [4. API Behavior](#4-api-behavior)
- [5. Frontend Usability](#5-frontend-usability)
- [6. Report Generation](#6-report-generation)
- [7. Sensitivity Analysis Results](#7-sensitivity-analysis-results)
- [8. Deployment Readiness](#8-deployment-readiness)
- [9. Accuracy Assessment Methodology](#9-accuracy-assessment-methodology)
- [10. Automated Test Suite](#10-automated-test-suite)
- [11. Known Issues & Mitigations](#11-known-issues--mitigations)

---

## 1. Evaluation Framework

This document evaluates the Crop Suitability Engine across five dimensions:

| Dimension | What we're checking |
|---|---|
| **Analytical validity** | Do the suitability scores reflect agronomically defensible thresholds? |
| **Pipeline correctness** | Do rasters align, normalize, and combine correctly? |
| **API behavior** | Do endpoints return correct data under normal and edge-case inputs? |
| **Frontend usability** | Does the UI correctly reflect analysis state and handle errors? |
| **Report quality** | Are generated reports accurate, complete, and legible? |

Where quantitative metrics are available they are reported. Where the evaluation is qualitative, the reasoning and evidence are stated explicitly.

Testing operates at two levels. An **automated pytest suite** ([§10](#10-automated-test-suite)) locks down the deterministic core — the fuzzy functions, weighted overlay, classification, statistics, config invariants, and API request handling — and runs on every push via GitHub Actions. The manual and analytical checks documented in the sections below (spatial alignment, report layout, LLM narrative quality, usability) complement the automated suite where behaviour is visual, agronomic, or otherwise not amenable to a simple assertion.

> **Configuration note:** as of v3.0, normalization thresholds and criterion weights are defined **per crop** in `config/crops/<crop>.json` and applied uniformly across all 47 counties; county configs (`config/counties/<county>.json`) carry geography only. Earlier per-county agronomy has been consolidated into per-crop agronomy — see [§2.2](#22-weight-defaults).

---

## 2. Analytical Validity

### 2.1 Normalization thresholds

Thresholds for each fuzzy function were derived from peer-reviewed agronomic literature and cross-checked against Kenya's National Cotton Development Authority (NCDA) guidelines. They live in `config/crops/cotton.json` and apply to cotton across every county. The table below summarizes the cotton config and its justification.

| Criterion | Function | Parameters | Agronomic basis |
|---|---|---|---|
| Elevation | Trapezoidal | a=200, b=500, c=1200, d=1800 | Cotton performs well at ~500–1200 m; the wide plateau accommodates both ASAL lowlands and mid-altitude zones across counties |
| Rainfall | Trapezoidal | a=400, b=600, c=1000, d=1600 | 600–1000 mm brackets the accepted optimal range for rainfed cotton in Kenya |
| Temperature | Gaussian | optimal=27, spread=5 | Mean annual temperature of ~25–30°C suits cotton; Gaussian reflects symmetric sensitivity around the optimum |
| Soil clay | Trapezoidal | a=100, b=200, c=400, d=550 | Moderate clay (200–400 g/kg) retains moisture without waterlogging; SoilGrids g/kg units confirmed |
| Slope | Linear descending | min=0, max=15 | FAO slope classes: <2° excellent, 2–8° good, >15° mechanization impossible and erosion risk unacceptable |

Because the thresholds are defined per crop rather than per county, a single crop config is validated once and reused everywhere. Other crops (`maize.json`, `coffee.json`, etc.) carry their own thresholds — e.g. coffee's higher-altitude, higher-rainfall optima. Every crop config's normalization types are checked automatically ([§10](#10-automated-test-suite)).

### 2.2 Weight defaults

Default weights reflect the relative importance of each criterion for the crop, informed by expert consultation. They are intentionally set as defaults, not fixed values — the entire purpose of the interactive dashboard is weight exploration by the analyst.

| Crop | Rainfall | Elevation | Temperature | Soil | Slope |
|---|---|---|---|---|---|
| Cotton | 0.30 | 0.15 | 0.20 | 0.20 | 0.15 |

Cotton's rainfall weight (0.30, the highest) reflects that water availability is the primary limiting factor for rainfed cotton in Kenya's drier zones. An automated test asserts that every crop's weights sum to 1.0 ([§10](#10-automated-test-suite)).

**Modeling simplification (and future work).** Weights are now defined per crop and applied uniformly across counties. This trades the earlier per-county tuning (e.g. a higher rainfall weight for semi-arid counties) for a much simpler, crop-centric config that scales to all 47 counties. Because the dashboard lets the analyst adjust weights per session, county-specific emphasis can still be applied interactively. A per-county weight/threshold override layer is noted as future work.

### 2.3 Classification thresholds

The four-class scheme (0–30, 30–50, 50–70, 70–100) is standard in FAO-style land suitability assessment and corresponds broadly to Not Suitable (N), Marginally Suitable (S3), Moderately Suitable (S2), and Highly Suitable (S1). These classes are fixed — they are not configurable per county — because the underlying score range (0–100) is consistent across all analyses by design.

### 2.4 Constraint masking

Protected areas are applied as hard exclusions. Pixels inside national parks, game reserves, or forest reserves receive a score of 0 regardless of biophysical suitability. This is correct behavior — these lands are legally unavailable for agricultural conversion. The constraint mask is built using `rasterize()` on the protected area polygons and combined with the county boundary mask using a logical AND:

```python
mask = (inside_boundary == 1) & (protected == 0)
```

### 2.5 Limitation: independence assumption

Weighted overlay assumes criterion independence. In practice, elevation and temperature are correlated (temperature decreases with elevation at ~6.5°C/km). For the spatial scales involved (county level), this correlation is modest, but analysts should be aware that the combined elevation + temperature weight (0.35 for Kitui) may slightly double-count the same underlying environmental gradient.

---

## 3. Pipeline Correctness

### 3.1 Spatial alignment verification

After `realign_to_boundary.py`, all aligned layers must share identical CRS, transform, width, and height. The `align_rasters.py` module includes a `verify_alignment()` method that checks this explicitly. A correct run produces output like:

```
── Verifying alignment ──────────────────────────────────
  Reference: elevation
  ✅ rainfall: aligned
  ✅ temperature: aligned
  ✅ soil: aligned
  ✅ slope: aligned

✅ All rasters aligned successfully.
```

Any mismatch at this stage will cause the weighted overlay to silently produce incorrect results (NumPy will broadcast mismatched arrays). The verification step should be run and pass before proceeding to normalization.

### 3.2 Normalization range check

After `normalize.py`, the sanity check reports the valid pixel range and mean for each normalized layer:

```
── Output sanity check ──────────────────────────────────
  elevation:    118432/138240 px | range 0.0-100.0 | mean 62.3
  rainfall:     118432/138240 px | range 0.0-100.0 | mean 71.4
  temperature:  118432/138240 px | range 0.0-100.0 | mean 88.1
  soil:         118432/138240 px | range 0.0-100.0 | mean 43.7
  slope:        118432/138240 px | range 0.0-100.0 | mean 79.2
```

Expected behavior:
- All layers should have the same valid pixel count (consistent with the county boundary extent)
- Range should be 0–100 (or 0–some_value if no pixels reach the plateau of a trapezoidal function)
- A layer reporting `NO VALID PIXELS` indicates a threshold mismatch — the normalization parameters don't overlap with the actual data range in the raster

### 3.3 Zero pixel count consistency

After `clip_to_boundary.py`, the verification step checks that zero pixel counts are consistent across all normalized layers:

```
── Verification ─────────────────────────────────────────
  elevation:    19808/138240 zero px (14.3%)
  rainfall:     19808/138240 zero px (14.3%)
  temperature:  19808/138240 zero px (14.3%)
  soil:         19808/138240 zero px (14.3%)
  slope:        19808/138240 zero px (14.3%)
```

All layers should have exactly the same zero pixel count after clipping. Inconsistency here indicates one or more layers were not correctly aligned before normalization and clipping.

### 3.4 Weighted overlay arithmetic

The weighted overlay is a straightforward linear combination. For a two-layer case with equal weights this can be verified by hand:

```python
# Given: layer_a all 80.0, layer_b all 60.0, weights 0.5/0.5
result = 80.0 * 0.5 + 60.0 * 0.5  # = 70.0 expected
```

The API's `/analyze` endpoint returns the mean score in the response, which can be compared against manual calculation for known test inputs.

### 3.5 Edge case: weights not summing to 1.0

The API normalizes weights that are within 1% of summing to 1.0:

```python
if not np.isclose(total, 1.0, atol=0.001):
    weights_dict = {k: v / total for k, v in weights_dict.items()}
```

Weights more than 1% off return HTTP 400 with a descriptive error. This prevents analyses running with unnormalized weights while allowing for minor floating-point drift from the frontend slider arithmetic.

---

## 4. API Behavior

### 4.1 Endpoint response correctness

| Endpoint | Test | Expected | Verified |
|---|---|---|---|
| `GET /health` | Cold start, no county loaded | `status: degraded`, active county `layers_loaded: 0` | ✅ |
| `GET /health` | Active county loaded | `status: healthy`, that county's `layers_loaded: 5` | ✅ |
| `GET /county` | county=kitui | Returns kitui metadata with correct map_center | ✅ |
| `POST /analyze` | Weights sum to 1.0 | Returns SuitabilityResponse with analysis_id | ✅ |
| `POST /analyze` | Weights sum to 1.05 | HTTP 400 weight validation error | ✅ |
| `POST /analyze` | Wrong criterion keys | HTTP 400 with expected/received diff | ✅ |
| `POST /analyze` | No layers loaded | HTTP 503 with descriptive message | ✅ |
| `GET /map-image/{id}` | Valid analysis_id | RGBA PNG, Content-Type: image/png | ✅ |
| `GET /map-image/{id}` | Invalid analysis_id | HTTP 404 | ✅ |
| `GET /download/{id}` | Valid analysis_id | GeoTIFF, Content-Type: image/tiff | ✅ |
| `POST /report/{id}` | depth=summary | PDF, 2 pages | ✅ |
| `POST /report/{id}` | depth=full | PDF, 4 pages | ✅ |
| `POST /report/{id}` | depth=invalid | HTTP 400 | ✅ |
| `POST /admin/reload` | No R2 configured | Re-fetches / reloads from local files, returns counts | ✅ |

Rows involving metadata endpoints and `/analyze` request validation (weight sum, criterion keys, not-loaded county, malformed body) are now also covered by the automated suite in `tests/test_api.py` ([§10](#10-automated-test-suite)); rows requiring rendered assets or a fully loaded county (map image, GeoTIFF download, PDF depth) remain manual/integration checks.

### 4.2 CORS

`allow_origins=["*"]` is set for development. For a production deployment serving a known frontend domain, this should be restricted to that domain to prevent cross-origin API abuse.

### 4.3 Constraint mask reprojection

The constraint mask may have a slightly different transform than the normalized layers (it is built from the preprocessed reference layer, while normalized layers are aligned to the boundary grid). The API reprojects the mask to match the layer profile before applying it:

```python
reproject(
    source=rasterio.band(src, 1),
    destination=mask_aligned,
    src_transform=src.transform, src_crs=src.crs,
    dst_transform=LAYERS_PROFILE["transform"], dst_crs=LAYERS_PROFILE["crs"],
    resampling=Resampling.nearest,
)
```

Using `nearest` resampling for the binary mask is correct — bilinear interpolation on a 0/1 mask would produce fractional values and incorrect exclusions.

---

## 5. Frontend Usability

### 5.1 Weight normalization behavior

When any slider is moved, all other sliders are proportionally rescaled. This is the correct behavior for a constrained sum — it prevents the total from drifting and eliminates the need for a manual "normalize" button. The implementation correctly handles the edge case where all other weights are 0 (no redistribution performed).

One usability concern: rapid slider movement can cause brief intervals where the total is not exactly 1.0, briefly disabling the Run Analysis button. In practice the total recovers within a render cycle, but a debounced weight display could improve perceived responsiveness.

### 5.2 API error states

Two error states are handled:

- **API unreachable** (startup): The app renders a full-screen error with the API URL and the command to start the server. Avoids a blank or broken UI.
- **Analysis failure**: `alert()` with the API error detail. This is functional but visually disruptive — inline error display in the left panel would be preferable.

### 5.3 Map overlay caching

The `?t=Date.now()` cache-busting parameter correctly forces browser re-fetch on each new analysis. Without this, the browser may serve the cached PNG from a previous analysis for the same `analysis_id` URL pattern, though in practice analysis IDs are timestamp-based and unique.

### 5.4 PDF overlay

The PDF iframe overlay uses a blob URL created from a `fetch` response. This avoids popup-blocker issues and keeps the report in context. The overlay closes on Escape or backdrop click. One gap: there is no loading indicator while the iframe renders the PDF content — the overlay appears immediately but the PDF may take 1–2 seconds to render inside the iframe, which can appear as a blank white box.

---

## 6. Report Generation

### 6.1 PDF layout correctness

The `report_writer.py` standalone test (`python src/report_writer.py`) generates a test PDF using dummy data with no rendered image assets. This verifies the ReportLab layout code independently of the analysis pipeline. The resulting PDF should be inspected manually to confirm:

- Cover block renders with correct county name and crop
- Score cards display four metrics in a 2×2 grid
- Classification table renders colored bars and correct percentages
- All text is legible at normal zoom
- Methodology section (full depth only) correctly lists normalization functions with parameters

Known layout behavior to verify:
- Legend is positioned outside the map axes in the right margin (not overlapping the map)
- Section header bars render green with white text (not black background)
- Page numbers appear in the footer of pages 2+

### 6.2 LLM narrative quality

The LLM narrative is evaluated qualitatively. A well-formed response should:

- Contain exactly three paragraphs
- Reference the specific county and crop by name
- Cite the mean suitability score and highly/moderately suitable percentages
- Name the most influential criterion (highest weight)
- Include a recommendation paragraph with at least one specific actionable item
- Contain no markdown formatting, headers, or bullet points

The prompt explicitly instructs the model on all of these requirements. The `_narrative_fallback()` template satisfies all structural requirements but produces less specific text.

**LLM failure modes observed:**
- Groq: occasionally returns fewer than 3 paragraphs on short max_token budgets → mitigated by `max_tokens=600`
- Gemini: occasionally includes light markdown formatting → the paragraph splitter (`split('\n\n')`) handles this gracefully
- Anthropic: most reliable output quality but no free tier

### 6.3 Missing asset handling

If a rendered PNG asset is missing (e.g. criteria_grid for an analysis run before the renderer was added), `_img()` returns `None` and the report builder substitutes a plain-text "image not available" message. The PDF still builds successfully. This was explicitly tested by passing an empty `rendered={}` dict to `build_report()` in the standalone test.

---

## 7. Sensitivity Analysis Results

`sensitivity_analysis.py` runs a one-at-a-time (OAT) analysis varying each criterion weight from 0 to 1 in 7 steps while redistributing remaining weight proportionally. The elasticity metric quantifies influence:

```
elasticity = (suitability_range / mid_suitability) / weight_range
```

### Kitui Cotton — representative sensitivity results

| Criterion | Suitability range | Elasticity | Influence |
|---|---|---|---|
| Rainfall | ~18.2 | 0.68 | Medium |
| Temperature | ~14.1 | 0.52 | Medium |
| Soil | ~11.3 | 0.42 | Low–Medium |
| Elevation | ~8.7 | 0.32 | Low |
| Slope | ~6.2 | 0.23 | Low |

*Note: these are illustrative values based on the Kitui agroecological zone characteristics. Actual values depend on the specific raster data used.*

**Interpretation:** Rainfall and temperature are the most influential criteria in Kitui — changes to their weights produce the largest shifts in mean suitability. This is consistent with the semi-arid ASAL context where water availability and heat stress are the primary limiting factors. Slope has the lowest influence, reflecting that most of Kitui's land area falls within the acceptable slope range for cotton.

**Implication for data quality:** High-influence criteria require higher-quality source data. Rainfall and temperature are fetched from NASA POWER (~0.5°/0.1° climatology grid) and elevation from Copernicus DEM GLO-30 via Planetary Computer; the coarser climate grids in particular should be validated against local station data before making resource-allocation decisions based on these results.

---

## 8. Deployment Readiness

The `deploy_check.py` script runs automated checks across eight categories. All checks should pass before deploying (verified passing on the current tree).

### Check categories and common failures

**Repo structure** — verifies that `src/api.py`, `src/config.py`, `src/pc_fetcher.py`, `config/counties/`, `config/crops/`, `render.yaml`, and `requirements.txt` all exist.

**render.yaml** — verifies:
- Start command uses `src.api:app` (not `api:app`, which fails on Render)
- Health check path is `/ping` (returns instantly so startup passes immediately)
- `SUITABILITY_DATA_DIR`, `ACTIVE_COUNTY`, `ACTIVE_CROP`, and `R2_BUCKET` are set
- Secret credentials (R2 keys, `GROQ_API_KEY`) are marked `sync: false` (never stored in git)

**requirements.txt** — verifies all required packages are listed: `fastapi`, `uvicorn`, `rasterio`, `numpy`, `boto3`, `geopandas`, `pillow`, `pydantic`, `planetary-computer`, `pystac-client`.

**County configs** — parses each `config/counties/*.json` and verifies the geography keys (`county`, `display_name`, `layers`, `map_center`, `map_zoom`).

**Crop configs** — parses each `config/crops/*.json` and verifies the agronomy keys, that weights sum to 1.0, and that every normalization type is valid.

**config.py path logic** — verifies `CONFIG_DIR` is resolved from `__file__` (not the working directory) and that `ACTIVE_COUNTY`, `ACTIVE_CROP`, and `SUITABILITY_DATA_DIR` env vars are supported.

**.gitignore** — warns (not errors) if `data/`, `venv/`, or `*.tif` are not ignored.

**R2 sync / fetch coverage** — verifies `sync_county_from_r2`, the on-demand `fetch_all_layers` fallback, the three R2 prefixes (`normalized/`, `boundaries/`, `preprocessed/`), and the `/admin/reload` + `/admin/load-county` endpoints are present in `api.py`.

### Post-deploy verification

After deploying to Render, confirm via:

```bash
curl https://suitability-engine.onrender.com/ping      # → {"status":"ok"} immediately
curl https://suitability-engine.onrender.com/health    # per-county load state
```

A cold start reports `status: degraded` until the active county reaches `loaded`; poll `GET /status/{county}` to watch the fetch/pipeline progress. If a county stays in `error`, check the Render logs for R2 credential errors or an unreachable data source.

---

## 9. Accuracy Assessment Methodology

### 9.1 Recommended validation approach

The analysis produces a modeled suitability surface. Ground-truth validation requires comparing modeled scores against observed outcomes. The recommended approach for future validation:

1. **Collect ground truth points**: GPS-located observations of actual cotton cultivation (presence/absence, or yield class if available) from agricultural extension records, satellite-derived cropland maps, or field surveys.

2. **Extract modeled scores**: Use the GeoTIFF download endpoint to obtain the raster, then sample it at ground-truth locations using `rasterio.sample()` or QGIS.

3. **Compute accuracy metrics**:
   - For presence/absence: ROC-AUC, True Skill Statistic (TSS), sensitivity/specificity at threshold
   - For yield class: Spearman rank correlation between modeled score and yield class
   - Confusion matrix for the four-class classification

4. **Threshold optimization**: If ground-truth data is available, the 30/50/70 classification thresholds can be optimized using the ROC curve to maximize TSS.

### 9.2 Baseline comparison

Without field data, the modeled output can be compared against:

- **Kenya Agricultural Research Institute (KARI) cotton suitability maps** — if available for the study counties
- **Previous MCDSS studies** for cotton in East Africa (e.g. Nampak et al. 2018 for Uganda; Mwangi et al. 2020 for Kenya's Eastern Province)
- **FAO AgroEcological Zones (AEZ) classifications** for cotton

### 9.3 Uncertainty quantification

The current model produces a point estimate with no uncertainty bounds. Future work should propagate uncertainty from two sources:

- **Threshold uncertainty**: use Monte Carlo sampling over plausible threshold ranges (e.g. ±10% on trapezoidal breakpoints) to produce a score distribution at each pixel
- **Weight uncertainty**: the sensitivity analysis already quantifies weight uncertainty; its results can be converted to a standard deviation surface

---

## 10. Automated Test Suite

The deterministic core of the engine is covered by an automated **pytest** suite in `tests/`, run locally with `pytest` and on every push / pull request by GitHub Actions (`.github/workflows/ci.yml`) against Python 3.11 and 3.12. The current suite is **68 tests, ~4 seconds**, all passing.

### Testing philosophy

Automated tests target the parts of the system with a single correct answer — arithmetic, thresholds, config invariants, and request validation. Three principles keep the suite reliable:

- **Offline and deterministic.** No test hits the network or triggers a data fetch. Raster operations write tiny in-memory GeoTIFFs to a temp directory instead of depending on downloaded county data.
- **Fast.** The whole suite runs in seconds, so it can gate every push without slowing development.
- **Assertion-based, not visual.** Anything requiring human judgement (map legibility, narrative quality, PDF layout) stays in the manual sections above; the automated suite asserts only on values.

### Coverage

| Test file | Target | What it verifies (and why) |
|---|---|---|
| `test_normalize.py` | `normalize.py` | The three fuzzy functions at their defining points — trapezoidal plateau/shoulders, Gaussian peak & symmetry, linear-descending endpoints — plus 0–100 clamping. These functions convert every raw value into a score, so an error here silently corrupts every map. |
| `test_suitability.py` | `suitability.py` | Weighted-overlay arithmetic (incl. the hand-checked `80·0.5 + 60·0.5 = 70` case from [§3.4](#34-weighted-overlay-arithmetic)), score clamping, four-class classification boundaries, statistics counts/percentages, and empty-raster safety. |
| `test_config.py` | `config.py` | County/crop discovery, the county × crop merge, and — parametrized across **all 10 crops** — that weights sum to 1.0, weight and normalization keys agree, and every normalization type is known. Guards the invariants the pipeline assumes. |
| `test_api.py` | `api.py` | Metadata endpoints (`/ping`, `/health`, `/counties`, `/crops`, `/county`, `/criteria`) and `/analyze` request validation (unloaded county → 404/503, malformed body → 422) via FastAPI `TestClient`. |

### Key techniques

- **Temp-raster fixtures.** `test_suitability.py` writes small labelled GeoTIFFs with `rasterio`, so classification and statistics are checked against hand-computed expected counts without any external data.
- **Startup-free API tests.** The API client is constructed as `TestClient(app)` **without** the context-manager form, so Starlette does not run the startup lifespan — no R2 sync or Planetary Computer fetch happens during the tests. This is what keeps `test_api.py` offline and fast.
- **Parametrization over configs.** Config invariants are parametrized across every crop file, so adding a new crop automatically inherits the weight-sum and normalization-type checks.

### Relationship to the manual checks

Several checks that were previously manual are now enforced automatically: the weighted-overlay arithmetic ([§3.4](#34-weighted-overlay-arithmetic)), the weight-sum validation ([§3.5](#35-edge-case-weights-not-summing-to-10)), and the metadata / `/analyze` validation rows in [§4.1](#41-endpoint-response-correctness). The manual checks that remain — spatial alignment ([§3.1](#31-spatial-alignment-verification)), report layout ([§6.1](#61-pdf-layout-correctness)), and LLM narrative quality ([§6.2](#62-llm-narrative-quality)) — are those requiring rendered output or human judgement, and are candidates for future integration tests.

### Running the suite

```bash
pip install -r requirements-dev.txt
pytest                    # 68 tests, ~4s
```

---

## 11. Known Issues & Mitigations

| Issue | Severity | Status | Mitigation |
|---|---|---|---|
| `align_rasters.py` uses `Dict` type hint without import | Low | Open | Add `from typing import Dict` to imports |
| `/admin/reload` endpoint has no authentication | Medium | Open | Add API key header check before production use |
| PDF iframe shows blank box for 1–2 seconds while rendering | Low | Open | Add a loading spinner inside the overlay |
| Sensitivity analysis imports `product` from `itertools` but doesn't use it | Low | Open | Remove unused import |
| `frontend/public/index.html` still references Bungoma in meta description | Low | Open | Update to generic description or dynamically set from API |
| R2 sync / data fetch failure is logged but does not prevent API startup | Medium | By design | Startup returns immediately; `/health` and `/status/{county}` report per-county `fetching`/`pipeline`/`error` state |
| LLM narrative may occasionally include markdown formatting | Low | Mitigated | Paragraph splitter handles `\n\n`; bold/italic markers pass through to PDF without ReportLab rendering them (plain text) |
| Weighted overlay does not account for criterion correlation | Informational | By design | Document in methodology; flag in reports for advanced users |
| No rate limiting on `/analyze` | Medium | Open | Add per-IP rate limiting for public deployments |