"""
realign_to_boundary.py
-----------------------
Reprojects all aligned rasters so their pixel grid snaps exactly to the
county boundary extent. Reads paths and resolution from active county config.

Run ONCE after preprocessing, before normalize.py:
    python src/realign_to_boundary.py
    python src/normalize.py
    python src/clip_to_boundary.py
"""

import sys
import math
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
import geopandas as gpd
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs


def main():
    config = load_config()
    paths  = config['_paths']
    res    = config['resolution']

    print("=" * 55)
    print(f"  REALIGN TO BOUNDARY: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    create_county_dirs(config)

    # Load boundary
    boundary_path = paths['boundary']
    if not boundary_path.exists():
        print(f"❌ Boundary not found: {boundary_path}")
        return

    gdf = gpd.read_file(boundary_path)
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')

    bounds = gdf.total_bounds  # [minx, miny, maxx, maxy]
    print(f"Boundary: west={bounds[0]:.6f}, south={bounds[1]:.6f}, "
          f"east={bounds[2]:.6f}, north={bounds[3]:.6f}")

    # Snap to resolution grid
    west  = math.floor(bounds[0] / res) * res
    south = math.floor(bounds[1] / res) * res
    east  = math.ceil(bounds[2]  / res) * res
    north = math.ceil(bounds[3]  / res) * res

    width  = round((east  - west)  / res)
    height = round((north - south) / res)

    target_transform = from_bounds(west, south, east, north, width, height)

    print(f"Target grid ({res}° resolution): {width}x{height} pixels")
    print(f"  west={west:.6f}, south={south:.6f}, "
          f"east={east:.6f}, north={north:.6f}")
    print()

    # Reproject each layer
    print("── Realigning ───────────────────────────────────────────")
    for name, src_path in paths['aligned_layers'].items():
        if not src_path.exists():
            print(f"  ⚠️  Missing: {src_path.name} — skipping")
            continue

        with rasterio.open(src_path) as src:
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
                height=height, width=width,
                transform=target_transform,
                crs='EPSG:4326',
                dtype=rasterio.float32,
                compress='lzw', nodata=0
            )

        with rasterio.open(src_path, 'w', **profile) as dst:
            dst.write(destination, 1)

        print(f"  ✅ {name}: shape=({height},{width})")

    print()
    print("=" * 55)
    print("  DONE — next: python src/normalize.py")
    print("=" * 55)


if __name__ == '__main__':
    main()