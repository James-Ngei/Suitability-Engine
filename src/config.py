"""
config.py
---------
Loads the active county configuration from config/active_county.txt.
All pipeline scripts import from here — no hardcoded county names anywhere else.

To switch counties:
    echo "kitui" > config/active_county.txt
    python src/normalize.py
    python src/api.py

Shared data (used by all counties) lives under data/shared/:
    data/shared/protected_areas_kenya.gpkg   ← download once from protectedplanet.net
"""

import json
from pathlib import Path

BASE_DIR    = Path.home() / 'suitability-engine'
CONFIG_DIR  = BASE_DIR / 'config'
ACTIVE_FILE = CONFIG_DIR / 'active_county.txt'
SHARED_DIR  = BASE_DIR / 'data' / 'shared'


def get_active_county() -> str:
    """Read which county is currently active."""
    if not ACTIVE_FILE.exists():
        raise FileNotFoundError(
            f"No active county set.\n"
            f"Create {ACTIVE_FILE} with a county name, e.g.:\n"
            f"  echo 'kitui' > {ACTIVE_FILE}"
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

    config_path = CONFIG_DIR / f'{county}.json'
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config found for '{county}'.\n"
            f"Expected: {config_path}\n"
            f"Available: {[p.stem for p in CONFIG_DIR.glob('*.json')]}"
        )

    with open(config_path) as f:
        config = json.load(f)

    config['_paths'] = _resolve_paths(config)
    return config


def _resolve_paths(config: dict) -> dict:
    """Build all filesystem paths from config."""
    county     = config['county']
    county_dir = BASE_DIR / 'data' / 'counties' / county

    return {
        # County-specific dirs
        'county_dir':       county_dir,
        'boundary':         county_dir / 'boundaries' / f'{county}_boundary.gpkg',
        'raw_dir':          county_dir / 'raw',
        'preprocessed_dir': county_dir / 'preprocessed',
        'processed_dir':    county_dir / 'processed',
        'normalized_dir':   county_dir / 'normalized',
        'results_dir':      county_dir / 'results',
        'api_results_dir':  county_dir / 'api_results',
        'sensitivity_dir':  county_dir / 'sensitivity',
        'constraint_mask':  county_dir / 'preprocessed' / f'{county}_constraints_mask.tif',

        # Shared across all counties (download once)
        'shared_dir':      SHARED_DIR,
        'protected_areas': SHARED_DIR / 'protected_areas_kenya.gpkg',

        # Layer paths derived from config['layers']
        'layers': {
            name: county_dir / 'preprocessed' / fname
            for name, fname in config['layers'].items()
        },
        'aligned_layers': {
            name: county_dir / 'processed' / f'aligned_{name}.tif'
            for name in config['layers']
        },
        'normalized_layers': {
            name: county_dir / 'normalized' / f'normalized_{name}.tif'
            for name in config['layers']
        },
    }


def create_county_dirs(config: dict):
    """Create all required directories for a county (including shared/)."""
    paths = config['_paths']
    dirs = [
        paths['raw_dir'],
        paths['preprocessed_dir'],
        paths['processed_dir'],
        paths['normalized_dir'],
        paths['results_dir'],
        paths['api_results_dir'],
        paths['sensitivity_dir'],
        paths['boundary'].parent,
        paths['shared_dir'],
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    print(f"✅ Directories ready for: {config['display_name']}")


def list_counties() -> list:
    """Return all available county configs."""
    return [p.stem for p in CONFIG_DIR.glob('*.json')]


if __name__ == '__main__':
    print(f"Active county: {get_active_county()}")
    config = load_config()
    print(f"Display name : {config['display_name']}")
    print(f"Crop         : {config['crop']}")
    print(f"Resolution   : {config['resolution']}°")
    print(f"Layers       : {list(config['layers'].keys())}")
    print(f"Available    : {list_counties()}")
    print(f"Protected    : {config['_paths']['protected_areas']}")