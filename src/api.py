"""
FastAPI Backend for Multi-Criteria Suitability Analysis
Exposes the suitability engine via REST API
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import numpy as np
import rasterio
from pathlib import Path
import json
from datetime import datetime

# Import our suitability engine
import sys
sys.path.append(str(Path(__file__).parent))
from suitability import SuitabilityEngine

app = FastAPI(
    title="Cotton Suitability Analysis API",
    description="Multi-criteria suitability analysis for cotton farming in Bungoma County",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
BASE_DIR = Path.home() / 'suitability-engine'
NORMALIZED_DIR = BASE_DIR / 'data' / 'normalized'
RESULTS_DIR = BASE_DIR / 'data' / 'api_results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Load normalized layers into memory for speed
NORMALIZED_LAYERS = {}
LAYERS_PROFILE = None

def load_layers():
    """Load all normalized layers into memory on startup"""
    global NORMALIZED_LAYERS, LAYERS_PROFILE
    
    layer_names = ['elevation', 'rainfall', 'temperature', 'soil', 'slope']
    
    for name in layer_names:
        path = NORMALIZED_DIR / f'normalized_{name}.tif'
        if path.exists():
            with rasterio.open(path) as src:
                NORMALIZED_LAYERS[name] = src.read(1).astype(np.float32)
                if LAYERS_PROFILE is None:
                    LAYERS_PROFILE = src.profile.copy()
    
    print(f"✅ Loaded {len(NORMALIZED_LAYERS)} normalized layers")

# Load on startup
@app.on_event("startup")
async def startup_event():
    load_layers()

# Pydantic models
class Weights(BaseModel):
    rainfall: float = Field(0.25, ge=0.0, le=1.0, description="Rainfall weight (0-1)")
    elevation: float = Field(0.20, ge=0.0, le=1.0, description="Elevation weight (0-1)")
    temperature: float = Field(0.20, ge=0.0, le=1.0, description="Temperature weight (0-1)")
    soil: float = Field(0.20, ge=0.0, le=1.0, description="Soil drainage weight (0-1)")
    slope: float = Field(0.15, ge=0.0, le=1.0, description="Slope weight (0-1)")
    
    class Config:
        schema_extra = {
            "example": {
                "rainfall": 0.25,
                "elevation": 0.20,
                "temperature": 0.20,
                "soil": 0.20,
                "slope": 0.15
            }
        }

class SuitabilityRequest(BaseModel):
    weights: Weights
    apply_constraints: bool = Field(True, description="Apply protected area constraints")

class SuitabilityResponse(BaseModel):
    suitability_range: Dict[str, float]
    statistics: Dict[str, float]
    classification: Dict[str, float]
    weights_used: Dict[str, float]
    timestamp: str

class CriterionInfo(BaseModel):
    name: str
    description: str
    optimal_range: str
    current_weight: float

# API Endpoints

@app.get("/")
async def root():
    """API information"""
    return {
        "message": "Cotton Suitability Analysis API",
        "version": "1.0.0",
        "endpoints": {
            "GET /health": "Health check",
            "GET /criteria": "List available criteria",
            "POST /analyze": "Run suitability analysis",
            "GET /results/{analysis_id}": "Get analysis results",
            "GET /download/{analysis_id}": "Download GeoTIFF"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "layers_loaded": len(NORMALIZED_LAYERS),
        "available_criteria": list(NORMALIZED_LAYERS.keys())
    }

@app.get("/criteria", response_model=List[CriterionInfo])
async def get_criteria():
    """Get information about all criteria"""
    criteria = [
        {
            "name": "rainfall",
            "description": "Annual rainfall in mm/year",
            "optimal_range": "700-1000 mm",
            "current_weight": 0.25
        },
        {
            "name": "elevation",
            "description": "Elevation above sea level in meters",
            "optimal_range": "0-1000 m",
            "current_weight": 0.20
        },
        {
            "name": "temperature",
            "description": "Mean annual temperature in °C",
            "optimal_range": "20-30 °C (optimal at 25°C)",
            "current_weight": 0.20
        },
        {
            "name": "soil",
            "description": "Soil drainage quality",
            "optimal_range": "Well-drained (class 3)",
            "current_weight": 0.20
        },
        {
            "name": "slope",
            "description": "Terrain slope in degrees",
            "optimal_range": "0-5° (max 15°)",
            "current_weight": 0.15
        }
    ]
    return criteria

@app.post("/analyze", response_model=SuitabilityResponse)
async def run_analysis(request: SuitabilityRequest):
    """
    Run suitability analysis with custom weights
    
    - **weights**: Dictionary of criterion weights (must sum to 1.0)
    - **apply_constraints**: Whether to exclude protected areas
    """
    
    # Validate weights sum to 1.0
    weights_dict = request.weights.dict()
    total = sum(weights_dict.values())
    
    if not np.isclose(total, 1.0, atol=0.01):
        raise HTTPException(
            status_code=400, 
            detail=f"Weights must sum to 1.0 (currently {total:.3f})"
        )
    
    # Normalize if slightly off
    if not np.isclose(total, 1.0, atol=0.001):
        weights_dict = {k: v/total for k, v in weights_dict.items()}
    
    # Calculate suitability
    suitability = np.zeros_like(list(NORMALIZED_LAYERS.values())[0], dtype=np.float32)
    
    for name, weight in weights_dict.items():
        if name in NORMALIZED_LAYERS:
            suitability += NORMALIZED_LAYERS[name] * weight
    
    # Apply constraints if requested
    if request.apply_constraints:
        constraint_path = BASE_DIR / 'data' / 'raw' / 'constraints' / 'bungoma_protected.tif'
        if constraint_path.exists():
            with rasterio.open(constraint_path) as src:
                protected = src.read(1)
                mask = np.where(protected == 1, 0, 1)
                suitability = suitability * mask
    
    # Calculate statistics
    valid_data = suitability[suitability > 0]
    
    stats = {
        "min": float(valid_data.min()),
        "max": float(valid_data.max()),
        "mean": float(valid_data.mean()),
        "std": float(valid_data.std()),
        "median": float(np.median(valid_data))
    }
    
    # Classification
    total_pixels = suitability.size
    classification = {
        "highly_suitable_pct": float((suitability >= 70).sum() / total_pixels * 100),
        "moderately_suitable_pct": float(((suitability >= 50) & (suitability < 70)).sum() / total_pixels * 100),
        "marginally_suitable_pct": float(((suitability >= 30) & (suitability < 50)).sum() / total_pixels * 100),
        "not_suitable_pct": float(((suitability > 0) & (suitability < 30)).sum() / total_pixels * 100),
        "excluded_pct": float((suitability == 0).sum() / total_pixels * 100)
    }
    
    # Save result
    analysis_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    
    profile = LAYERS_PROFILE.copy()
    profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)
    
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(suitability, 1)
    
    # Save metadata
    metadata = {
        "analysis_id": analysis_id,
        "weights": weights_dict,
        "statistics": stats,
        "classification": classification,
        "constraints_applied": request.apply_constraints,
        "timestamp": datetime.now().isoformat()
    }
    
    metadata_path = RESULTS_DIR / f"metadata_{analysis_id}.json"
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    return SuitabilityResponse(
        suitability_range={"min": stats["min"], "max": stats["max"]},
        statistics=stats,
        classification=classification,
        weights_used=weights_dict,
        timestamp=datetime.now().isoformat()
    )

@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str):
    """Get metadata for a specific analysis"""
    metadata_path = RESULTS_DIR / f"metadata_{analysis_id}.json"
    
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    with open(metadata_path, 'r') as f:
        metadata = json.load(f)
    
    return metadata

@app.get("/download/{analysis_id}")
async def download_geotiff(analysis_id: str):
    """Download suitability GeoTIFF"""
    geotiff_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    
    if not geotiff_path.exists():
        raise HTTPException(status_code=404, detail="GeoTIFF not found")
    
    return FileResponse(
        geotiff_path,
        media_type="image/tiff",
        filename=f"cotton_suitability_{analysis_id}.tif"
    )

@app.get("/geojson/{analysis_id}")
async def get_geojson(
    analysis_id: str,
    threshold: float = Query(70.0, ge=0.0, le=100.0, description="Suitability threshold")
):
    """
    Get suitable areas as GeoJSON (pixels above threshold)
    """
    geotiff_path = RESULTS_DIR / f"suitability_{analysis_id}.tif"
    
    if not geotiff_path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    
    with rasterio.open(geotiff_path) as src:
        suitability = src.read(1)
        transform = src.transform
        
        # Find suitable pixels
        suitable_mask = suitability >= threshold
        
        # Convert to features
        features = []
        rows, cols = np.where(suitable_mask)
        
        for row, col in zip(rows[:100], cols[:100]):  # Limit to 100 for demo
            lon, lat = transform * (col, row)
            score = float(suitability[row, col])
            
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "suitability": score
                }
            })
        
        geojson = {
            "type": "FeatureCollection",
            "features": features
        }
        
        return geojson

@app.get("/default-weights")
async def get_default_weights():
    """Get default criterion weights"""
    return {
        "rainfall": 0.25,
        "elevation": 0.20,
        "temperature": 0.20,
        "soil": 0.20,
        "slope": 0.15
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)