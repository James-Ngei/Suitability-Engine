import rasterio
from rasterio.features import rasterize
from pathlib import Path
import geopandas as gpd
import numpy as np

# --- Paths ---
BOUNDARY_PATH = Path("data/boundaries/bungoma_boundary.gpkg")
REFERENCE_RASTER = Path("data/preprocessed/bungoma_elevation.tif")
MASK_OUTPUT = Path("data/preprocessed/bungoma_constraints_mask.tif")

# --- Parameters ---
MASK_VALUE = 1       # value for allowed pixels
NODATA_VALUE = 0     # value for excluded pixels

# --- Load boundary ---
boundary = gpd.read_file(BOUNDARY_PATH)
print(f"📌 Boundary loaded: {len(boundary)} features")

# --- Load reference raster for grid info ---
with rasterio.open(REFERENCE_RASTER) as ref:
    REF_HEIGHT = ref.height
    REF_WIDTH = ref.width
    REF_TRANSFORM = ref.transform
    REF_CRS = ref.crs
    REF_DTYPE = ref.dtypes[0]

# --- Rasterize the boundary to match reference raster ---
mask_arr = rasterize(
    [(geom, MASK_VALUE) for geom in boundary.geometry],
    out_shape=(REF_HEIGHT, REF_WIDTH),
    transform=REF_TRANSFORM,
    fill=NODATA_VALUE,
    dtype=np.uint8
)

print(f"✅ Constraints mask generated, shape: {mask_arr.shape}")

# --- Save mask to file ---
mask_meta = {
    "driver": "GTiff",
    "height": REF_HEIGHT,
    "width": REF_WIDTH,
    "count": 1,
    "dtype": "uint8",
    "crs": REF_CRS,
    "transform": REF_TRANSFORM,
    "nodata": NODATA_VALUE,
    "compress": "lzw"
}

with rasterio.open(MASK_OUTPUT, "w", **mask_meta) as dst:
    dst.write(mask_arr, 1)

print(f"✅ Constraints mask saved: {MASK_OUTPUT}")
