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


def _threshold_hint(fn_name: str, params: dict, data_min: float, data_max: float) -> str:
    """
    Return a plain-English explanation of why a layer has no valid pixels,
    comparing the actual data range to the configured thresholds.
    """
    if fn_name == 'trapezoidal':
        a, d = params.get('a', '?'), params.get('d', '?')
        return (
            f"Data range {data_min:.1f}–{data_max:.1f} does not overlap "
            f"threshold window {a}–{d}. "
            f"{'Data is entirely ABOVE the upper threshold d=' + str(d) + '.' if isinstance(d, (int,float)) and data_min > d else ''}"
            f"{'Data is entirely BELOW the lower threshold a=' + str(a) + '.' if isinstance(a, (int,float)) and data_max < a else ''}"
        )
    elif fn_name == 'gaussian':
        opt    = params.get('optimal', '?')
        spread = params.get('spread', '?')
        return (
            f"Data range {data_min:.1f}–{data_max:.1f}. "
            f"Gaussian optimal={opt}, spread={spread}. "
            f"All values may be too far from the optimum to score above 0."
        )
    elif fn_name == 'linear_descending':
        max_val = params.get('max_val', '?')
        return (
            f"Data range {data_min:.1f}–{data_max:.1f}. "
            f"linear_descending max_val={max_val}. "
            f"{'All data is above max_val — everything hard-zeroed.' if isinstance(max_val, (int,float)) and data_min > max_val else ''}"
        )
    return f"Data range {data_min:.1f}–{data_max:.1f}, params={params}"


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

    # Sanity check — now with diagnostic hints on failure
    print("── Output sanity check ──────────────────────────────────")
    all_good = True
    for name, path in paths['normalized_layers'].items():
        norm_cfg = config['normalization'][name]

        with rasterio.open(path) as src:
            data = src.read(1)

        # Read the raw aligned data for range reporting
        aligned_path = paths['aligned_layers'][name]
        with rasterio.open(aligned_path) as src:
            raw      = src.read(1).astype(float)
            nd       = src.nodata
            raw_mask = (raw != nd) & np.isfinite(raw) if nd is not None else np.isfinite(raw)
            raw_valid = raw[raw_mask]

        valid = data[data > 0]

        if valid.size == 0:
            hint = _threshold_hint(
                norm_cfg['type'],
                norm_cfg['params'],
                float(raw_valid.min()) if raw_valid.size else float('nan'),
                float(raw_valid.max()) if raw_valid.size else float('nan'),
            )
            print(f"  ⚠️  {name}: NO VALID PIXELS")
            print(f"       Raw data range : {raw_valid.min():.1f}–{raw_valid.max():.1f}" if raw_valid.size else "       Raw data: no valid pixels either")
            print(f"       Threshold type : {norm_cfg['type']}, params={norm_cfg['params']}")
            print(f"       Diagnosis      : {hint.strip()}")
            print(f"       Fix            : Update config/crops/<crop>.json normalization params so")
            print(f"                        the threshold window overlaps the actual data range above.")
            all_good = False
        elif valid.min() == valid.max():
            # Uniform output — likely a constant-fill input (e.g. ISRIC fallback)
            print(f"  ⚠️  {name}: UNIFORM OUTPUT — {valid.mean():.1f}/100 everywhere")
            print(f"       Raw data range : {raw_valid.min():.1f}–{raw_valid.max():.1f}" if raw_valid.size else "")
            if raw_valid.size and raw_valid.min() == raw_valid.max():
                print(f"       Cause          : Raw input is constant ({raw_valid.mean():.1f}) — likely a single-point soil fill.")
                print(f"       Fix            : Re-fetch soil with: ACTIVE_COUNTY={config['county']} python src/pc_fetcher.py --fetch")
            else:
                print(f"       Cause          : All valid pixels land on the plateau of the fuzzy function.")
        else:
            print(f"  {name}: {valid.size}/{data.size} px | "
                  f"range {valid.min():.1f}–{valid.max():.1f} | "
                  f"mean {valid.mean():.1f}")

    print()
    if all_good:
        print("✅ All layers normalized successfully.")
        print("   Next: python src/clip_to_boundary.py")
    else:
        print("⚠️  Some layers have issues — see diagnosis above.")
        print(f"   Crop config : config/crops/{config.get('crop_id', 'cotton')}.json")
        print(f"   County      : {config['county']}")


if __name__ == '__main__':
    main()