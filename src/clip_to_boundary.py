"""
clip_to_boundary.py
--------------------
Clips all normalized rasters AND the constraint mask to the exact
Bungoma County polygon shape, replacing the bounding-box versions.

Run ONCE after normalization, before (re)starting the API:
    python src/clip_to_boundary.py

Pipeline position:
    preprocess → align → normalize → [THIS SCRIPT] → API
"""

import numpy as np
import rasterio
from rasterio.mask import mask as rio_mask
from rasterio.features import rasterize
import geopandas as gpd
from pathlib import Path


# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR       = Path.home() / 'suitability-engine'
BOUNDARY_PATH  = BASE_DIR / 'data' / 'boundaries' / 'bungoma_boundary.gpkg'
NORMALIZED_DIR = BASE_DIR / 'data' / 'normalized'
CONSTRAINT_OUT = BASE_DIR / 'data' / 'preprocessed' / 'bungoma_constraints_mask.tif'

LAYERS = ['elevation', 'rainfall', 'temperature', 'soil', 'slope']


def load_boundary(boundary_path: Path, target_crs: str = 'EPSG:4326'):
    """Load and reproject boundary to match raster CRS."""
    gdf = gpd.read_file(boundary_path)
    if str(gdf.crs) != target_crs:
        gdf = gdf.to_crs(target_crs)
    print(f"✅ Boundary loaded: {len(gdf)} feature(s), CRS={gdf.crs}")
    return gdf


def clip_raster(input_path: Path, output_path: Path, shapes, nodata=0):
    """
    Clip a raster to polygon shapes using rasterio.mask.
    Pixels outside the polygon are set to nodata (transparent on map).
    """
    with rasterio.open(input_path) as src:
        # Reproject shapes to match raster CRS just in case
        out_image, out_transform = rio_mask(
            src,
            shapes,
            crop=True,       # Crop to polygon extent
            filled=True,     # Fill outside with nodata
            nodata=nodata,
            all_touched=False
        )

        out_profile = src.profile.copy()
        out_profile.update({
            'height':    out_image.shape[1],
            'width':     out_image.shape[2],
            'transform': out_transform,
            'nodata':    nodata,
            'compress':  'lzw'
        })

        with rasterio.open(output_path, 'w', **out_profile) as dst:
            dst.write(out_image)

    print(f"  ✅ Clipped → {output_path.name}  shape={out_image.shape[1:]}")
    return output_path


def generate_constraint_mask(boundary_gdf, reference_path: Path, output_path: Path):
    """
    Rasterize the Bungoma boundary polygon as a binary mask:
      1 = inside Bungoma (allowed)
      0 = outside (excluded / nodata)

    Uses the clipped normalized layer as the reference grid so
    the mask is guaranteed to be the same shape.
    """
    with rasterio.open(reference_path) as ref:
        ref_transform = ref.transform
        ref_crs       = ref.crs
        ref_height    = ref.height
        ref_width     = ref.width

    # Reproject boundary to raster CRS
    gdf = boundary_gdf.to_crs(ref_crs)
    shapes = [(geom, 1) for geom in gdf.geometry]

    mask_arr = rasterize(
        shapes,
        out_shape=(ref_height, ref_width),
        transform=ref_transform,
        fill=0,          # outside = 0
        dtype=np.uint8
    )

    profile = {
        'driver':    'GTiff',
        'height':    ref_height,
        'width':     ref_width,
        'count':     1,
        'dtype':     'uint8',
        'crs':       ref_crs,
        'transform': ref_transform,
        'nodata':    0,
        'compress':  'lzw'
    }

    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(mask_arr, 1)

    inside  = int(mask_arr.sum())
    outside = int((mask_arr == 0).sum())
    print(f"  ✅ Constraint mask → {output_path.name}")
    print(f"     Inside Bungoma: {inside:,} px  |  Outside: {outside:,} px")
    return output_path


def main():
    print("=" * 60)
    print("  CLIP RASTERS TO BUNGOMA BOUNDARY")
    print("=" * 60)
    print()

    # 1. Load boundary
    boundary = load_boundary(BOUNDARY_PATH)
    shapes = list(boundary.geometry)
    print()

    # 2. Clip each normalized layer IN-PLACE
    print("── Clipping normalized layers ──────────────────────────────")
    clipped_reference = None

    for name in LAYERS:
        src_path = NORMALIZED_DIR / f'normalized_{name}.tif'
        if not src_path.exists():
            print(f"  ⚠️  Missing: {src_path.name} — skipping")
            continue

        clip_raster(src_path, src_path, shapes, nodata=0)

        if clipped_reference is None:
            clipped_reference = src_path

    print()

    # 3. Regenerate constraint mask aligned to clipped rasters
    print("── Regenerating constraint mask ────────────────────────────")
    if clipped_reference is None:
        print("❌ No clipped rasters found — cannot generate constraint mask.")
        return

    generate_constraint_mask(boundary, clipped_reference, CONSTRAINT_OUT)
    print()

    # 4. Summary
    print("=" * 60)
    print("  DONE — next steps:")
    print("  1. Restart the FastAPI server (python src/api.py)")
    print("  2. Run a fresh analysis in the frontend")
    print("  3. The map should now follow Bungoma's actual shape")
    print("=" * 60)


if __name__ == '__main__':
    main()