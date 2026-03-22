"""
clip_to_boundary.py
--------------------
Clips all normalized rasters to the county polygon boundary and regenerates
the constraints mask. Reads all paths from the active county config.

The constraints mask combines:
  1. County boundary          — pixels outside the county are excluded
  2. Protected areas (national) — pixels inside protected areas are excluded

Run AFTER normalize.py:
    python src/clip_to_boundary.py
    Then restart the API.
"""

import sys
import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.features import rasterize
import geopandas as gpd
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs

# Import the shared mask builder from preprocess so logic lives in one place
from preprocess import build_constraints_mask


def clip_raster(input_path: Path, output_path: Path, shapes, nodata=0):
    """Clip raster to polygon shapes. Pixels outside become nodata."""
    with rasterio.open(input_path) as src:
        out_image, out_transform = rio_mask(
            src, shapes,
            crop=True, filled=True, nodata=nodata, all_touched=False,
        )
        profile = src.profile.copy()
        profile.update(
            height=out_image.shape[1],
            width=out_image.shape[2],
            transform=out_transform,
            nodata=nodata,
            compress='lzw',
        )
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(out_image)
    return output_path


def main():
    config = load_config()
    paths  = config['_paths']

    print("=" * 55)
    print(f"  CLIP TO BOUNDARY: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    create_county_dirs(config)

    # --- Load boundary ---
    boundary_path = paths['boundary']
    if not boundary_path.exists():
        print(f"❌ Boundary not found: {boundary_path}")
        return

    gdf = gpd.read_file(boundary_path)
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')
    shapes = list(gdf.geometry)

    # --- Clip each normalized layer in-place ---
    print("── Clipping normalized layers ───────────────────────────")
    clipped_reference = None

    for name, path in paths['normalized_layers'].items():
        if not path.exists():
            print(f"  ⚠️  Missing: {path.name} — skipping")
            continue
        clip_raster(path, path, shapes, nodata=0)
        print(f"  ✅ {name}")
        if clipped_reference is None:
            clipped_reference = path

    print()

    if clipped_reference is None:
        print("❌ No clipped layers found — nothing to build mask from.")
        return

    # --- Regenerate constraints mask (boundary + protected areas) ---
    print("── Regenerating constraints mask ────────────────────────")
    build_constraints_mask(
        boundary_gdf=gdf,
        reference_path=clipped_reference,
        output_path=paths['constraint_mask'],
        protected_path=paths['protected_areas'],
    )
    print()

    # --- Verify zero pixel counts are consistent across layers ---
    print("── Verification ─────────────────────────────────────────")
    for name, path in paths['normalized_layers'].items():
        if not path.exists():
            continue
        with rasterio.open(path) as src:
            data = src.read(1)
        zeros = int((data == 0).sum())
        total = data.size
        print(f"  {name}: {zeros:,}/{total:,} zero px ({zeros/total*100:.1f}%)")

    print()
    print("=" * 55)
    print("  DONE — restart the API:")
    print("  python src/api.py")
    print("=" * 55)


if __name__ == '__main__':
    main()