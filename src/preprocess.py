"""
preprocess.py
-------------
Reprojects and clips raw rasters to match the reference raster grid, then
saves them into the county preprocessed directory.

Also builds the constraints mask by combining:
  1. County boundary          — pixels outside the county are excluded
  2. Protected areas (national) — pixels inside protected areas are excluded

The national protected areas file lives at data/shared/protected_areas_kenya.gpkg.
Download it once from https://www.protectedplanet.net (filter by Kenya, WDPA format).
If the file is absent the script warns and falls back to a boundary-only mask.

Reads all paths from the active county config — no hardcoded county names.

Run from the project root:
    python src/preprocess.py
"""

import sys
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.mask import mask
from rasterio.features import rasterize
import geopandas as gpd
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs

# --- Parameters ---
NODATA_VALUE       = 255
CATEGORICAL_LAYERS = {"soil.tif", "landcover.tif"}


# ── Constraints mask ───────────────────────────────────────────────────────────

def build_constraints_mask(boundary_gdf: gpd.GeoDataFrame,
                            reference_path: Path,
                            output_path: Path,
                            protected_path: Path = None) -> Path:
    """
    Build a binary constraints mask raster:
        1 = allowed  (inside county AND not a protected area)
        0 = excluded (outside county OR inside a protected area)

    Args:
        boundary_gdf:   County boundary GeoDataFrame (EPSG:4326).
        reference_path: A preprocessed raster used for grid dimensions/transform.
        output_path:    Destination path for the mask .tif.
        protected_path: Path to the national protected areas vector file.
                        If None or missing the mask falls back to boundary-only.
    """
    with rasterio.open(reference_path) as ref:
        transform = ref.transform
        crs       = ref.crs
        height    = ref.height
        width     = ref.width

    # --- Layer 1: county boundary (1 = inside) ---
    boundary_reproj = boundary_gdf.to_crs(crs)
    boundary_shapes = [(geom, 1) for geom in boundary_reproj.geometry]

    boundary_mask = rasterize(
        boundary_shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    )
    print(f"    Boundary   : {int(boundary_mask.sum()):,} / {height * width:,} px inside county")

    # --- Layer 2: protected areas (1 = protected → excluded) ---
    protected_mask = np.zeros((height, width), dtype=np.uint8)

    if protected_path and Path(protected_path).exists():
        print(f"    Loading    : {Path(protected_path).name}")
        prot_gdf = gpd.read_file(protected_path)

        # Spatial pre-filter to county bbox for speed
        bbox = boundary_gdf.to_crs('EPSG:4326').total_bounds  # [minx, miny, maxx, maxy]
        prot_gdf = prot_gdf.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]

        if len(prot_gdf) == 0:
            print(f"    ℹ️  No protected areas overlap this county — skipping")
        else:
            # WDPA includes point records; keep polygons only
            prot_gdf = prot_gdf[
                prot_gdf.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])
            ].to_crs(crs)

            if len(prot_gdf) > 0:
                prot_shapes = [
                    (geom, 1) for geom in prot_gdf.geometry if geom is not None
                ]
                protected_mask = rasterize(
                    prot_shapes,
                    out_shape=(height, width),
                    transform=transform,
                    fill=0,
                    dtype=np.uint8,
                )
                print(f"    Protected  : {int(protected_mask.sum()):,} px will be excluded")
            else:
                print(f"    ℹ️  No polygon protected areas found after filtering")
    else:
        if protected_path:
            print(f"    ⚠️  Protected areas file not found:")
            print(f"        {protected_path}")
            print(f"        Download from protectedplanet.net → data/shared/")
            print(f"        Falling back to boundary-only mask.")
        else:
            print(f"    ℹ️  No protected areas path configured — boundary-only mask.")

    # --- Combine: allowed = inside boundary AND NOT protected ---
    combined = np.where(
        (boundary_mask == 1) & (protected_mask == 0),
        np.uint8(1),
        np.uint8(0),
    )

    allowed  = int(combined.sum())
    excluded = int(boundary_mask.sum()) - allowed
    print(f"    Combined   : {allowed:,} px allowed, {excluded:,} px excluded (protected)")

    profile = {
        'driver':    'GTiff',
        'height':    height,
        'width':     width,
        'count':     1,
        'dtype':     'uint8',
        'crs':       crs,
        'transform': transform,
        'nodata':    0,
        'compress':  'lzw',
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, 'w', **profile) as dst:
        dst.write(combined, 1)

    print(f"    ✅ Saved   : {output_path.name}")
    return output_path


# ── Raster processing ──────────────────────────────────────────────────────────

def _process_one(raster_path: Path, out_path: Path,
                 boundary: gpd.GeoDataFrame,
                 ref_crs=None, ref_transform=None,
                 ref_width=None, ref_height=None,
                 is_reference: bool = False):
    """Reproject one raster to the reference grid and clip to boundary."""
    print(f"\n🔹 Processing: {raster_path.name}")

    with rasterio.open(raster_path) as src:
        print(f"  CRS: {src.crs}, Resolution: {src.res}, Shape: {src.shape}")
        src_dtype = src.dtypes[0]

        if is_reference:
            ref_crs       = src.crs
            ref_transform = src.transform
            ref_width     = src.width
            ref_height    = src.height

        resampling = (
            Resampling.nearest
            if raster_path.name in CATEGORICAL_LAYERS
            else Resampling.bilinear
        )

        data = np.full(
            (src.count, ref_height, ref_width),
            NODATA_VALUE,
            dtype=src_dtype,
        )

        for i in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, i),
                destination=data[i - 1],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=ref_transform,
                dst_crs=ref_crs,
                resampling=resampling,
                src_nodata=src.nodata if src.nodata is not None else NODATA_VALUE,
                dst_nodata=NODATA_VALUE,
            )

        print("  ✅ Reprojection done.")

        kwargs = src.meta.copy()
        kwargs.update({
            'crs':       ref_crs,
            'transform': ref_transform,
            'width':     ref_width,
            'height':    ref_height,
            'nodata':    NODATA_VALUE,
            'compress':  'lzw',
        })

    # Clip to boundary
    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(**kwargs) as tmp:
            tmp.write(data)
            clipped, clipped_transform = mask(
                tmp,
                shapes=boundary.geometry,
                crop=True,
                nodata=NODATA_VALUE,
            )

    print(f"  ✅ Clipped to boundary, shape: {clipped.shape[1:]}")

    out_meta = kwargs.copy()
    out_meta.update({
        'height':    clipped.shape[1],
        'width':     clipped.shape[2],
        'transform': clipped_transform,
    })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, 'w', **out_meta) as dst:
        dst.write(clipped)

    print(f"  ✅ Saved: {out_path.name}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    config = load_config()
    paths  = config['_paths']
    county = config['county']

    print("=" * 55)
    print(f"  PREPROCESS: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    create_county_dirs(config)

    raw_dir          = paths['raw_dir']
    preprocessed_dir = paths['preprocessed_dir']
    boundary_path    = paths['boundary']
    protected_path   = paths['protected_areas']

    # --- Load county boundary ---
    if not boundary_path.exists():
        print(f"❌ Boundary not found: {boundary_path}")
        return

    print(f"📌 Loading {config['display_name']} boundary...")
    boundary = gpd.read_file(boundary_path)
    if str(boundary.crs) != 'EPSG:4326':
        boundary = boundary.to_crs('EPSG:4326')
    print(f"✅ Boundary loaded — {len(boundary)} feature(s).")
    print()

    # --- Ensure reference raster (elevation) exists ---
    reference_name = f"{county}_elevation.tif"
    reference_path = preprocessed_dir / reference_name

    if not reference_path.exists():
        raw_elevation = raw_dir / "elevation.tif"
        if not raw_elevation.exists():
            print(f"❌ Reference raster not found: {reference_path}")
            print(f"   Drop elevation.tif into {raw_dir} and re-run.")
            return
        print(f"ℹ️  Building reference raster from raw elevation first...")
        _process_one(raw_elevation, reference_path, boundary, is_reference=True)

    with rasterio.open(reference_path) as ref:
        REF_CRS       = ref.crs
        REF_TRANSFORM = ref.transform
        REF_WIDTH     = ref.width
        REF_HEIGHT    = ref.height

    print(f"✅ Reference raster: {reference_name} ({REF_WIDTH}×{REF_HEIGHT})")
    print()

    # --- Process all raw rasters ---
    raster_paths = sorted(raw_dir.rglob("*.tif"))
    if not raster_paths:
        print(f"⚠️  No .tif files found in {raw_dir}")
    else:
        print("── Processing rasters ───────────────────────────────────")
        for rp in raster_paths:
            out_name = f"{county}_{rp.name}"
            out_path = preprocessed_dir / out_name

            if out_path.exists():
                print(f"  ✓  Already exists: {out_name} — skipping")
                continue

            _process_one(
                rp, out_path, boundary,
                ref_crs=REF_CRS, ref_transform=REF_TRANSFORM,
                ref_width=REF_WIDTH, ref_height=REF_HEIGHT,
            )
        print()

    # --- Build constraints mask ---
    print("── Building constraints mask ────────────────────────────")
    build_constraints_mask(
        boundary_gdf=boundary,
        reference_path=reference_path,
        output_path=paths['constraint_mask'],
        protected_path=protected_path,
    )
    print()

    print("=" * 55)
    print("  DONE")
    print(f"  Preprocessed : {preprocessed_dir}")
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