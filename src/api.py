"""
FastAPI Backend for Multi-Criteria Suitability Analysis
Exposes the suitability engine via REST API
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
from suitability import SuitabilityEngine

app = FastAPI(
    title="Cotton Suitability Analysis API",
    description="Multi-criteria suitability analysis for cotton farming in Bungoma County",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR       = Path.home() / 'suitability-engine'
NORMALIZED_DIR = BASE_DIR / 'data' / 'normalized'
RESULTS_DIR    = BASE_DIR / 'data' / 'api_results'
BOUNDARY_PATH  = BASE_DIR / 'data' / 'boundaries' / 'bungoma_boundary.gpkg'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── In-memory layer cache ──────────────────────────────────────────────────────
NORMALIZED_LAYERS = {}
LAYERS_PROFILE    = None
RASTER_BOUNDS     = None   # actual bounds read from raster on startup


def load_layers():
    global NORMALIZED_LAYERS, LAYERS_PROFILE, RASTER_BOUNDS
    for name in ['elevation', 'rainfall', 'temperature', 'soil', 'slope']:
        path = NORMALIZED_DIR / f'normalized_{name}.tif'
        if path.exists():
            with rasterio.open(path) as src:
                NORMALIZED_LAYERS[name] = src.read(1).astype(np.float32)
                if LAYERS_PROFILE is None:
                    LAYERS_PROFILE = src.profile.copy()
                    b = src.bounds
                    # [[south, west], [north, east]] — Leaflet order
                    RASTER_BOUNDS = [
                        [b.bottom, b.left],
                        [b.top,    b.right]
                    ]
                    print(f"  Raster bounds (Leaflet): {RASTER_BOUNDS}")
    print(f"✅ Loaded {len(NORMALIZED_LAYERS)} normalized layers")


@app.on_event("startup")
async def startup_event():
    load_layers()


# ── Pydantic models ────────────────────────────────────────────────────────────
class Weights(BaseModel):
    rainfall:    float = Field(0.25, ge=0.0, le=1.0)
    elevation:   float = Field(0.20, ge=0.0, le=1.0)
    temperature: float = Field(0.20, ge=0.0, le=1.0)
    soil:        float = Field(0.20, ge=0.0, le=1.0)
    slope:       float = Field(0.15, ge=0.0, le=1.0)


class SuitabilityRequest(BaseModel):
    weights:           Weights
    apply_constraints: bool = Field(True)


class SuitabilityResponse(BaseModel):
    analysis_id:       str
    raster_bounds:     list          # [[south, west], [north, east]] for Leaflet
    suitability_range: Dict[str, float]
    statistics:        Dict[str, float]
    classification:    Dict[str, float]
    weights_used:      Dict[str, float]
    timestamp:         str


class CriterionInfo(BaseModel):
    name:           str
    description:    str
    optimal_range:  str
    current_weight: float


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "message": "Cotton Suitability Analysis API",
        "version": "1.0.0",
        "endpoints": {
            "GET  /health":                  "Health check",
            "GET  /criteria":                "List analysis criteria",
            "GET  /boundary-geojson":        "Bungoma boundary as GeoJSON",
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
        "layers_loaded":      len(NORMALIZED_LAYERS),
        "available_criteria": list(NORMALIZED_LAYERS.keys()),
        "boundary_available": BOUNDARY_PATH.exists(),
        "raster_bounds":      RASTER_BOUNDS,
    }


@app.get("/criteria", response_model=List[CriterionInfo])
async def get_criteria():
    return [
        {"name": "rainfall",    "description": "Annual rainfall in mm/year",         "optimal_range": "1400-1800 mm",            "current_weight": 0.25},
        {"name": "elevation",   "description": "Elevation above sea level in metres", "optimal_range": "1200-1700 m",             "current_weight": 0.20},
        {"name": "temperature", "description": "Mean annual temperature in °C",       "optimal_range": "20-30 °C (optimal 25°C)", "current_weight": 0.20},
        {"name": "soil",        "description": "Soil clay content (SoilGrids g/kg)",  "optimal_range": "250-380 g/kg",            "current_weight": 0.20},
        {"name": "slope",       "description": "Terrain slope in degrees",            "optimal_range": "0-5° (max 15°)",          "current_weight": 0.15},
    ]


@app.get("/boundary-geojson")
async def get_boundary_geojson():
    """Return Bungoma County boundary as GeoJSON (EPSG:4326)."""
    if not BOUNDARY_PATH.exists():
        raise HTTPException(status_code=404, detail="Boundary file not found")
    gdf = gpd.read_file(BOUNDARY_PATH)
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')
    return json.loads(gdf.to_json())


@app.post("/analyze", response_model=SuitabilityResponse)
async def run_analysis(request: SuitabilityRequest):
    """Run weighted overlay. Returns analysis_id and exact raster_bounds for map overlay."""

    weights_dict = request.weights.dict()
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

    # Apply constraints — reproject mask to match suitability grid
    if request.apply_constraints:
        constraint_path = BASE_DIR / 'data' / 'preprocessed' / 'bungoma_constraints_mask.tif'
        if constraint_path.exists():
            with rasterio.open(constraint_path) as src:
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
            detail="No valid pixels after constraints — check constraint raster."
        )

    stats = {
        "min":    float(valid_data.min()),
        "max":    float(valid_data.max()),
        "mean":   float(valid_data.mean()),
        "std":    float(valid_data.std()),
        "median": float(np.median(valid_data)),
    }

    total_pixels = suitability.size
    classification = {
        "highly_suitable_pct":     float((suitability >= 70).sum() / total_pixels * 100),
        "moderately_suitable_pct": float(((suitability >= 50) & (suitability < 70)).sum() / total_pixels * 100),
        "marginally_suitable_pct": float(((suitability >= 30) & (suitability < 50)).sum() / total_pixels * 100),
        "not_suitable_pct":        float(((suitability > 0)  & (suitability < 30)).sum() / total_pixels * 100),
        "excluded_pct":            float((suitability == 0).sum() / total_pixels * 100),
    }

    # Save GeoTIFF
    analysis_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tif_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    profile = LAYERS_PROFILE.copy()
    profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)
    with rasterio.open(tif_path, 'w', **profile) as dst:
        dst.write(suitability, 1)

    # Save metadata
    metadata = {
        "analysis_id":         analysis_id,
        "raster_bounds":       RASTER_BOUNDS,
        "weights":             weights_dict,
        "statistics":          stats,
        "classification":      classification,
        "constraints_applied": request.apply_constraints,
        "timestamp":           datetime.now().isoformat(),
    }
    with open(RESULTS_DIR / f"metadata_{analysis_id}.json", 'w') as f:
        json.dump(metadata, f, indent=2)

    return SuitabilityResponse(
        analysis_id=analysis_id,
        raster_bounds=RASTER_BOUNDS,
        suitability_range={"min": stats["min"], "max": stats["max"]},
        statistics=stats,
        classification=classification,
        weights_used=weights_dict,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/map-image/{analysis_id}")
async def get_map_image(analysis_id: str):
    """
    Render the suitability GeoTIFF as a transparent RGBA PNG for Leaflet ImageOverlay.
    Colour ramp: red (low) → amber → green (high). Nodata pixels are fully transparent.
    """
    tif_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)

    h, w   = data.shape
    rgba   = np.zeros((h, w, 4), dtype=np.uint8)
    valid  = data > 0
    norm   = np.clip(data / 100.0, 0, 1)

    # Red → amber → green colour ramp
    r = np.where(norm < 0.5,
                 239 + (255 - 239) * (norm * 2),
                 255 + (46  - 255) * ((norm - 0.5) * 2))
    g = np.where(norm < 0.5,
                 83  + (167 - 83)  * (norm * 2),
                 167 + (125 - 167) * ((norm - 0.5) * 2))
    b = np.where(norm < 0.5,
                 80  + (38  - 80)  * (norm * 2),
                 38  + (50  - 38)  * ((norm - 0.5) * 2))

    rgba[valid, 0] = np.clip(r[valid], 0, 255).astype(np.uint8)
    rgba[valid, 1] = np.clip(g[valid], 0, 255).astype(np.uint8)
    rgba[valid, 2] = np.clip(b[valid], 0, 255).astype(np.uint8)
    rgba[valid, 3] = 200  # semi-transparent; nodata stays 0 (fully transparent)

    img = Image.fromarray(rgba, mode='RGBA')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return Response(content=buf.read(), media_type="image/png")


@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str):
    path = RESULTS_DIR / f"metadata_{analysis_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    with open(path) as f:
        return json.load(f)


@app.get("/download/{analysis_id}")
async def download_geotiff(analysis_id: str):
    tif_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    return FileResponse(
        path=str(tif_path),
        media_type="image/tiff",
        filename=f"cotton_suitability_{analysis_id}.tif"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)