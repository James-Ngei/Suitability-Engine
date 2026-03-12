import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.mask import mask
import geopandas as gpd
import numpy as np
from pathlib import Path

# --- Paths ---
RAW_DIR = Path("data/counties/kitui/raw")          # original rasters
BOUNDARY_PATH = Path("data/counties/kitui/boundaries/kitui_boundary.gpkg")
PROCESSED_DIR = Path("data/counties/kitui/processed")
PROCESSED_DIR.mkdir(exist_ok=True)
REFERENCE_RASTER = PROCESSED_DIR / "kitui_elevation.tif"

# --- Parameters ---
TARGET_CRS = "EPSG:4326"
NODATA_VALUE = 255

# --- Load boundary ---
print("📌 Loading Kitui county boundary...")
boundary = gpd.read_file(BOUNDARY_PATH)
print(f"✅ Boundary loaded, {len(boundary)} features found.")

# --- Load reference raster once ---
with rasterio.open(REFERENCE_RASTER) as ref:
    REF_CRS = ref.crs
    REF_TRANSFORM = ref.transform
    REF_WIDTH = ref.width
    REF_HEIGHT = ref.height
    REF_DTYPE = ref.dtypes[0]

# --- Processing Loop ---
for raster_path in RAW_DIR.rglob("*.tif"):
    print(f"\n🔹 Processing raster: {raster_path.name}")

    with rasterio.open(raster_path) as src:
        print(f"  CRS: {src.crs}, Resolution: {src.res}, Shape: {src.shape}")

        # Initialize array aligned to reference raster
        data = np.full((src.count, REF_HEIGHT, REF_WIDTH), NODATA_VALUE, dtype=src.dtypes[0])

        # Decide resampling method
        resampling_method = Resampling.nearest if raster_path.name in ["soil.tif", "landcover.tif"] else Resampling.bilinear

        # Reproject each band to match reference raster
        for i in range(1, src.count + 1):
            reproject(
                source=rasterio.band(src, i),
                destination=data[i-1],
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=REF_TRANSFORM,
                dst_crs=REF_CRS,
                resampling=resampling_method,
                src_nodata=src.nodata if src.nodata is not None else NODATA_VALUE,
                dst_nodata=NODATA_VALUE
            )

        print("  ✅ Reprojection and resampling done.")

        # Prepare metadata for writing
        kwargs = src.meta.copy()
        kwargs.update({
            "crs": REF_CRS,
            "transform": REF_TRANSFORM,
            "width": REF_WIDTH,
            "height": REF_HEIGHT,
            "nodata": NODATA_VALUE,
            "compress": "lzw"
        })

        # Clip to boundary
        with rasterio.io.MemoryFile() as memfile:
            with memfile.open(**kwargs) as tmp:
                tmp.write(data)
                clipped, clipped_transform = mask(
                    tmp,
                    shapes=boundary.geometry,
                    crop=True,
                    nodata=NODATA_VALUE
                )

        print(f"  ✅ Clipped to Kitui boundary, shape: {clipped.shape[1:]}")

        # Save preprocessed raster
        out_meta = kwargs.copy()
        out_meta.update({
            "height": clipped.shape[1],
            "width": clipped.shape[2],
            "transform": clipped_transform
        })

        out_path = PROCESSED_DIR / raster_path.name
        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(clipped)

        print(f"✅ Preprocessed raster saved: {out_path}")
