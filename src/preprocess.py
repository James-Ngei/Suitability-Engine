"""
preprocess.py
-------------
Reprojects, resamples, clips all raw rasters to the county boundary and
builds the constraints mask. Reads all config from the active county config.

Handles raw files named either:
  - plain:           elevation.tif
  - county-prefixed: kitui_elevation.tif   ← auto-detected, no renaming needed

Run first in the pipeline:
    python src/preprocess.py
"""

import sys
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.mask import mask as rio_mask
from rasterio.features import rasterize
from rasterio.io import MemoryFile
import geopandas as gpd
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs

CATEGORICAL_LAYERS = {'soil', 'landcover'}


def find_raw_file(raw_dir: Path, layer_name: str, county: str) -> Path | None:
    """
    Find a raw file for a layer, accepting either:
      - plain name:          elevation.tif
      - county-prefixed:     kitui_elevation.tif
    Returns the Path if found, None if missing.
    """
    plain    = raw_dir / f'{layer_name}.tif'
    prefixed = raw_dir / f'{county}_{layer_name}.tif'

    if plain.exists():
        return plain
    if prefixed.exists():
        return prefixed

    # Fallback: scan for any .tif containing the layer name (skip Zone.Identifier)
    matches = [
        m for m in raw_dir.glob(f'*{layer_name}*.tif')
        if ':' not in str(m)
    ]
    if matches:
        return matches[0]

    return None


def _process_raster(src_path: Path, out_path: Path, shapes,
                    resampling=Resampling.bilinear):
    """Reproject to WGS84 and clip a single raster to the county boundary."""
    with rasterio.open(src_path) as src:
        print(f"  CRS: {src.crs}, Resolution: {src.res}, Shape: {src.shape}")

        data = np.zeros((src.height, src.width), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1),
            destination=data,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=src.transform,
            dst_crs='EPSG:4326',
            resampling=resampling,
        )

        kwargs = src.meta.copy()
        kwargs.update({
            'crs':      'EPSG:4326',
            'dtype':    'float32',
            'nodata':   0,
            'compress': 'lzw',
            'count':    1,
        })

        with MemoryFile() as memfile:
            with memfile.open(**kwargs) as tmp:
                tmp.write(data, 1)
                clipped, clip_transform = rio_mask(
                    tmp, shapes, crop=True, nodata=0
                )

        kwargs.update({
            'height':    clipped.shape[1],
            'width':     clipped.shape[2],
            'transform': clip_transform,
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, 'w', **kwargs) as dst:
        dst.write(clipped[0], 1)

    print(f"  ✅ Reprojection done.")
    print(f"  ✅ Clipped to boundary, shape: {clipped.shape[1:]}")
    print(f"  ✅ Saved: {out_path.name}")


def build_constraints_mask(boundary_gdf, reference_path: Path,
                            output_path: Path, protected_path: Path = None):
    """
    Build binary constraint mask:
      1 = inside county boundary AND not a protected area
      0 = outside county OR inside protected area
    """
    with rasterio.open(reference_path) as ref:
        transform = ref.transform
        crs       = ref.crs
        height    = ref.height
        width     = ref.width

    gdf    = boundary_gdf.to_crs(crs)
    shapes = [(geom, 1) for geom in gdf.geometry]

    # Layer 1: county boundary
    inside = rasterize(
        shapes, out_shape=(height, width),
        transform=transform, fill=0, dtype=np.uint8
    )
    print(f"    Boundary   : {inside.sum():,} / {inside.size:,} px inside county")

    # Layer 2: protected areas (optional)
    protected = np.zeros((height, width), dtype=np.uint8)
    if protected_path and protected_path.exists():
        print(f"    Loading    : {protected_path.name}")
        pa = gpd.read_file(protected_path, bbox=tuple(gdf.total_bounds))
        pa = pa.to_crs(crs)
        pa = pa[pa.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])]
        if len(pa) > 0:
            pa_shapes = [(geom, 1) for geom in pa.geometry]
            protected = rasterize(
                pa_shapes, out_shape=(height, width),
                transform=transform, fill=0, dtype=np.uint8
            )
            print(f"    Protected  : {protected.sum():,} px will be excluded")
        else:
            print(f"    Protected  : no polygon features in bbox — skipping")
    else:
        if protected_path:
            print(f"    ⚠️  Protected areas file not found: {protected_path.name}")
            print(f"       Constraint mask will use boundary only.")

    # Combine: allowed = inside boundary AND NOT protected
    mask_arr = ((inside == 1) & (protected == 0)).astype(np.uint8)
    excluded_protected = int(((inside == 1) & (protected == 1)).sum())
    print(f"    Combined   : {mask_arr.sum():,} px allowed, "
          f"{excluded_protected:,} px excluded (protected)")

    profile = {
        'driver': 'GTiff', 'height': height, 'width': width,
        'count': 1, 'dtype': 'uint8', 'crs': crs,
        'transform': transform, 'nodata': 0, 'compress': 'lzw'
    }
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(mask_arr, 1)

    print(f"    ✅ Saved   : {output_path.name}")
    return output_path


def main():
    config = load_config()
    paths  = config['_paths']
    county = config['county']

    print("=" * 55)
    print(f"  PREPROCESS: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    create_county_dirs(config)

    # Load boundary
    if not paths['boundary'].exists():
        print(f"❌ Boundary not found: {paths['boundary']}")
        return

    print(f"📌 Loading {config['display_name']} boundary...")
    boundary = gpd.read_file(paths['boundary'])
    if str(boundary.crs) != 'EPSG:4326':
        boundary = boundary.to_crs('EPSG:4326')
    print(f"✅ Boundary loaded — {len(boundary)} feature(s).")
    print()
    shapes = list(boundary.geometry)

    # Bootstrap reference raster (elevation must go first)
    reference_path = paths['layers']['elevation']
    if not reference_path.exists():
        elev_raw = find_raw_file(paths['raw_dir'], 'elevation', county)
        if elev_raw is None:
            print(f"❌ Reference raster not found: {reference_path}")
            print(f"   Drop elevation.tif (or {county}_elevation.tif) "
                  f"into {paths['raw_dir']} and re-run.")
            return

        print(f"ℹ️  Building reference raster from raw elevation first...")
        _process_raster(elev_raw, reference_path, shapes,
                        resampling=Resampling.bilinear)
        w = reference_path.stat().st_size // 1024
        print(f"✅ Reference raster: {reference_path.name} ({w} KB)")
        print()

    # Process all layers
    print("── Processing rasters ───────────────────────────────────")
    for layer_name in config['layers']:
        out_path = paths['layers'][layer_name]

        if out_path.exists():
            print(f"  ✓  Already exists: {out_path.name} — skipping")
            continue

        raw_path = find_raw_file(paths['raw_dir'], layer_name, county)
        if raw_path is None:
            print(f"  ⚠️  Raw file not found for '{layer_name}' — skipping")
            continue

        resamp = Resampling.nearest if layer_name in CATEGORICAL_LAYERS \
                 else Resampling.bilinear

        print(f"\n🔹 Processing: {raw_path.name} → {out_path.name}")
        _process_raster(raw_path, out_path, shapes, resampling=resamp)

    # Build constraints mask
    print()
    print("── Building constraints mask ────────────────────────────")
    build_constraints_mask(
        boundary_gdf=boundary,
        reference_path=reference_path,
        output_path=paths['constraint_mask'],
        protected_path=paths.get('protected_areas'),
    )

    print()
    print("=" * 55)
    print(f"  DONE")
    print(f"  Preprocessed : {paths['preprocessed_dir']}")
    print(f"  Mask         : {paths['constraint_mask'].name}")
    print()
    print("  Next steps:")
    print("    python src/realign_to_boundary.py")
    print("    python src/normalize.py")
    print("    python src/clip_to_boundary.py")
    print("    python src/api.py")
    print("=" * 55)


if __name__ == '__main__':
    main()