"""
normalize.py
------------
Converts aligned rasters to 0-100 suitability scores using fuzzy
membership functions. Thresholds are read from the active county config —
no hardcoded values here.

Usage:
    python src/normalize.py
"""

import sys
import numpy as np
import rasterio
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs


# ── Fuzzy functions ────────────────────────────────────────────────────────────

def trapezoidal(value: np.ndarray, a, b, c, d) -> np.ndarray:
    """0 before a | rise a→b | plateau b→c | fall c→d | 0 after d"""
    score = np.zeros_like(value, dtype=float)
    score[(value >= a) & (value < b)]  = (value[(value >= a) & (value < b)]  - a) / (b - a) * 100
    score[(value >= b) & (value <= c)] = 100
    score[(value > c)  & (value <= d)] = (d - value[(value > c) & (value <= d)]) / (d - c) * 100
    return np.clip(score, 0, 100)


def gaussian(value: np.ndarray, optimal, spread) -> np.ndarray:
    """100 at optimal, bell-curve decline both sides."""
    return np.clip(100 * np.exp(-((value - optimal) ** 2) / (2 * spread ** 2)), 0, 100)


def linear_descending(value: np.ndarray, min_val, max_val) -> np.ndarray:
    """100 at min_val, 0 at max_val."""
    return np.clip(((max_val - value) / (max_val - min_val)) * 100, 0, 100)


FUZZY_FUNCTIONS = {
    'trapezoidal':      trapezoidal,
    'gaussian':         gaussian,
    'linear_descending': linear_descending,
}


# ── Normalize a single layer ───────────────────────────────────────────────────

def normalize_layer(input_path: Path, output_path: Path,
                    norm_config: dict, name: str) -> Path:
    """
    Apply fuzzy function to a raster and save 0-100 score raster.
    Nodata pixels (value == nodata) are preserved as 0.
    """
    fn_name = norm_config['type']
    params  = norm_config['params']
    fn      = FUZZY_FUNCTIONS[fn_name]

    with rasterio.open(input_path) as src:
        raw     = src.read(1).astype(float)
        profile = src.profile.copy()
        nodata  = src.nodata

    # Mask nodata before scoring
    if nodata is not None:
        valid_mask = raw != nodata
    else:
        valid_mask = np.ones_like(raw, dtype=bool)

    score = np.zeros_like(raw, dtype=np.float32)
    score[valid_mask] = fn(raw[valid_mask], **params).astype(np.float32)

    # Special case: hard zero above slope max
    if fn_name == 'linear_descending' and 'max_val' in params:
        score[raw > params['max_val']] = 0

    profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(score, 1)

    return output_path


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    paths  = config['_paths']

    print("=" * 55)
    print(f"  NORMALIZING: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    create_county_dirs(config)

    # Check aligned inputs exist
    print("── Checking aligned inputs ──────────────────────────────")
    all_ok = True
    for name, path in paths['aligned_layers'].items():
        exists = path.exists()
        print(f"  {'✅' if exists else '❌'} {name}: {path.name}")
        if not exists:
            all_ok = False
    print()

    if not all_ok:
        print("⚠️  Missing aligned layers — run realign_to_boundary.py first")
        return

    # Normalize each layer
    print("── Normalizing layers ───────────────────────────────────")
    for name, aligned_path in paths['aligned_layers'].items():
        norm_cfg    = config['normalization'][name]
        output_path = paths['normalized_layers'][name]

        print(f"  {name} ({norm_cfg['type']}): {norm_cfg['description']}")
        normalize_layer(aligned_path, output_path, norm_cfg, name)
        print(f"    ✅ Saved: {output_path.name}")
    print()

    # Sanity check
    print("── Output sanity check ──────────────────────────────────")
    all_good = True
    for name, path in paths['normalized_layers'].items():
        with rasterio.open(path) as src:
            data  = src.read(1)
        valid = data[data > 0]
        if valid.size == 0:
            print(f"  ⚠️  {name}: NO VALID PIXELS — check thresholds in config")
            all_good = False
        else:
            print(f"  {name}: {valid.size}/{data.size} px | "
                  f"range {valid.min():.1f}-{valid.max():.1f} | "
                  f"mean {valid.mean():.1f}")

    print()
    if all_good:
        print("✅ All layers normalized successfully.")
        print("   Next: python src/clip_to_boundary.py")
    else:
        print("⚠️  Some layers have issues — review thresholds in "
              f"config/{config['county']}.json")


if __name__ == '__main__':
    main()