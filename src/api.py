"""
FastAPI Backend for Multi-Criteria Suitability Analysis
All county-specific config is read from the active county config file.
Switch counties by changing config/active_county.txt (or ACTIVE_COUNTY env var).

S3 bucket layout expected:
  suitability-engine/
    kitui/
      normalized/   normalized_elevation.tif  normalized_rainfall.tif ...
      boundary/     kitui_boundary.gpkg
      constraints/  kitui_constraints_mask.tif
      results/      (written back after each analysis)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Dict, List
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from pathlib import Path
from map_renderer import render_all
from report_writer import build_report
import json
from datetime import datetime
from PIL import Image
import io
import geopandas as gpd
import sys
import os
import logging

logger = logging.getLogger("suitability-api")
logging.basicConfig(level=logging.INFO)
logging.getLogger("botocore.credentials").setLevel(logging.WARNING)

sys.path.append(str(Path(__file__).parent))
from config import load_config

# ── Load county config ─────────────────────────────────────────────────────────
CONFIG = load_config()
PATHS  = CONFIG['_paths']

app = FastAPI(
    title=f"{CONFIG['crop']} Suitability Analysis API — {CONFIG['display_name']}",
    description=(
        f"Multi-criteria suitability analysis for {CONFIG['crop'].lower()} "
        f"farming in {CONFIG['display_name']}, {CONFIG['country']}"
    ),
    version="2.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory layer cache ──────────────────────────────────────────────────────
NORMALIZED_LAYERS: Dict[str, np.ndarray] = {}
LAYERS_PROFILE    = None
RASTER_BOUNDS     = None


# ══════════════════════════════════════════════════════════════════════════════
# S3 SYNC
# ══════════════════════════════════════════════════════════════════════════════

def _s3_client():
    """Return a boto3 S3 client, or None if boto3 / credentials are absent."""
    try:
        import boto3
        return boto3.client("s3")
    except Exception as e:
        logger.warning(f"boto3 unavailable or credentials missing: {e}")
        return None


def sync_county_from_s3() -> bool:
    """
    Download county data from S3 into the local filesystem before the API
    loads layers.

    S3 layout (actual):
        <bucket>/<county>/normalized/   normalized_<layer>.tif
        <bucket>/<county>/boundary/     <county>_boundary.gpkg
        <bucket>/<county>/constraints/  protected_areas_kenya.gpkg

    Local layout (mirrors config._paths):
        SUITABILITY_DATA_DIR/data/counties/<county>/normalized/
        SUITABILITY_DATA_DIR/data/counties/<county>/boundaries/
        SUITABILITY_DATA_DIR/data/shared/               ← protected areas go here
    """
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        logger.info("AWS_S3_BUCKET not set — skipping S3 sync, using local files.")
        return True

    s3 = _s3_client()
    if s3 is None:
        return False

    county = CONFIG["county"]

    # Map:  s3_prefix  →  local_directory
    sync_map = {
        f"{county}/normalized/":   PATHS["normalized_dir"],
        f"{county}/boundary/":     PATHS["boundary"].parent,
        # constraints/ holds protected_areas_kenya.gpkg → goes to shared/
        f"{county}/constraints/":  PATHS["shared_dir"],
        # preprocessed/ holds kitui_constraints_mask.tif
        f"{county}/preprocessed/": PATHS["constraint_mask"].parent,
    }

    total_downloaded = 0
    total_skipped    = 0

    for s3_prefix, local_dir in sync_map.items():
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)

        try:
            paginator = s3.get_paginator("list_objects_v2")
            pages     = paginator.paginate(Bucket=bucket, Prefix=s3_prefix)

            for page in pages:
                for obj in page.get("Contents", []):
                    key       = obj["Key"]
                    filename  = key.split("/")[-1]

                    # Skip S3 "folder" placeholders
                    if not filename:
                        continue

                    local_path = local_dir / filename

                    # Only download if missing or S3 version is newer
                    s3_mtime = obj["LastModified"].timestamp()
                    if local_path.exists():
                        local_mtime = local_path.stat().st_mtime
                        if local_mtime >= s3_mtime:
                            logger.info(f"  ✓ Skip (up to date): {filename}")
                            total_skipped += 1
                            continue

                    logger.info(f"  ↓ Downloading: s3://{bucket}/{key}  →  {local_path}")
                    s3.download_file(bucket, key, str(local_path))
                    total_downloaded += 1

        except Exception as e:
            logger.error(f"S3 sync failed for prefix '{s3_prefix}': {e}")
            return False

    logger.info(
        f"S3 sync complete — {total_downloaded} downloaded, {total_skipped} skipped."
    )
    return True


def upload_result_to_s3(local_path: Path, analysis_id: str):
    """
    Upload a finished GeoTIFF result back to S3 (optional — best-effort).
    S3 key: <county>/results/suitability_<analysis_id>.tif
    """
    bucket = os.environ.get("AWS_S3_BUCKET")
    if not bucket:
        return

    s3 = _s3_client()
    if s3 is None:
        return

    county  = CONFIG["county"]
    s3_key  = f"{county}/results/suitability_{analysis_id}.tif"

    try:
        s3.upload_file(str(local_path), bucket, s3_key)
        logger.info(f"  ↑ Uploaded result: s3://{bucket}/{s3_key}")
    except Exception as e:
        logger.warning(f"Result upload to S3 failed (non-fatal): {e}")


# ══════════════════════════════════════════════════════════════════════════════
# LAYER LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_layers():
    global NORMALIZED_LAYERS, LAYERS_PROFILE, RASTER_BOUNDS

    missing = []
    for name, path in PATHS["normalized_layers"].items():
        if not path.exists():
            missing.append(str(path))
            continue

        with rasterio.open(path) as src:
            NORMALIZED_LAYERS[name] = src.read(1).astype(np.float32)
            if LAYERS_PROFILE is None:
                LAYERS_PROFILE = src.profile.copy()
                b = src.bounds
                RASTER_BOUNDS = [[b.bottom, b.left], [b.top, b.right]]

        logger.info(f"  ✅ Loaded layer: {name}  ({path.name})")

    if missing:
        logger.warning(f"Missing normalized layers: {missing}")

    logger.info(
        f"[{CONFIG['display_name']}] Loaded {len(NORMALIZED_LAYERS)}/{len(PATHS['normalized_layers'])} layers"
    )
    if RASTER_BOUNDS:
        logger.info(f"   Raster bounds: {RASTER_BOUNDS}")


@app.on_event("startup")
async def startup_event():
    logger.info("=" * 55)
    logger.info(f"  Starting: {CONFIG['display_name']} — {CONFIG['crop']}")
    logger.info("=" * 55)

    # 1. Pull data from S3
    logger.info("── Syncing from S3 ──────────────────────────────────")
    ok = sync_county_from_s3()
    if not ok:
        logger.error("S3 sync failed — API may have no data to serve.")

    # 2. Load layers into memory
    logger.info("── Loading normalized layers ─────────────────────────")
    load_layers()


# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

def make_weights_model():
    defaults = CONFIG["weights"]
    fields   = {
        name: (float, Field(default, ge=0.0, le=1.0))
        for name, default in defaults.items()
    }
    from pydantic import create_model
    return create_model("Weights", **fields)

Weights = make_weights_model()


class SuitabilityRequest(BaseModel):
    weights:           dict
    apply_constraints: bool = Field(True)


class SuitabilityResponse(BaseModel):
    analysis_id:       str
    county:            str
    raster_bounds:     list
    suitability_range: Dict[str, float]
    statistics:        Dict[str, float]
    classification:    Dict[str, float]
    weights_used:      Dict[str, float]
    timestamp:         str


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    return {
        "county":  CONFIG["display_name"],
        "crop":    CONFIG["crop"],
        "version": "2.1.0",
        "endpoints": {
            "GET  /health":                           "Health check",
            "GET  /county":                           "Active county info",
            "GET  /criteria":                         "Analysis criteria",
            "GET  /boundary-geojson":                 "County boundary GeoJSON",
            "POST /analyze":                          "Run suitability analysis",
            "GET  /map-image/{analysis_id}":          "PNG overlay for Leaflet",
            "GET  /report-assets/{id}/{asset}":       "Rendered map or chart PNG",
            "POST /report/{analysis_id}":             "Generate PDF report",
            "POST /render/{analysis_id}":             "Re-render report assets",
            "GET  /results/{analysis_id}":            "Analysis metadata JSON",
            "GET  /download/{analysis_id}":           "Download GeoTIFF",
            "POST /admin/reload":                     "Re-sync S3 and reload layers",
        },
    }


@app.get("/health")
async def health_check():
    return {
        "status":             "healthy" if NORMALIZED_LAYERS else "degraded",
        "county":             CONFIG["display_name"],
        "layers_loaded":      len(NORMALIZED_LAYERS),
        "layers_expected":    len(PATHS["normalized_layers"]),
        "available_criteria": list(NORMALIZED_LAYERS.keys()),
        "boundary_available": PATHS["boundary"].exists(),
        "constraint_mask":    PATHS["constraint_mask"].exists(),
        "raster_bounds":      RASTER_BOUNDS,
        "s3_bucket":          os.environ.get("AWS_S3_BUCKET", "not configured"),
    }

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.get("/county")
async def get_county_info():
    return {
        "county":       CONFIG["county"],
        "display_name": CONFIG["display_name"],
        "country":      CONFIG["country"],
        "crop":         CONFIG["crop"],
        "map_center":   CONFIG["map_center"],
        "map_zoom":     CONFIG["map_zoom"],
        "weights":      CONFIG["weights"],
    }


@app.get("/criteria")
async def get_criteria():
    criteria_info = CONFIG["criteria_info"]
    weights       = CONFIG["weights"]
    return [
        {
            "name":           name,
            "description":    criteria_info[name]["description"],
            "optimal_range":  criteria_info[name]["optimal_range"],
            "current_weight": weights[name],
        }
        for name in weights
    ]


@app.get("/boundary-geojson")
async def get_boundary_geojson():
    if not PATHS["boundary"].exists():
        raise HTTPException(status_code=404, detail="Boundary file not found")
    gdf = gpd.read_file(PATHS["boundary"])
    if str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return json.loads(gdf.to_json())


@app.post("/analyze", response_model=SuitabilityResponse)
async def run_analysis(request: SuitabilityRequest):
    if not NORMALIZED_LAYERS:
        raise HTTPException(
            status_code=503,
            detail="No normalized layers loaded. Check /health and S3 configuration."
        )

    weights_dict = request.weights
    expected = set(CONFIG["weights"].keys())
    received = set(weights_dict.keys())
    if received != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Expected weights for {sorted(expected)}, got {sorted(received)}"
        )

    total = sum(weights_dict.values())
    if not np.isclose(total, 1.0, atol=0.01):
        raise HTTPException(
            status_code=400,
            detail=f"Weights must sum to 1.0 (currently {total:.3f})"
        )
    if not np.isclose(total, 1.0, atol=0.001):
        weights_dict = {k: v / total for k, v in weights_dict.items()}

    # Weighted overlay
    suitability = np.zeros_like(
        list(NORMALIZED_LAYERS.values())[0], dtype=np.float32
    )
    for name, weight in weights_dict.items():
        if name in NORMALIZED_LAYERS:
            suitability += NORMALIZED_LAYERS[name] * weight

    # Apply constraints
    if request.apply_constraints and PATHS["constraint_mask"].exists():
        with rasterio.open(PATHS["constraint_mask"]) as src:
            mask_aligned = np.zeros(suitability.shape, dtype=np.uint8)
            reproject(
                source=rasterio.band(src, 1),
                destination=mask_aligned,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=LAYERS_PROFILE["transform"],
                dst_crs=LAYERS_PROFILE["crs"],
                resampling=Resampling.nearest,
            )
            suitability = suitability * mask_aligned.astype(np.float32)

    suitability = np.clip(suitability, 0, 100)

    valid_data = suitability[suitability > 0]
    if valid_data.size == 0:
        raise HTTPException(
            status_code=500,
            detail="No valid pixels after constraints."
        )

    stats = {
        "min":    float(valid_data.min()),
        "max":    float(valid_data.max()),
        "mean":   float(valid_data.mean()),
        "std":    float(valid_data.std()),
        "median": float(np.median(valid_data)),
    }

    boundary_pixels = int((suitability > 0).sum())
    protected_pixels = 0
    if request.apply_constraints and PATHS["constraint_mask"].exists():
        with rasterio.open(PATHS["constraint_mask"]) as src:
            cmask = src.read(1)
        inside_boundary  = int((cmask > 0).sum())
        protected_pixels = max(0, inside_boundary - boundary_pixels)
    else:
        inside_boundary = boundary_pixels

    total_pixels = int(inside_boundary) if inside_boundary > 0 else suitability.size

    classification = {
        "highly_suitable_pct":     float((suitability >= 70).sum()                           / total_pixels * 100),
        "moderately_suitable_pct": float(((suitability >= 50) & (suitability < 70)).sum()   / total_pixels * 100),
        "marginally_suitable_pct": float(((suitability >= 30) & (suitability < 50)).sum()   / total_pixels * 100),
        "not_suitable_pct":        float(((suitability > 0)  & (suitability < 30)).sum()    / total_pixels * 100),
        "excluded_pct":            float(protected_pixels / total_pixels * 100),
    }

    # Save GeoTIFF locally
    PATHS["api_results_dir"].mkdir(parents=True, exist_ok=True)
    analysis_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tif_path    = PATHS["api_results_dir"] / f"suitability_{analysis_id}.tif"

    profile = LAYERS_PROFILE.copy()
    profile.update(dtype=rasterio.float32, compress="lzw", nodata=0)
    with rasterio.open(tif_path, "w", **profile) as dst:
        dst.write(suitability, 1)

    # Upload result back to S3 (best-effort, non-blocking)
    upload_result_to_s3(tif_path, analysis_id)

    # Render all map and chart assets for this analysis
    rendered = render_all(
        analysis_id    = analysis_id,
        classification = classification,
        weights        = weights_dict,
        config         = CONFIG,
        paths          = PATHS,
    )

    metadata = {
        "analysis_id":         analysis_id,
        "county":              CONFIG["county"],
        "raster_bounds":       RASTER_BOUNDS,
        "weights":             weights_dict,
        "statistics":          stats,
        "classification":      classification,
        "constraints_applied": request.apply_constraints,
        "timestamp":           datetime.now().isoformat(),
        "rendered_assets":     {k: str(v) for k, v in rendered.items() if v},
    }
    with open(PATHS["api_results_dir"] / f"metadata_{analysis_id}.json", "w") as f:
        json.dump(metadata, f, indent=2)

    return SuitabilityResponse(
        analysis_id=analysis_id,
        county=CONFIG["display_name"],
        raster_bounds=RASTER_BOUNDS,
        suitability_range={"min": stats["min"], "max": stats["max"]},
        statistics=stats,
        classification=classification,
        weights_used=weights_dict,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/map-image/{analysis_id}")
async def get_map_image(analysis_id: str):
    """Render suitability GeoTIFF as transparent RGBA PNG for Leaflet."""
    tif_path = PATHS["api_results_dir"] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)

    h, w = data.shape
    rgba  = np.zeros((h, w, 4), dtype=np.uint8)
    valid = data > 0
    s     = data

    r = np.select([s < 30, s < 50, s < 70, s >= 70], [239, 255, 102,  46], default=0)
    g = np.select([s < 30, s < 50, s < 70, s >= 70], [ 83, 167, 187, 125], default=0)
    b = np.select([s < 30, s < 50, s < 70, s >= 70], [ 80,  38, 106,  50], default=0)

    rgba[valid, 0] = np.clip(r[valid], 0, 255).astype(np.uint8)
    rgba[valid, 1] = np.clip(g[valid], 0, 255).astype(np.uint8)
    rgba[valid, 2] = np.clip(b[valid], 0, 255).astype(np.uint8)
    rgba[valid, 3] = 255

    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.get("/report-assets/{analysis_id}/{asset_name}")
async def get_report_asset(analysis_id: str, asset_name: str):
    """
    Serve a rendered report asset (map PNG or chart PNG) by name.

    asset_name options:
      suitability_map       — main 4-class suitability map
      criteria_grid         — 2×N grid of individual criterion layers
      classification_chart  — horizontal bar chart of class percentages
      weight_chart          — horizontal bar chart of criterion weights
    """
    filename_map = {
        "suitability_map":      f"suitability_map_{analysis_id}.png",
        "criteria_grid":        f"criteria_grid_{analysis_id}.png",
        "classification_chart": f"classification_chart_{analysis_id}.png",
        "weight_chart":         f"weight_chart_{analysis_id}.png",
    }
    if asset_name not in filename_map:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown asset '{asset_name}'. "
                   f"Choose from: {list(filename_map)}"
        )

    asset_path = PATHS["api_results_dir"] / filename_map[asset_name]
    if not asset_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Asset '{asset_name}' not yet rendered for analysis '{analysis_id}'. "
                   f"POST /render/{analysis_id} to generate it."
        )

    return FileResponse(path=str(asset_path), media_type="image/png")


@app.post("/report/{analysis_id}")
async def generate_report(analysis_id: str, depth: str = "full"):
    """
    Generate a PDF report for a completed analysis.

    Query params:
      depth = summary  →  2 pages: map, stats, classification, narrative
      depth = full     →  4 pages: adds criteria grid, methodology section (default)

    The report is saved alongside the GeoTIFF and returned as a download.
    LLM provider is controlled via the LLM_PROVIDER environment variable
    (groq | gemini | anthropic | ollama). Falls back to a template if unset.
    """
    if depth not in ("summary", "full"):
        raise HTTPException(
            status_code=400,
            detail="depth must be 'summary' or 'full'"
        )

    meta_path = PATHS["api_results_dir"] / f"metadata_{analysis_id}.json"
    if not meta_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Analysis '{analysis_id}' not found. Run POST /analyze first."
        )

    with open(meta_path) as f:
        metadata = json.load(f)

    # Resolve rendered asset paths (stored as strings in metadata)
    rendered = {
        k: Path(v)
        for k, v in metadata.get("rendered_assets", {}).items()
    }

    # If assets are missing (e.g. old analysis), render them now
    if not rendered:
        logger.info(f"No rendered assets found for {analysis_id} — rendering now")
        rendered = render_all(
            analysis_id    = analysis_id,
            classification = metadata["classification"],
            weights        = metadata["weights"],
            config         = CONFIG,
            paths          = PATHS,
        )
        metadata["rendered_assets"] = {k: str(v) for k, v in rendered.items() if v}
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

    pdf_path = build_report(
        analysis_id = analysis_id,
        metadata    = metadata,
        rendered    = rendered,
        config      = CONFIG,
        paths       = PATHS,
        depth       = depth,
    )

    county_slug = CONFIG["county"]
    filename    = f"{county_slug}_suitability_{analysis_id}_{depth}.pdf"

    return FileResponse(
        path       = str(pdf_path),
        media_type = "application/pdf",
        filename   = filename,
    )


@app.post("/render/{analysis_id}")
async def render_report_assets(analysis_id: str):
    """
    (Re)render all report assets (maps + charts) for an existing analysis.
    Useful if the analysis ran before map_renderer was added, or to
    regenerate after a config or styling change.
    """
    tif_path = PATHS["api_results_dir"] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Analysis '{analysis_id}' not found."
        )

    meta_path = PATHS["api_results_dir"] / f"metadata_{analysis_id}.json"
    with open(meta_path) as f:
        metadata = json.load(f)

    rendered = render_all(
        analysis_id    = analysis_id,
        classification = metadata["classification"],
        weights        = metadata["weights"],
        config         = CONFIG,
        paths          = PATHS,
    )

    # Persist updated asset paths back to metadata
    metadata["rendered_assets"] = {k: str(v) for k, v in rendered.items() if v}
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return {
        "analysis_id": analysis_id,
        "rendered":    list(rendered.keys()),
    }


@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str):
    path = PATHS["api_results_dir"] / f"metadata_{analysis_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    with open(path) as f:
        return json.load(f)


@app.get("/download/{analysis_id}")
async def download_geotiff(analysis_id: str):
    tif_path = PATHS["api_results_dir"] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    return FileResponse(
        path=str(tif_path),
        media_type="image/tiff",
        filename=f"{CONFIG['county']}_suitability_{analysis_id}.tif",
    )


@app.post("/admin/reload")
async def reload_layers():
    """
    Re-sync from S3 and reload normalized layers into memory.
    Useful after uploading new data without restarting the service.
    """
    global NORMALIZED_LAYERS, LAYERS_PROFILE, RASTER_BOUNDS
    NORMALIZED_LAYERS = {}
    LAYERS_PROFILE    = None
    RASTER_BOUNDS     = None

    ok = sync_county_from_s3()
    if not ok:
        raise HTTPException(status_code=500, detail="S3 sync failed")

    load_layers()
    return {
        "status":        "reloaded",
        "layers_loaded": len(NORMALIZED_LAYERS),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)