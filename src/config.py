"""
config.py
---------
Loads county config (geography) and crop config (agronomy) separately,
then merges them for the pipeline and API.

Directory structure:
  config/
    counties/        ← 47 county JSON files (geography only)
    crops/           ← 10+ crop JSON files (normalization + weights)
    active_county.txt
    active_crop.txt

Environment variables:
    ACTIVE_COUNTY          — override active_county.txt
    ACTIVE_CROP            — override active_crop.txt (default: cotton)
    SUITABILITY_DATA_DIR   — runtime data directory
"""

import os
import json
from pathlib import Path
from typing import Optional

# CONFIG_DIR: always relative to this file — works locally and on Render
CONFIG_DIR    = Path(__file__).resolve().parent.parent / "config"
COUNTIES_DIR  = CONFIG_DIR / "counties"
CROPS_DIR     = CONFIG_DIR / "crops"

_env_data_dir = os.environ.get("SUITABILITY_DATA_DIR")
BASE_DIR      = Path(_env_data_dir) if _env_data_dir else Path.home() / "suitability-engine"
SHARED_DIR    = BASE_DIR / "data" / "shared"


# ── Active county / crop ───────────────────────────────────────────────────────

def get_active_county() -> str:
    env = os.environ.get("ACTIVE_COUNTY")
    if env:
        return env.strip().lower()
    f = CONFIG_DIR / "active_county.txt"
    if f.exists():
        v = f.read_text().strip().lower()
        if v:
            return v
    return "kitui"


def get_active_crop() -> str:
    env = os.environ.get("ACTIVE_CROP")
    if env:
        return env.strip().lower()
    f = CONFIG_DIR / "active_crop.txt"
    if f.exists():
        v = f.read_text().strip().lower()
        if v:
            return v
    return "cotton"


# ── Listing ────────────────────────────────────────────────────────────────────

def list_counties() -> list:
    """All available county IDs (from config/counties/)."""
    if not COUNTIES_DIR.exists():
        # Fallback: old flat structure
        return [p.stem for p in CONFIG_DIR.glob("*.json")
                if p.stem not in ("active_county", "active_crop")]
    return sorted(p.stem for p in COUNTIES_DIR.glob("*.json"))


def list_crops() -> list:
    """All available crop IDs (from config/crops/)."""
    if not CROPS_DIR.exists():
        return ["cotton"]
    return sorted(p.stem for p in CROPS_DIR.glob("*.json"))


# ── Loaders ────────────────────────────────────────────────────────────────────

def load_county_config(county: str) -> dict:
    """Load county geography config (no agronomy)."""
    # Try new location first
    path = COUNTIES_DIR / f"{county}.json"
    if not path.exists():
        # Fallback: old flat structure
        path = CONFIG_DIR / f"{county}.json"
    if not path.exists():
        available = list_counties()
        raise FileNotFoundError(
            f"No county config for '{county}'. Available: {available}"
        )
    with open(path) as f:
        return json.load(f)


def load_crop_config(crop: str) -> dict:
    """Load crop agronomy config."""
    path = CROPS_DIR / f"{crop}.json"
    if not path.exists():
        available = list_crops()
        raise FileNotFoundError(
            f"No crop config for '{crop}'. Available: {available}"
        )
    with open(path) as f:
        return json.load(f)


def load_config(county: Optional[str] = None,
                crop:   Optional[str] = None) -> dict:
    """
    Load and merge county + crop configs.
    Returns a single config dict with _paths attached — same interface
    as before so all pipeline scripts work unchanged.
    """
    if county is None:
        county = get_active_county()
    if crop is None:
        crop = get_active_crop()

    county_cfg = load_county_config(county)
    crop_cfg   = load_crop_config(crop)

    # Merge: county geography + crop agronomy
    merged = {
        **county_cfg,
        # Crop fields (override anything in county config)
        "crop":          crop_cfg["display_name"],
        "crop_id":       crop_cfg["crop_id"],
        "normalization": crop_cfg["normalization"],
        "weights":       crop_cfg["weights"],
        "criteria_info": crop_cfg["criteria_info"],
        # Keep display_name from county
        "display_name":  county_cfg["display_name"],
    }

    merged["_paths"] = _resolve_paths(merged, crop)
    return merged


def _resolve_paths(config: dict, crop_id: str) -> dict:
    """
    Build all filesystem paths.
    Rasters are cached per-county (shared across crops).
    Results are per county+crop combination.
    """
    county     = config["county"]
    county_dir = BASE_DIR / "data" / "counties" / county

    return {
        "county_dir":       county_dir,
        "boundary":         county_dir / "boundaries" / f"{county}_boundary.gpkg",
        "raw_dir":          county_dir / "raw",
        "preprocessed_dir": county_dir / "preprocessed",
        "processed_dir":    county_dir / "processed",
        "normalized_dir":   county_dir / "normalized",
        # Results are per crop — same county rasters, different analysis
        "results_dir":      county_dir / "results" / crop_id,
        "api_results_dir":  county_dir / "api_results" / crop_id,
        "sensitivity_dir":  county_dir / "sensitivity" / crop_id,
        "constraint_mask":  county_dir / "preprocessed" / f"{county}_constraints_mask.tif",

        "shared_dir":       SHARED_DIR,
        "protected_areas":  SHARED_DIR / "protected_areas_kenya.gpkg",

        "layers": {
            name: county_dir / "preprocessed" / fname
            for name, fname in config["layers"].items()
        },
        "aligned_layers": {
            name: county_dir / "processed" / f"aligned_{name}.tif"
            for name in config["layers"]
        },
        "normalized_layers": {
            name: county_dir / "normalized" / f"normalized_{name}.tif"
            for name in config["layers"]
        },
    }


def create_county_dirs(config: dict):
    paths = config["_paths"]
    for d in [
        paths["raw_dir"], paths["preprocessed_dir"], paths["processed_dir"],
        paths["normalized_dir"], paths["results_dir"], paths["api_results_dir"],
        paths["sensitivity_dir"], paths["boundary"].parent, paths["shared_dir"],
    ]:
        d.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    print(f"Active county : {get_active_county()}")
    print(f"Active crop   : {get_active_crop()}")
    print(f"Counties      : {len(list_counties())} available")
    print(f"Crops         : {list_crops()}")
    config = load_config()
    print(f"Loaded        : {config['display_name']} × {config['crop']}")
    print(f"CONFIG_DIR    : {CONFIG_DIR}")
    print(f"BASE_DIR      : {BASE_DIR}")