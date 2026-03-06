"""
realign_to_boundary.py
-----------------------
Reprojects all aligned rasters so their pixel grid snaps exactly
to the Bungoma boundary extent. This fixes the visual offset between
the heatmap and the boundary outline on the map.

Run ONCE, then re-run normalize.py:
    python src/realign_to_boundary.py
    python src/normalize.py
"""

import math
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
import geopandas as gpd
from pathlib import Path


# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR      = Path.home() / 'suitability-engine'
BOUNDARY_PATH = BASE_DIR / 'data' / 'boundaries' / 'bungoma_boundary.gpkg'
PROCESSED_DIR = BASE_DIR / 'data' / 'processed'

LAYERS = {
    'elevation':   'aligned_elevation.tif',
    'rainfall':    'aligned_rainfall.tif',
    'temperature': 'aligned_temperature.tif',
    'soil':        'aligned_soil.tif',
    'slope':       'aligned_slope.tif',
}

RESOLUTION = 0.01  # degrees (~1 km)


def main():
    print("=" * 55)
    print("  REALIGN RASTERS TO BOUNDARY EXTENT")
    print("=" * 55)
    print()

    # 1. Load boundary and derive snapped target grid
    gdf = gpd.read_file(BOUNDARY_PATH)
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')

    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    print(f"Boundary extent:")
    print(f"  west={bounds[0]:.6f}, south={bounds[1]:.6f}")
    print(f"  east={bounds[2]:.6f}, north={bounds[3]:.6f}")
    print()

    # Snap to resolution grid
    west  = math.floor(bounds[0] / RESOLUTION) * RESOLUTION
    south = math.floor(bounds[1] / RESOLUTION) * RESOLUTION
    east  = math.ceil(bounds[2]  / RESOLUTION) * RESOLUTION
    north = math.ceil(bounds[3]  / RESOLUTION) * RESOLUTION

    width  = round((east  - west)  / RESOLUTION)
    height = round((north - south) / RESOLUTION)

    target_transform = from_bounds(west, south, east, north, width, height)

    print(f"Target grid (snapped to {RESOLUTION}° resolution):")
    print(f"  west={west:.6f}, south={south:.6f}")
    print(f"  east={east:.6f}, north={north:.6f}")
    print(f"  size={width} x {height} pixels")
    print()

    # 2. Reproject each layer onto the target grid
    print("── Realigning layers ───────────────────────────────────")
    for name, fname in LAYERS.items():
        src_path = PROCESSED_DIR / fname

        if not src_path.exists():
            print(f"  ⚠️  Missing: {fname} — skipping")
            continue

        with rasterio.open(src_path) as src:
            print(f"  {name}:")
            print(f"    before: shape={src.shape}, "
                  f"west={src.bounds.left:.6f}, south={src.bounds.bottom:.6f}")

            destination = np.zeros((height, width), dtype=np.float32)

            reproject(
                source=rasterio.band(src, 1),
                destination=destination,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs='EPSG:4326',
                resampling=Resampling.bilinear
            )

            profile = src.profile.copy()
            profile.update(
                height=height,
                width=width,
                transform=target_transform,
                crs='EPSG:4326',
                dtype=rasterio.float32,
                compress='lzw',
                nodata=0
            )

        # Overwrite the aligned file in place
        with rasterio.open(src_path, 'w', **profile) as dst:
            dst.write(destination, 1)

        print(f"    after : shape=({height},{width}), "
              f"west={west:.6f}, south={south:.6f}")
        print(f"    ✅ Saved")

    print()
    print("=" * 55)
    print("  DONE")
    print("  Next steps:")
    print("  1. python src/normalize.py")
    print("  2. python src/clip_to_boundary.py")
    print("  3. Restart API: python src/api.py")
    print("=" * 55)


if __name__ == '__main__':
    main()