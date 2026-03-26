"""
config.py
---------
Loads the active county configuration from config/active_county.txt.
All pipeline scripts import from here — no hardcoded county names anywhere else.

On Render (or any deployed environment), set:
    SUITABILITY_DATA_DIR=/tmp/suitability-engine
    ACTIVE_COUNTY=bungoma          ← overrides active_county.txt
    AWS_S3_BUCKET=suitability-engine
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...
    AWS_DEFAULT_REGION=eu-north-1
"""

import os
import json
from pathlib import Path

# ── Base directory ─────────────────────────────────────────────────────────────
# Locally:  ~/suitability-engine (default)
# On Render: /tmp/suitability-engine  (set SUITABILITY_DATA_DIR env var)
_env_data_dir = os.environ.get("SUITABILITY_DATA_DIR")
BASE_DIR    = Path(_env_data_dir) if _env_data_dir else Path.home() / "suitability-engine"
CONFIG_DIR  = BASE_DIR / "config"
ACTIVE_FILE = CONFIG_DIR / "active_county.txt"
SHARED_DIR  = BASE_DIR / "data" / "shared"


def get_active_county() -> str:
    """Read which county is currently active.
    Env var ACTIVE_COUNTY takes priority over active_county.txt."""
    env_county = os.environ.get("ACTIVE_COUNTY")
    if env_county:
        return env_county.strip().lower()

    if not ACTIVE_FILE.exists():
        raise FileNotFoundError(
            f"No active county set.\n"
            f"Create {ACTIVE_FILE} with a county name, e.g.:\n"
            f"  echo 'kitui' > {ACTIVE_FILE}\n"
            f"Or set the ACTIVE_COUNTY environment variable."
        )
    county = ACTIVE_FILE.read_text().strip().lower()
    if not county:
        raise ValueError(f"{ACTIVE_FILE} is empty — write a county name into it.")
    return county


def load_config(county: str = None) -> dict:
    """
    Load config for a county. Uses active county if none specified.
    Returns the full config dict with resolved paths attached as '_paths'.
    """
    if county is None:
        county = get_active_county()

    config_path = CONFIG_DIR / f"{county}.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config found for '{county}'.\n"
            f"Expected: {config_path}\n"
            f"Available: {[p.stem for p in CONFIG_DIR.glob('*.json')]}"
        )

    with open(config_path) as f:
        config = json.load(f)

    config["_paths"] = _resolve_paths(config)
    return config


def _resolve_paths(config: dict) -> dict:
    """Build all filesystem paths from config."""
    county     = config["county"]
    county_dir = BASE_DIR / "data" / "counties" / county

    return {
        "county_dir":       county_dir,
        "boundary":         county_dir / "boundaries" / f"{county}_boundary.gpkg",
        "raw_dir":          county_dir / "raw",
        "preprocessed_dir": county_dir / "preprocessed",
        "processed_dir":    county_dir / "processed",
        "normalized_dir":   county_dir / "normalized",
        "results_dir":      county_dir / "results",
        "api_results_dir":  county_dir / "api_results",
        "sensitivity_dir":  county_dir / "sensitivity",
        "constraint_mask":  county_dir / "preprocessed" / f"{county}_constraints_mask.tif",

        "shared_dir":      SHARED_DIR,
        "protected_areas": SHARED_DIR / "protected_areas_kenya.gpkg",

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
    """Create all required directories for a county (including shared/)."""
    paths = config["_paths"]
    dirs = [
        paths["raw_dir"],
        paths["preprocessed_dir"],
        paths["processed_dir"],
        paths["normalized_dir"],
        paths["results_dir"],
        paths["api_results_dir"],
        paths["sensitivity_dir"],
        paths["boundary"].parent,
        paths["shared_dir"],
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    print(f"✅ Directories ready for: {config['display_name']}")


def list_counties() -> list:
    """Return all available county configs."""
    return [p.stem for p in CONFIG_DIR.glob("*.json")]


if __name__ == "__main__":
    print(f"Active county : {get_active_county()}")
    config = load_config()
    print(f"Display name  : {config['display_name']}")
    print(f"Crop          : {config['crop']}")
    print(f"Resolution    : {config['resolution']}°")
    print(f"Layers        : {list(config['layers'].keys())}")
    print(f"Available     : {list_counties()}")
    print(f"BASE_DIR      : {BASE_DIR}")