"""
FastAPI Backend — Crop Suitability Engine
No S3. Data is fetched on-demand from Planetary Computer + NASA POWER and
cached on the local filesystem (survives the Render session; gone on redeploy).

County switching:
  - Default county set via ACTIVE_COUNTY env var (falls back to active_county.txt)
  - Any endpoint that takes county-specific data accepts ?county= query param
  - POST /admin/load-county?county=bungoma   triggers fetch + pipeline for a new county
  - GET  /status/{county}                    returns fetch/pipeline progress

Startup behaviour:
  - Loads whichever counties are already cached (normalized layers exist locally)
  - Kicks off a background fetch for ACTIVE_COUNTY if not cached
  - API is immediately available for cached counties; /health reports per-county status
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import geopandas as gpd
import numpy as np
import rasterio
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image
import io
from pydantic import BaseModel, Field
from rasterio.warp import Resampling, reproject

logging.basicConfig(level=logging.INFO)
logging.getLogger("botocore").setLevel(logging.WARNING)
logger = logging.getLogger("suitability-api")

sys.path.append(str(Path(__file__).parent))
from config import load_config, list_counties, list_crops, get_active_county, get_active_crop, load_crop_config


# ══════════════════════════════════════════════════════════════════════════════
# R2 STORAGE  (Cloudflare R2 — S3-compatible, free tier 10GB)
# Set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET in env.
# If not set, startup falls back to PC/NASA fetch (slower but always works).
# ══════════════════════════════════════════════════════════════════════════════

def _r2_client():
    """Return a boto3 client pointed at Cloudflare R2, or None if not configured."""
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([account_id, access_key, secret_key]):
        return None
    try:
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
    except Exception as e:
        logger.warning(f"R2 client init failed: {e}")
        return None


def _r2_bucket() -> str:
    return os.environ.get("R2_BUCKET", "suitability-engine")


def _r2_has_county(county: str, country: str = "kenya") -> bool:
    """Check if R2 has normalized layers for this county (fast list call)."""
    client = _r2_client()
    if not client:
        return False
    try:
        prefix = f"{country}/{county}/normalized/"
        resp   = client.list_objects_v2(Bucket=_r2_bucket(), Prefix=prefix, MaxKeys=1)
        return resp.get("KeyCount", 0) > 0
    except Exception:
        return False


def sync_county_from_r2(county: str) -> bool:
    """
    Download normalized layers + boundary + constraint mask from R2.
    Returns True if all normalized layers were downloaded successfully.
    Much faster than running the pipeline (~30s vs 12-17min).
    """
    client = _r2_client()
    if not client:
        logger.info(f"[{county}] R2 not configured — will use PC/NASA fetch")
        return False

    try:
        config  = load_config(county)
    except Exception as e:
        logger.warning(f"[{county}] Config load failed: {e}")
        return False

    paths   = config["_paths"]
    country = config.get("country", "kenya").lower()
    bucket  = _r2_bucket()

    # What to sync: (r2_prefix, local_dir, required_for_success)
    sync_targets = [
        (f"{country}/{county}/normalized/",   paths["normalized_dir"],         True),
        (f"{country}/{county}/boundaries/",   paths["boundary"].parent,        False),
        (f"{country}/{county}/preprocessed/", paths["constraint_mask"].parent, False),
    ]

    normalized_count = 0
    expected         = len(config["layers"])

    for prefix, local_dir, required in sync_targets:
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    filename   = obj["Key"].split("/")[-1]
                    if not filename:
                        continue
                    local_path = local_dir / filename
                    # Skip if up to date
                    if local_path.exists():
                        local_mtime = local_path.stat().st_mtime
                        r2_mtime    = obj["LastModified"].timestamp()
                        if local_mtime >= r2_mtime:
                            if "normalized" in prefix:
                                normalized_count += 1
                            continue
                    logger.info(f"[{county}] ↓ {filename}")
                    client.download_file(bucket, obj["Key"], str(local_path))
                    if "normalized" in prefix:
                        normalized_count += 1
        except Exception as e:
            logger.warning(f"[{county}] R2 sync failed for {prefix}: {e}")
            if required:
                return False

    success = normalized_count >= expected
    if success:
        logger.info(f"[{county}] R2 sync complete: {normalized_count}/{expected} normalized layers")
    else:
        logger.warning(f"[{county}] R2 sync incomplete: {normalized_count}/{expected} — will fall back to PC fetch")
    return success


def upload_county_to_r2(county: str) -> bool:
    """
    Upload normalized layers + boundary + constraint mask to R2 after pipeline.
    Called automatically after a successful PC fetch + pipeline run.
    """
    client = _r2_client()
    if not client:
        return False

    try:
        config  = load_config(county)
    except Exception:
        return False

    paths   = config["_paths"]
    country = config.get("country", "kenya").lower()
    bucket  = _r2_bucket()
    count   = 0

    uploads = []
    # Normalized layers
    for path in paths["normalized_layers"].values():
        if path.exists():
            uploads.append((path, f"{country}/{county}/normalized/{path.name}"))
    # Boundary
    if paths["boundary"].exists():
        uploads.append((paths["boundary"], f"{country}/{county}/boundaries/{paths['boundary'].name}"))
    # Constraint mask
    if paths["constraint_mask"].exists():
        uploads.append((paths["constraint_mask"], f"{country}/{county}/preprocessed/{paths['constraint_mask'].name}"))

    for local_path, key in uploads:
        try:
            client.upload_file(str(local_path), bucket, key)
            logger.info(f"[{county}] ↑ {local_path.name} → R2")
            count += 1
        except Exception as e:
            logger.warning(f"[{county}] R2 upload failed for {local_path.name}: {e}")

    logger.info(f"[{county}] Uploaded {count}/{len(uploads)} files to R2")
    return count > 0


# ── Per-county in-memory cache ─────────────────────────────────────────────────
# { county_id: { "layers": {name: np.ndarray}, "profile": dict, "bounds": list } }
COUNTY_CACHE: Dict[str, dict] = {}

# Tracks background fetch/pipeline progress per county
# { county_id: { "status": "idle|fetching|pipeline|ready|error", "message": str, "pct": int } }
COUNTY_STATUS: Dict[str, dict] = {}


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Crop Suitability Engine API",
    description="Multi-criteria suitability analysis. Dynamic county selection, PC-backed data.",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# CACHE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _set_status(county: str, status: str, message: str, pct: int = 0):
    COUNTY_STATUS[county] = {"status": status, "message": message, "pct": pct, "updated": datetime.now().isoformat()}
    logger.info(f"[{county}] {status.upper()} — {message}")


def load_county_layers(county: str) -> bool:
    """
    Load normalized layers for a county into COUNTY_CACHE.
    Returns True if all layers loaded successfully.
    """
    try:
        config = load_config(county)
    except Exception as e:
        _set_status(county, "error", f"Config load failed: {e}")
        return False

    paths = config["_paths"]
    layers = {}
    profile = None
    bounds = None

    for name, path in paths["normalized_layers"].items():
        if not path.exists():
            logger.warning(f"[{county}] Missing normalized layer: {path.name}")
            continue
        with rasterio.open(path) as src:
            layers[name] = src.read(1).astype(np.float32)
            if profile is None:
                profile = src.profile.copy()
                b = src.bounds
                bounds = [[b.bottom, b.left], [b.top, b.right]]
        logger.info(f"[{county}] Loaded: {name}")

    expected = len(config["layers"])
    if len(layers) == 0:
        return False

    COUNTY_CACHE[county] = {
        "layers":  layers,
        "profile": profile,
        "bounds":  bounds,
        "config":  config,
    }

    if len(layers) < expected:
        _set_status(county, "ready", f"Partial load: {len(layers)}/{expected} layers", pct=100)
    else:
        _set_status(county, "ready", f"All {expected} layers loaded", pct=100)

    return len(layers) > 0


def _run_pipeline(county: str):
    """Run the 4-step preprocessing pipeline for a county (blocking)."""
    scripts = [
        "src/preprocess.py",
        "src/realign_to_boundary.py",
        "src/normalize.py",
        "src/clip_to_boundary.py",
    ]
    total = len(scripts)
    for i, script in enumerate(scripts):
        _set_status(county, "pipeline", f"Running {script}", pct=50 + int((i / total) * 45))
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True,
            env={**os.environ, "ACTIVE_COUNTY": county},
        )
        if result.returncode != 0:
            _set_status(county, "error", f"{script} failed: {result.stderr[:300]}")
            return False
        logger.info(f"[{county}] {script} done")
    return True


async def _fetch_and_prepare_county(county: str):
    """
    Background task: fetch from PC → run pipeline → load layers.
    Runs in a thread pool so it doesn't block the event loop.
    """
    loop = asyncio.get_event_loop()

    try:
        config = load_config(county)
    except Exception as e:
        _set_status(county, "error", f"Config not found: {e}")
        return

    from pc_fetcher import fetch_all_layers, layers_are_cached

    # Step 1 — fetch raw layers
    if not layers_are_cached(config):
        _set_status(county, "fetching", "Downloading raw layers from Planetary Computer / NASA POWER", pct=5)
        try:
            await loop.run_in_executor(None, lambda: fetch_all_layers(config))
            _set_status(county, "fetching", "Raw layers downloaded", pct=48)
        except Exception as e:
            _set_status(county, "error", f"PC fetch failed: {e}")
            return
    else:
        _set_status(county, "pipeline", "Raw layers already cached — skipping fetch", pct=48)

    # Step 2 — run pipeline
    _set_status(county, "pipeline", "Running preprocessing pipeline", pct=50)
    ok = await loop.run_in_executor(None, lambda: _run_pipeline(county))
    if not ok:
        return  # status already set to error inside _run_pipeline

    # Step 3 — upload to R2 so next cold start is fast (best-effort, non-blocking)
    if os.environ.get("R2_ACCOUNT_ID"):
        _set_status(county, "pipeline", "Uploading to R2 for future fast startup", pct=94)
        try:
            await loop.run_in_executor(None, lambda: upload_county_to_r2(county))
        except Exception as e:
            logger.warning(f"[{county}] R2 upload failed (non-fatal): {e}")

    # Step 4 — load into memory
    _set_status(county, "pipeline", "Loading layers into memory", pct=97)
    ok = await loop.run_in_executor(None, lambda: load_county_layers(county))
    if not ok:
        _set_status(county, "error", "Layer load failed after pipeline")


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    logger.info("=" * 55)
    logger.info("  Crop Suitability Engine  v3.0")
    r2_configured = bool(os.environ.get("R2_ACCOUNT_ID"))
    logger.info(f"  Storage: {'Cloudflare R2' if r2_configured else 'PC/NASA fetch (no R2 configured)'}")
    logger.info("=" * 55)

    active = get_active_county()

    # Phase 1: load whatever is already cached locally (instant)
    for county in list_counties():
        try:
            cfg = load_config(county)
            if any(p.exists() for p in cfg["_paths"]["normalized_layers"].values()):
                logger.info(f"[{county}] Local cache found — loading...")
                load_county_layers(county)
            else:
                _set_status(county, "idle", "Not yet fetched")
        except Exception as e:
            logger.warning(f"[{county}] Skipping: {e}")

    # Phase 2: if active county not loaded, try R2 then fall back to PC fetch
    if active not in COUNTY_CACHE:
        if r2_configured and _r2_has_county(active):
            logger.info(f"[{active}] Pulling from R2...")
            _set_status(active, "fetching", "Downloading from R2 storage", pct=10)
            loop = asyncio.get_event_loop()
            ok   = await loop.run_in_executor(None, lambda: sync_county_from_r2(active))
            if ok:
                load_county_layers(active)
                logger.info(f"[{active}] Loaded from R2 ✅")
            else:
                logger.warning(f"[{active}] R2 pull incomplete — falling back to PC fetch")
                asyncio.create_task(_fetch_and_prepare_county(active))
        else:
            logger.info(f"[{active}] Not in R2 or R2 not configured — starting PC fetch...")
            asyncio.create_task(_fetch_and_prepare_county(active))
    else:
        logger.info(f"[{active}] Already loaded ✅")

    # Build RAG store (best-effort)
    try:
        from report_writer import build_rag_store
        build_rag_store()
    except Exception as e:
        logger.warning(f"RAG store: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _require_county(county: Optional[str]) -> str:
    c = (county or get_active_county()).strip().lower()
    return c

def _require_crop(crop: Optional[str]) -> str:
    return (crop or get_active_crop()).strip().lower()

def _require_loaded(county: str) -> dict:
    """Return county cache entry or raise 503 with status info."""
    if county not in COUNTY_CACHE:
        status = COUNTY_STATUS.get(county, {})
        st = status.get("status", "unknown")
        msg = status.get("message", "County not loaded")
        pct = status.get("pct", 0)
        if st in ("fetching", "pipeline"):
            raise HTTPException(
                status_code=503,
                detail={"status": st, "message": msg, "pct": pct,
                        "hint": f"Data is being prepared. Poll GET /status/{county}"}
            )
        raise HTTPException(
            status_code=404,
            detail={"status": st, "message": msg,
                    "hint": f"POST /admin/load-county?county={county} to fetch data"}
        )
    return COUNTY_CACHE[county]


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — metadata
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def root():
    active = get_active_county()
    return {
        "version": "3.0.0",
        "active_county": active,
        "loaded_counties": list(COUNTY_CACHE.keys()),
        "available_counties": list_counties(),
    }


@app.get("/health")
async def health():
    active = get_active_county()
    loaded = list(COUNTY_CACHE.keys())
    per_county = {}
    for c in list_counties():
        st = COUNTY_STATUS.get(c, {})
        cache = COUNTY_CACHE.get(c, {})
        per_county[c] = {
            "status":        st.get("status", "idle"),
            "message":       st.get("message", ""),
            "pct":           st.get("pct", 0),
            "layers_loaded": len(cache.get("layers", {})),
        }
    return {
        "status":          "healthy" if loaded else "degraded",
        "active_county":   active,
        "loaded_counties": loaded,
        "counties":        per_county,
    }


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.get("/status/{county}")
async def county_status(county: str):
    """Poll this to track fetch/pipeline progress for a county."""
    c = county.strip().lower()
    st = COUNTY_STATUS.get(c, {"status": "idle", "message": "Not started", "pct": 0})
    cache = COUNTY_CACHE.get(c, {})
    return {
        "county":        c,
        "layers_loaded": len(cache.get("layers", {})),
        **st,
    }


@app.get("/counties")
async def list_all_counties():
    """All available county configs + their current load status."""
    result = []
    for c in list_counties():
        try:
            cfg = load_config(c)
            st  = COUNTY_STATUS.get(c, {"status": "idle", "pct": 0})
            result.append({
                "county":       c,
                "display_name": cfg.get("display_name", c),
                "country":      cfg.get("country", ""),
                "crop":         cfg.get("crop", ""),
                "status":       st.get("status", "idle"),
                "pct":          st.get("pct", 0),
                "loaded":       c in COUNTY_CACHE,
            })
        except Exception:
            pass
    return result



@app.get("/crops")
async def list_all_crops():
    """All available crop configs."""
    result = []
    for crop_id in list_crops():
        try:
            cfg = load_crop_config(crop_id)
            result.append({
                "crop_id":       cfg["crop_id"],
                "display_name":  cfg["display_name"],
                "scientific_name": cfg.get("scientific_name", ""),
                "description":   cfg.get("description", ""),
            })
        except Exception:
            pass
    return result


@app.get("/county")
async def get_county_info(county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    """County metadata. ?county= overrides the default active county."""
    c = _require_county(county)
    cr = _require_crop(crop)
    try:
        cfg = load_config(c, cr)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No config for county '{c}'")
    return {
        "county":       cfg["county"],
        "display_name": cfg["display_name"],
        "country":      cfg["country"],
        "crop":         cfg["crop"],
        "map_center":   cfg["map_center"],
        "map_zoom":     cfg["map_zoom"],
        "weights":      cfg["weights"],
        "loaded":       c in COUNTY_CACHE,
        "status":       COUNTY_STATUS.get(c, {}).get("status", "idle"),
    }


@app.get("/criteria")
async def get_criteria(county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    c   = _require_county(county)
    cr  = _require_crop(crop)
    cfg = load_config(c, cr)
    return [
        {
            "name":          name,
            "description":   cfg["criteria_info"][name]["description"],
            "optimal_range": cfg["criteria_info"][name]["optimal_range"],
            "current_weight": cfg["weights"][name],
        }
        for name in cfg["weights"]
    ]


@app.get("/boundary-geojson")
async def get_boundary_geojson(county: Optional[str] = Query(None)):
    c    = _require_county(county)
    try:
        cfg  = load_config(c)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No config for '{c}'")
    path = cfg["_paths"]["boundary"]
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Boundary not found for '{c}'. Try POST /admin/load-county?county={c}")
    gdf = gpd.read_file(path)
    if str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return json.loads(gdf.to_json())


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — analysis
# ══════════════════════════════════════════════════════════════════════════════

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


@app.post("/analyze", response_model=SuitabilityResponse)
async def run_analysis(request: SuitabilityRequest, county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    c     = _require_county(county)
    cr    = _require_crop(crop)
    cache = _require_loaded(c)

    # Reload config with specific crop (cache stores base county config)
    cfg = load_config(c, cr)
    layers       = cache["layers"]
    profile      = cache["profile"]
    raster_bounds = cache["bounds"]
    paths        = cfg["_paths"]

    weights_dict = request.weights
    expected = set(cfg["weights"].keys())
    received = set(weights_dict.keys())
    if received != expected:
        raise HTTPException(status_code=400,
            detail=f"Expected weights for {sorted(expected)}, got {sorted(received)}")

    total = sum(weights_dict.values())
    if not np.isclose(total, 1.0, atol=0.01):
        raise HTTPException(status_code=400,
            detail=f"Weights must sum to 1.0 (currently {total:.3f})")
    if not np.isclose(total, 1.0, atol=0.001):
        weights_dict = {k: v / total for k, v in weights_dict.items()}

    # Weighted overlay
    suitability = np.zeros_like(list(layers.values())[0], dtype=np.float32)
    for name, weight in weights_dict.items():
        if name in layers:
            suitability += layers[name] * weight

    # Apply constraints
    constraint_path = paths["constraint_mask"]
    if request.apply_constraints and constraint_path.exists():
        with rasterio.open(constraint_path) as src:
            mask_aligned = np.zeros(suitability.shape, dtype=np.uint8)
            reproject(
                source=rasterio.band(src, 1),
                destination=mask_aligned,
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=profile["transform"], dst_crs=profile["crs"],
                resampling=Resampling.nearest,
            )
            suitability = suitability * mask_aligned.astype(np.float32)

    suitability = np.clip(suitability, 0, 100)
    valid = suitability[suitability > 0]
    if valid.size == 0:
        raise HTTPException(status_code=500, detail="No valid pixels after constraints.")

    stats = {
        "min":    float(valid.min()),
        "max":    float(valid.max()),
        "mean":   float(valid.mean()),
        "std":    float(valid.std()),
        "median": float(np.median(valid)),
    }

    boundary_pixels = int((suitability > 0).sum())
    protected_pixels = 0
    if request.apply_constraints and constraint_path.exists():
        with rasterio.open(constraint_path) as src:
            cmask = src.read(1)
        inside_boundary = int((cmask > 0).sum())
        protected_pixels = max(0, inside_boundary - boundary_pixels)
    else:
        inside_boundary = boundary_pixels
    total_pixels = max(inside_boundary, 1)

    classification = {
        "highly_suitable_pct":     float((suitability >= 70).sum()                          / total_pixels * 100),
        "moderately_suitable_pct": float(((suitability >= 50) & (suitability < 70)).sum()  / total_pixels * 100),
        "marginally_suitable_pct": float(((suitability >= 30) & (suitability < 50)).sum()  / total_pixels * 100),
        "not_suitable_pct":        float(((suitability > 0)  & (suitability < 30)).sum()   / total_pixels * 100),
        "excluded_pct":            float(protected_pixels / total_pixels * 100),
    }

    # Save GeoTIFF
    paths["api_results_dir"].mkdir(parents=True, exist_ok=True)
    analysis_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    tif_path    = paths["api_results_dir"] / f"suitability_{analysis_id}.tif"
    out_profile = profile.copy()
    out_profile.update(dtype=rasterio.float32, compress="lzw", nodata=0)
    with rasterio.open(tif_path, "w", **out_profile) as dst:
        dst.write(suitability, 1)

    # Render assets
    try:
        from map_renderer import render_all
        rendered = render_all(
            analysis_id=analysis_id,
            classification=classification,
            weights=weights_dict,
            config=cfg,
            paths=paths,
        )
    except Exception as e:
        logger.warning(f"render_all failed (non-fatal): {e}")
        rendered = {}

    metadata = {
        "analysis_id":         analysis_id,
        "county":              c,
        "raster_bounds":       raster_bounds,
        "weights":             weights_dict,
        "statistics":          stats,
        "classification":      classification,
        "constraints_applied": request.apply_constraints,
        "timestamp":           datetime.now().isoformat(),
        "rendered_assets":     {k: str(v) for k, v in rendered.items() if v},
    }
    with open(paths["api_results_dir"] / f"metadata_{analysis_id}.json", "w") as f:
        json.dump(metadata, f, indent=2)

    return SuitabilityResponse(
        analysis_id=analysis_id,
        county=cfg["display_name"],
        raster_bounds=raster_bounds,
        suitability_range={"min": stats["min"], "max": stats["max"]},
        statistics=stats,
        classification=classification,
        weights_used=weights_dict,
        timestamp=datetime.now().isoformat(),
    )


@app.get("/map-image/{analysis_id}")
async def get_map_image(analysis_id: str, county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    c   = _require_county(county)
    cr  = _require_crop(crop)
    cfg = load_config(c, cr)
    tif_path = cfg["_paths"]["api_results_dir"] / f"suitability_{analysis_id}.tif"
    if not tif_path.exists():
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")

    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)

    h, w  = data.shape
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

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")



# ── Criterion colormaps (matches map_renderer.py) ─────────────────────────────
_CRITERION_CMAPS = {
    'elevation':   'terrain',
    'rainfall':    'YlGnBu',
    'temperature': 'RdYlBu_r',
    'soil':        'YlOrBr',
    'slope':       'copper_r',
}

@app.get("/layer-image/{county}/{layer_name}")
async def get_layer_image(county: str, layer_name: str):
    """
    Render a single normalized criterion layer as a georeferenced RGBA PNG.
    Uses the same bounds as /map-image so Leaflet ImageOverlay works identically.
    Layers are read from the in-memory county cache — no disk I/O, ~50ms response.

    layer_name: elevation | rainfall | temperature | soil | slope
    """
    c = county.strip().lower()
    cache = COUNTY_CACHE.get(c)
    if not cache:
        st = COUNTY_STATUS.get(c, {})
        raise HTTPException(
            status_code=503 if st.get("status") in ("fetching","pipeline") else 404,
            detail=f"County '{c}' not loaded. Status: {st.get('status','idle')}"
        )

    layers = cache["layers"]
    if layer_name not in layers:
        raise HTTPException(
            status_code=404,
            detail=f"Layer '{layer_name}' not found. Available: {list(layers.keys())}"
        )

    data  = layers[layer_name]          # numpy float32, 0-100, 0=nodata
    valid = data > 0

    if not valid.any():
        raise HTTPException(status_code=404, detail=f"No valid pixels for '{layer_name}'")

    # Apply matplotlib colormap
    import matplotlib.pyplot as plt
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors

    cmap_name = _CRITERION_CMAPS.get(layer_name, 'viridis')
    cmap      = plt.get_cmap(cmap_name)

    # Normalise 0-100 → 0-1 for colormap, but only over valid pixels
    normed        = np.zeros_like(data, dtype=np.float32)
    normed[valid] = data[valid] / 100.0

    rgba_float = cmap(normed)                            # H×W×4, float 0-1
    rgba       = (rgba_float * 255).astype(np.uint8)

    # Transparent outside county, semi-transparent inside
    rgba[~valid, 3] = 0
    rgba[valid,  3] = 210                                # slight transparency

    img = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")


@app.get("/layer-meta")
async def get_layer_meta():
    """
    Returns display metadata for each criterion layer —
    used by the frontend to build the layer toggle legend.
    """
    return {
        "elevation":   {"label": "Elevation",   "unit": "m",     "low": "Low",    "high": "High",  "colormap": "terrain"},
        "rainfall":    {"label": "Rainfall",    "unit": "mm/yr", "low": "Dry",    "high": "Wet",   "colormap": "YlGnBu"},
        "temperature": {"label": "Temperature", "unit": "°C",    "low": "Cool",   "high": "Hot",   "colormap": "RdYlBu_r"},
        "soil":        {"label": "Soil Clay",   "unit": "g/kg",  "low": "Sandy",  "high": "Clay",  "colormap": "YlOrBr"},
        "slope":       {"label": "Slope",       "unit": "°",     "low": "Flat",   "high": "Steep", "colormap": "copper_r"},
    }

@app.post("/report/{analysis_id}")
async def generate_report(analysis_id: str, depth: str = "full", county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    if depth not in ("summary", "full"):
        raise HTTPException(status_code=400, detail="depth must be 'summary' or 'full'")
    c   = _require_county(county)
    cr  = _require_crop(crop)
    cfg = load_config(c, cr)
    meta_path = cfg["_paths"]["api_results_dir"] / f"metadata_{analysis_id}.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")
    with open(meta_path) as f:
        metadata = json.load(f)

    rendered = {k: Path(v) for k, v in metadata.get("rendered_assets", {}).items()}
    if not rendered:
        from map_renderer import render_all
        rendered = render_all(
            analysis_id=analysis_id,
            classification=metadata["classification"],
            weights=metadata["weights"],
            config=cfg,
            paths=cfg["_paths"],
        )

    from report_writer import build_report
    pdf_path = build_report(
        analysis_id=analysis_id,
        metadata=metadata,
        rendered=rendered,
        config=cfg,
        paths=cfg["_paths"],
        depth=depth,
    )
    return FileResponse(path=str(pdf_path), media_type="application/pdf",
                        filename=f"{c}_suitability_{analysis_id}_{depth}.pdf")


@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str, county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    c   = _require_county(county)
    cr  = _require_crop(crop)
    cfg = load_config(c, cr)
    path = cfg["_paths"]["api_results_dir"] / f"metadata_{analysis_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    with open(path) as f:
        return json.load(f)


@app.get("/download/{analysis_id}")
async def download_geotiff(analysis_id: str, county: Optional[str] = Query(None), crop: Optional[str] = Query(None)):
    c   = _require_county(county)
    cfg = load_config(c)
    tif = cfg["_paths"]["api_results_dir"] / f"suitability_{analysis_id}.tif"
    if not tif.exists():
        raise HTTPException(status_code=404, detail="Analysis not found")
    return FileResponse(path=str(tif), media_type="image/tiff",
                        filename=f"{c}_suitability_{analysis_id}.tif")


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — admin / county management
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/admin/load-county")
async def load_county(background_tasks: BackgroundTasks, county: str = Query(...)):
    """
    Trigger fetch + pipeline + load for a county.
    Returns immediately; poll GET /status/{county} for progress.
    If already loaded, reloads from disk (fast).
    """
    c = county.strip().lower()
    if c not in list_counties():
        raise HTTPException(status_code=404,
            detail=f"No config for '{c}'. Available: {list_counties()}")

    current = COUNTY_STATUS.get(c, {}).get("status")
    if current in ("fetching", "pipeline"):
        return {"message": f"Already in progress for '{c}'", "status": current}

    # If already cached on disk, just reload into memory (fast path)
    try:
        cfg = load_config(c)
        has_layers = any(p.exists() for p in cfg["_paths"]["normalized_layers"].values())
    except Exception:
        has_layers = False

    if has_layers:
        _set_status(c, "pipeline", "Loading cached layers into memory", pct=90)
        load_county_layers(c)
        return {"message": f"Loaded '{c}' from local cache", "status": "ready"}

    # Try R2 before falling back to full PC fetch
    if os.environ.get("R2_ACCOUNT_ID") and _r2_has_county(c):
        _set_status(c, "fetching", "Downloading from R2 storage", pct=10)
        ok = sync_county_from_r2(c)
        if ok:
            load_county_layers(c)
            return {"message": f"Loaded '{c}' from R2", "status": "ready"}
        logger.warning(f"[{c}] R2 pull incomplete — falling back to full PC fetch")

    # Full fetch + pipeline in background
    _set_status(c, "fetching", "Queued for fetch", pct=1)
    background_tasks.add_task(_fetch_and_prepare_county, c)
    return {
        "message": f"Fetch started for '{c}'. Poll GET /status/{c}",
        "status":  "fetching",
    }


@app.post("/admin/reload")
async def reload_all(background_tasks: BackgroundTasks):
    """Reload all cached counties from disk (no re-fetch)."""
    reloaded = []
    for c in list_counties():
        try:
            cfg = load_config(c)
            if any(p.exists() for p in cfg["_paths"]["normalized_layers"].values()):
                load_county_layers(c)
                reloaded.append(c)
        except Exception as e:
            logger.warning(f"[{c}] reload failed: {e}")
    return {"reloaded": reloaded, "total": len(reloaded)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)