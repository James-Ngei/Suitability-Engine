"""
config.py
---------
Loads the active county configuration from config/active_county.txt.
All pipeline scripts import from here — no hardcoded county names anywhere else.

Key distinction:
  CONFIG_DIR  — the config/ folder in the repo (JSON files + active_county.txt)
                Always resolved relative to THIS file, so it works both locally
                and on Render where the repo is checked out to /opt/render/project/src.

  BASE_DIR    — where runtime DATA lives (rasters, results, etc.)
                Locally:   ~/suitability-engine
                On Render: /tmp/suitability-engine  (set SUITABILITY_DATA_DIR)

Environment variables:
    SUITABILITY_DATA_DIR   — override runtime data directory
    ACTIVE_COUNTY          — override active_county.txt (required on Render)
    AWS_S3_BUCKET          — S3 bucket name
    AWS_ACCESS_KEY_ID      — set in Render dashboard (secret)
    AWS_SECRET_ACCESS_KEY  — set in Render dashboard (secret)
    AWS_DEFAULT_REGION     — e.g. eu-north-1
"""

import os
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

_env_data_dir = os.environ.get("SUITABILITY_DATA_DIR")
BASE_DIR      = Path(_env_data_dir) if _env_data_dir else Path.home() / "suitability-engine"

SHARED_DIR  = BASE_DIR / "data" / "shared"


def get_active_county() -> str:
    """
    Read which county is currently active.
    Priority: ACTIVE_COUNTY env var > active_county.txt in repo config dir.
    """
    env_county = os.environ.get("ACTIVE_COUNTY")
    if env_county:
        return env_county.strip().lower()

    active_file = CONFIG_DIR / "active_county.txt"
    if not active_file.exists():
        raise FileNotFoundError(
            f"No active county set.\n"
            f"Either set the ACTIVE_COUNTY environment variable, or create:\n"
            f"  {active_file}\n"
            f"with a county name, e.g.:  echo 'kitui' > {active_file}"
        )
    county = active_file.read_text().strip().lower()
    if not county:
        raise ValueError(f"{active_file} is empty — write a county name into it.")
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
        available = [p.stem for p in CONFIG_DIR.glob("*.json")]
        raise FileNotFoundError(
            f"No config found for '{county}'.\n"
            f"Expected: {config_path}\n"
            f"Available: {available}"
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
    print(f"CONFIG_DIR    : {CONFIG_DIR}")
    print(f"BASE_DIR      : {BASE_DIR}")