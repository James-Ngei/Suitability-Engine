"""
FastAPI Backend for Multi-Criteria Suitability Analysis
All county-specific config is read from the active county config file.
Switch counties by changing config/active_county.txt and restarting.
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
import json
from datetime import datetime
from PIL import Image
import io
import geopandas as gpd
import sys


sys.path.append(str(Path(__file__).parent))
from config import load_config

# ── Load county config ─────────────────────────────────────────────────────────
CONFIG = load_config()
PATHS  = CONFIG['_paths']

app = FastAPI(
    title=f"{CONFIG['crop']} Suitability Analysis API — {CONFIG['display_name']}",
    description=f"Multi-criteria suitability analysis for {CONFIG['crop'].lower()} "
                f"farming in {CONFIG['display_name']}, {CONFIG['country']}",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory layer cache ──────────────────────────────────────────────────────
NORMALIZED_LAYERS = {}
LAYERS_PROFILE    = None
RASTER_BOUNDS     = None


def load_layers():
    global NORMALIZED_LAYERS, LAYERS_PROFILE, RASTER_BOUNDS

    for name, path in PATHS['normalized_layers'].items():
        if path.exists():
            with rasterio.open(path) as src:
                NORMALIZED_LAYERS[name] = src.read(1).astype(np.float32)
                if LAYERS_PROFILE is None:
                    LAYERS_PROFILE = src.profile.copy()
                    b = src.bounds
                    RASTER_BOUNDS = [[b.bottom, b.left], [b.top, b.right]]

    print(f"✅ [{CONFIG['display_name']}] Loaded {len(NORMALIZED_LAYERS)} layers")
    if RASTER_BOUNDS:
        print(f"   Raster bounds: {RASTER_BOUNDS}")


@app.on_event("startup")
async def startup_event():
    load_layers()


# ── Pydantic models ────────────────────────────────────────────────────────────
def make_weights_model():
    """Dynamically build Weights model from config defaults."""
    defaults = CONFIG['weights']
    fields   = {
        name: (float, Field(default, ge=0.0, le=1.0))
        for name, default in defaults.items()
    }
    from pydantic import create_model
    return create_model('Weights', **fields)

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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "county":   CONFIG['display_name'],
        "crop":     CONFIG['crop'],
        "version":  "2.0.0",
        "endpoints": {
            "GET  /health":                  "Health check",
            "GET  /county":                  "Active county info",
            "GET  /criteria":                "Analysis criteria",
            "GET  /boundary-geojson":        "County boundary GeoJSON",
            "POST /analyze":                 "Run suitability analysis",
            "GET  /map-image/{analysis_id}": "PNG overlay for Leaflet",
            "GET  /results/{analysis_id}":   "Analysis metadata",
            "GET  /download/{analysis_id}":  "Download GeoTIFF",
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status":             "healthy",
        "county":             CONFIG['display_name'],
        "layers_loaded":      len(NORMALIZED_LAYERS),
        "available_criteria": list(NORMALIZED_LAYERS.keys()),
        "boundary_available": PATHS['boundary'].exists(),
        "raster_bounds":      RASTER_BOUNDS,
    }


@app.get("/county")
async def get_county_info():
    """Return active county metadata for the frontend."""
    return {
        "county":       CONFIG['county'],
        "display_name": CONFIG['display_name'],
        "country":      CONFIG['country'],
        "crop":         CONFIG['crop'],
        "map_center":   CONFIG['map_center'],
        "map_zoom":     CONFIG['map_zoom'],
        "weights":      CONFIG['weights'],
    }


@app.get("/criteria")
async def get_criteria():
    criteria_info = CONFIG['criteria_info']
    weights       = CONFIG['weights']
    return [
        {
            "name":          name,
            "description":   criteria_info[name]['description'],
            "optimal_range": criteria_info[name]['optimal_range'],
            "current_weight": weights[name],
        }
        for name in weights
    ]


@app.get("/boundary-geojson")
async def get_boundary_geojson():
    if not PATHS['boundary'].exists():
        raise HTTPException(status_code=404, detail="Boundary file not found")
    gdf = gpd.read_file(PATHS['boundary'])
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')
    return json.loads(gdf.to_json())


@app.post("/analyze", response_model=SuitabilityResponse)
async def run_analysis(request: SuitabilityRequest):
    """Run weighted overlay. Returns analysis_id and raster_bounds for map overlay."""

    weights_dict = request.weights
    # Validate keys match config layers
    expected = set(CONFIG['weights'].keys())
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
    suitability = np.zeros_like(list(NORMALIZED_LAYERS.values())[0], dtype=np.float32)
    for name, weight in weights_dict.items():
        if name in NORMALIZED_LAYERS:
            suitability += NORMALIZED_LAYERS[name] * weight

    # Apply constraints
    if request.apply_constraints and PATHS['constraint_mask'].exists():
        with rasterio.open(PATHS['constraint_mask']) as src:
            mask_aligned = np.zeros(suitability.shape, dtype=np.uint8)
            reproject(
                source=rasterio.band(src, 1),
                destination=mask_aligned,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=LAYERS_PROFILE['transform'],
                dst_crs=LAYERS_PROFILE['crs'],
                resampling=Resampling.nearest
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

    # Use only pixels inside the county boundary as denominator
    boundary_pixels = (suitability > 0).sum()  # non-zero = inside boundary & not protected
    # Load constraint mask to find protected pixels inside boundary
    protected_pixels = 0
    if request.apply_constraints and PATHS['constraint_mask'].exists():
        with rasterio.open(PATHS['constraint_mask']) as src:
            cmask = src.read(1)
        # Resize mask to match suitability shape if needed
        inside_boundary = (cmask > 0).sum()
        protected_pixels = int(inside_boundary - boundary_pixels)
        if protected_pixels < 0:
            protected_pixels = 0
    else:
        inside_boundary = boundary_pixels

    total_pixels = int(inside_boundary) if inside_boundary > 0 else suitability.size

    classification = {
        "highly_suitable_pct":     float((suitability >= 70).sum() / total_pixels * 100),
        "moderately_suitable_pct": float(((suitability >= 50) & (suitability < 70)).sum() / total_pixels * 100),
        "marginally_suitable_pct": float(((suitability >= 30) & (suitability < 50)).sum() / total_pixels * 100),
        "not_suitable_pct":        float(((suitability > 0)  & (suitability < 30)).sum() / total_pixels * 100),
        "excluded_pct":            float(protected_pixels / total_pixels * 100),
    }

    # Save GeoTIFF
    PATHS['api_results_dir'].mkdir(parents=True, exist_ok=True)
    analysis_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tif_path    = PATHS['api_results_dir'] / f"suitability_{analysis_id}.tif"
    profile     = LAYERS_PROFILE.copy()
    profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)
    with rasterio.open(tif_path, 'w', **profile) as dst:
        dst.write(suitability, 1)

    metadata = {
        "analysis_id":         analysis_id,
        "county":              CONFIG['county'],
        "raster_bounds":       RASTER_BOUNDS,
        "weights":             weights_dict,
        "statistics":          stats,
        "classification":      classification,
        "constraints_applied": request.apply_constraints,
        "timestamp":           datetime.now().isoformat(),
    }
    with open(PATHS['api_results_dir'] / f"metadata_{analysis_id}.json", 'w') as f:
        json.dump(metadata, f, indent=2)

    return SuitabilityResponse(
        analysis_id=analysis_id,
        county=CONFIG['display_name'],
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
    tif_path = PATHS['api_results_dir'] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)

    h, w  = data.shape
    rgba  = np.zeros((h, w, 4), dtype=np.uint8)
    valid = data > 0
    s     = data  # raw scores 0-100

    # Colour bands matching classification thresholds:
    # <30  → red    #ef5350  (239, 83, 80)
    # 30-50 → amber  #ffa726  (255, 167, 38)
    # 50-70 → light green #66bb6a (102, 187, 106)
    # >=70  → dark green #2e7d32 (46, 125, 50)
    r = np.select(
        [s < 30, s < 50, s < 70, s >= 70],
        [239,     255,    102,     46],
        default=0
    )
    g = np.select(
        [s < 30, s < 50, s < 70, s >= 70],
        [83,      167,    187,     125],
        default=0
    )
    b = np.select(
        [s < 30, s < 50, s < 70, s >= 70],
        [80,      38,     106,     50],
        default=0
    )

    rgba[valid, 0] = np.clip(r[valid], 0, 255).astype(np.uint8)
    rgba[valid, 1] = np.clip(g[valid], 0, 255).astype(np.uint8)
    rgba[valid, 2] = np.clip(b[valid], 0, 255).astype(np.uint8)
    rgba[valid, 3] = 255  # Opaque where valid, transparent where 0

    img = Image.fromarray(rgba, mode='RGBA')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str):
    path = PATHS['api_results_dir'] / f"metadata_{analysis_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    with open(path) as f:
        return json.load(f)


@app.get("/download/{analysis_id}")
async def download_geotiff(analysis_id: str):
    tif_path = PATHS['api_results_dir'] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    return FileResponse(
        path=str(tif_path),
        media_type="image/tiff",
        filename=f"{CONFIG['county']}_suitability_{analysis_id}.tif"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)