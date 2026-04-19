"""
pc_fetcher.py
-------------
Fetches all raster inputs for a county on demand.

Sources:
  Elevation    → Planetary Computer  cop-dem-glo-30  (30 m)
  Slope        → Derived from DEM locally
  Rainfall     → NASA POWER API      monthly climatology → annual mm/yr
  Temperature  → NASA POWER API      monthly climatology → mean annual °C
  Soil         → Planetary Computer  soilgrids  (clay 0-30cm g/kg)

NASA POWER strategy:
  - Sample a regular grid of points across the county bbox
  - Fetch 20-year monthly climatology (T2M, PRECTOTCORR) per point via JSON API
  - Interpolate sampled values to a full raster using scipy griddata
  - No authentication, no large downloads (~0.5 KB per point)

Usage:
    python src/pc_fetcher.py           # connection + cache status
    python src/pc_fetcher.py --fetch   # download all missing layers
"""

import logging
import math
import sys
import tempfile
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import requests
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling, calculate_default_transform

logger = logging.getLogger("pc-fetcher")

_PC_CATALOG_URL  = "https://planetarycomputer.microsoft.com/api/stac/v1"
_NASA_POWER_URL  = "https://power.larc.nasa.gov/api/temporal/climatology/point"
_DEFAULT_RES     = 0.005   # degrees (~500 m)
_POWER_GRID_STEP = 0.25    # sample every 0.25° — ~7×6 points over Kitui


# ══════════════════════════════════════════════════════════════════════════════
# Cache helpers
# ══════════════════════════════════════════════════════════════════════════════

def layers_are_cached(config: dict) -> bool:
    paths, county = config["_paths"], config["county"]
    return all(
        (paths["raw_dir"] / f"{county}_{n}.tif").exists()
        for n in config["layers"]
    )


def layer_is_cached(config: dict, name: str) -> bool:
    paths, county = config["_paths"], config["county"]
    return (paths["raw_dir"] / f"{county}_{name}.tif").exists()


# ══════════════════════════════════════════════════════════════════════════════
# Bounding box
# ══════════════════════════════════════════════════════════════════════════════

def _get_bbox(config: dict) -> Tuple[float, float, float, float]:
    """Returns (west, south, east, north) with small buffer."""
    import geopandas as gpd
    bp = config["_paths"]["boundary"]
    if bp.exists():
        gdf = gpd.read_file(bp)
        if str(gdf.crs) != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")
        b = gdf.total_bounds
        return (b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1)
    lat, lon = config["map_center"]
    return (lon - 1.5, lat - 1.5, lon + 1.5, lat + 1.5)


# ══════════════════════════════════════════════════════════════════════════════
# Raster I/O helpers
# ══════════════════════════════════════════════════════════════════════════════

def _save_layer(data: np.ndarray, transform, crs, path: Path,
                nodata: float = -9999.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", dtype=rasterio.float32,
        width=data.shape[1], height=data.shape[0],
        count=1, crs=crs, transform=transform,
        nodata=nodata, compress="lzw",
    ) as dst:
        dst.write(data.astype(np.float32), 1)


def _reproject_to_wgs84(src_path: Path, dst_path: Path,
                         resolution: float = _DEFAULT_RES,
                         resampling=Resampling.bilinear):
    with rasterio.open(src_path) as src:
        t, w, h = calculate_default_transform(
            src.crs, "EPSG:4326",
            src.width, src.height, *src.bounds,
            resolution=resolution,
        )
        profile = src.profile.copy()
        profile.update(crs="EPSG:4326", transform=t, width=w, height=h,
                       dtype=rasterio.float32, compress="lzw")
        data = np.zeros((h, w), dtype=np.float32)
        reproject(
            source=rasterio.band(src, 1), destination=data,
            src_transform=src.transform, src_crs=src.crs,
            dst_transform=t, dst_crs="EPSG:4326",
            resampling=resampling,
        )
    with rasterio.open(dst_path, "w", **profile) as dst:
        dst.write(data, 1)


# ══════════════════════════════════════════════════════════════════════════════
# PC catalog + download
# ══════════════════════════════════════════════════════════════════════════════

def _get_pc_catalog():
    import planetary_computer, pystac_client
    return pystac_client.Client.open(
        _PC_CATALOG_URL,
        modifier=planetary_computer.sign_inplace,
    )


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"  ↓ {dest.name}")
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                f.write(chunk)
    return dest


# ══════════════════════════════════════════════════════════════════════════════
# Elevation
# ══════════════════════════════════════════════════════════════════════════════

def fetch_elevation(config: dict, output_path: Path) -> Path:
    logger.info("── Fetching elevation (COP-DEM GLO-30) ──────────────────")
    bbox    = _get_bbox(config)
    catalog = _get_pc_catalog()

    items = list(catalog.search(
        collections=["cop-dem-glo-30"], bbox=bbox,
    ).items())
    if not items:
        raise RuntimeError("No COP-DEM tiles found")

    logger.info(f"  {len(items)} tile(s)")
    tmp        = Path(tempfile.mkdtemp(prefix="pc_dem_"))
    tile_paths = []
    for item in items:
        p = tmp / f"{item.id}.tif"
        _download(item.assets["data"].href, p)
        tile_paths.append(p)

    if len(tile_paths) == 1:
        mosaic_path = tile_paths[0]
    else:
        logger.info(f"  Mosaicking {len(tile_paths)} tiles...")
        srcs = [rasterio.open(p) for p in tile_paths]
        mosaic, t = merge(srcs, method="first")
        for s in srcs:
            s.close()
        mosaic_path = tmp / "mosaic.tif"
        p0 = srcs[0].profile.copy()
        p0.update(height=mosaic.shape[1], width=mosaic.shape[2], transform=t)
        with rasterio.open(mosaic_path, "w", **p0) as dst:
            dst.write(mosaic)

    _reproject_to_wgs84(mosaic_path, output_path,
                         resolution=config.get("resolution", _DEFAULT_RES))
    logger.info(f"  ✅ Elevation: {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Slope
# ══════════════════════════════════════════════════════════════════════════════

def fetch_slope(config: dict, elevation_path: Path, output_path: Path) -> Path:
    logger.info("── Deriving slope ────────────────────────────────────────")
    with rasterio.open(elevation_path) as src:
        elev      = src.read(1).astype(np.float64)
        transform = src.transform
        crs       = src.crs
        nodata    = src.nodata if src.nodata is not None else -9999

    lat     = config["map_center"][0]
    pixel_h = abs(transform.e) * 111320.0
    pixel_w = abs(transform.a) * 111320.0 * math.cos(math.radians(lat))

    valid     = (elev != nodata) & np.isfinite(elev)
    ec        = np.where(valid, elev, np.nan)
    dy, dx    = np.gradient(ec, pixel_h, pixel_w)
    slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2))).astype(np.float32)
    slope_deg[~valid] = -9999.0

    _save_layer(slope_deg, transform, crs, output_path, nodata=-9999.0)
    logger.info(f"  ✅ Slope: {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# NASA POWER grid sampler + interpolator
# ══════════════════════════════════════════════════════════════════════════════

def _power_fetch_point(lon: float, lat: float, parameter: str) -> float:
    """
    Fetch 20-year annual climatology mean from NASA POWER for one point.
    Returns the ANN (annual mean) value, or None on failure.
    """
    url = (
        f"{_NASA_POWER_URL}"
        f"?parameters={parameter}"
        f"&community=AG"
        f"&longitude={lon:.4f}&latitude={lat:.4f}"
        f"&format=JSON"
    )
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        val = r.json()["properties"]["parameter"][parameter].get("ANN")
        return float(val) if val is not None and val != -999 else None
    except Exception as e:
        logger.debug(f"  POWER ({lon:.2f},{lat:.2f}): {e}")
        return None


def _power_to_raster(config: dict, parameter: str,
                      transform_fn=None) -> Tuple[np.ndarray, object, str]:
    """
    Sample NASA POWER on a grid, interpolate to full county raster.

    parameter:    NASA POWER parameter name (e.g. T2M, PRECTOTCORR)
    transform_fn: optional value transform, e.g. lambda x: x * 365
    """
    from scipy.interpolate import griddata

    west, south, east, north = _get_bbox(config)
    res  = config.get("resolution", _DEFAULT_RES)
    step = _POWER_GRID_STEP

    lons = np.arange(west  + step / 2, east,  step)
    lats = np.arange(south + step / 2, north, step)
    logger.info(f"  Sampling {len(lons)*len(lats)} points "
                f"({len(lons)}×{len(lats)} grid, step={step}°)")

    pts_lon, pts_lat, pts_val = [], [], []
    for lat in lats:
        for lon in lons:
            val = _power_fetch_point(lon, lat, parameter)
            if val is not None:
                if transform_fn:
                    val = transform_fn(val)
                pts_lon.append(lon)
                pts_lat.append(lat)
                pts_val.append(val)

    if len(pts_val) < 3:
        raise RuntimeError(
            f"NASA POWER: only {len(pts_val)} valid points for {parameter}. "
            f"Check API access."
        )

    logger.info(f"  {len(pts_val)} valid samples  "
                f"range {min(pts_val):.1f}–{max(pts_val):.1f}")

    # Build output grid
    out_w = max(int((east - west)  / res), 10)
    out_h = max(int((north - south) / res), 10)
    t     = from_bounds(west, south, east, north, out_w, out_h)

    col_c = np.linspace(west  + res / 2, east  - res / 2, out_w)
    row_c = np.linspace(north - res / 2, south + res / 2, out_h)
    gx, gy = np.meshgrid(col_c, row_c)

    pts = np.column_stack([pts_lon, pts_lat])

    # Cubic → linear → nearest fallback chain
    interp = griddata(pts, pts_val, (gx, gy), method="cubic")
    nans   = np.isnan(interp)
    if nans.any():
        linear          = griddata(pts, pts_val, (gx, gy), method="linear")
        interp[nans]    = linear[nans]
    nans = np.isnan(interp)
    if nans.any():
        nearest         = griddata(pts, pts_val, (gx, gy), method="nearest")
        interp[nans]    = nearest[nans]

    return interp.astype(np.float32), t, "EPSG:4326"


# ══════════════════════════════════════════════════════════════════════════════
# Rainfall
# ══════════════════════════════════════════════════════════════════════════════

def fetch_rainfall(config: dict, output_path: Path) -> Path:
    """
    Mean annual rainfall (mm/yr) from NASA POWER.
    PRECTOTCORR ANN = annual mean mm/day × 365 = annual mm.
    """
    logger.info("── Fetching rainfall (NASA POWER) ────────────────────────")
    data, transform, crs = _power_to_raster(
        config,
        parameter="PRECTOTCORR",
        transform_fn=lambda x: x * 365,
    )
    logger.info(f"  Range {data.min():.0f}–{data.max():.0f} mm/yr  "
                f"mean {data.mean():.0f}")
    _save_layer(data, transform, crs, output_path)
    logger.info(f"  ✅ Rainfall: {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Temperature
# ══════════════════════════════════════════════════════════════════════════════

def fetch_temperature(config: dict, output_path: Path) -> Path:
    """
    Mean annual temperature (°C) from NASA POWER.
    T2M ANN = mean annual temperature at 2 m in °C.
    """
    logger.info("── Fetching temperature (NASA POWER) ─────────────────────")
    data, transform, crs = _power_to_raster(
        config,
        parameter="T2M",
        transform_fn=None,
    )
    logger.info(f"  Range {data.min():.1f}–{data.max():.1f} °C  "
                f"mean {data.mean():.1f}")
    _save_layer(data, transform, crs, output_path)
    logger.info(f"  ✅ Temperature: {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Soil
# ══════════════════════════════════════════════════════════════════════════════

def fetch_soil(config: dict, output_path: Path) -> Path:
    """Clay content (g/kg) from SoilGrids on Planetary Computer."""
    logger.info("── Fetching soil clay (SoilGrids / PC) ──────────────────")
    bbox    = _get_bbox(config)
    catalog = _get_pc_catalog()

    items = list(catalog.search(collections=["soilgrids"], bbox=bbox).items())
    clay  = [i for i in items
             if "clay" in i.id.lower() and ("0-30" in i.id or "030" in i.id)]
    if not clay:
        clay = [i for i in items if "clay" in i.id.lower()]
    if not clay:
        raise RuntimeError("No SoilGrids clay items on PC")

    logger.info(f"  {len(clay)} item(s)")
    tmp = Path(tempfile.mkdtemp(prefix="pc_soil_"))
    tile_paths = []
    for item in clay:
        key = next(
            (k for k in item.assets
             if "clay" in k.lower() or k in ["mean", "data"]),
            list(item.assets.keys())[0],
        )
        p = tmp / f"{item.id}.tif"
        try:
            _download(item.assets[key].href, p)
            tile_paths.append(p)
        except Exception as e:
            logger.warning(f"  {item.id}: {e}")

    if not tile_paths:
        raise RuntimeError("All SoilGrids downloads failed")

    if len(tile_paths) == 1:
        raw_path = tile_paths[0]
    else:
        srcs = [rasterio.open(p) for p in tile_paths]
        mosaic, t = merge(srcs, method="first")
        for s in srcs:
            s.close()
        raw_path = tmp / "soil_mosaic.tif"
        p0 = srcs[0].profile.copy()
        p0.update(height=mosaic.shape[1], width=mosaic.shape[2], transform=t)
        with rasterio.open(raw_path, "w", **p0) as dst:
            dst.write(mosaic)

    with rasterio.open(raw_path) as src:
        data      = src.read(1).astype(np.float32)
        transform = src.transform
        crs       = src.crs
        nd        = src.nodata if src.nodata is not None else -9999

    valid    = (data != nd) & np.isfinite(data)
    clay_gkg = np.where(valid, data / 10.0, nd)   # cg/kg → g/kg

    conv = tmp / "soil_conv.tif"
    _save_layer(clay_gkg, transform, crs, conv, nodata=nd)
    _reproject_to_wgs84(conv, output_path,
                         resolution=config.get("resolution", _DEFAULT_RES),
                         resampling=Resampling.nearest)
    logger.info(f"  ✅ Soil: {output_path.name}  "
                f"(mean {np.nanmean(clay_gkg[valid]):.0f} g/kg)")
    return output_path


def _fetch_soil_isric_fallback(config: dict, output_path: Path) -> Path:
    logger.info("── Fetching soil clay (ISRIC fallback) ──────────────────")
    import math as _math
    west, south, east, north = _get_bbox(config)
    res = config.get("resolution", _DEFAULT_RES)

    # Snap bbox to resolution grid so transform is exact
    west  = _math.floor(west  / res) * res
    south = _math.floor(south / res) * res
    east  = _math.ceil(east   / res) * res
    north = _math.ceil(north  / res) * res

    w = round((east  - west)  / res)
    h = round((north - south) / res)

    lat, lon = (south + north) / 2, (west + east) / 2
    clay_gkg = 250.0
    try:
        url  = (f"https://rest.isric.org/soilgrids/v2.0/properties/query"
                f"?lon={lon:.4f}&lat={lat:.4f}"
                f"&property=clay&depth=0-30cm&value=mean")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        layers = resp.json().get("properties", {}).get("layers", [])
        for layer in layers:
            if layer.get("name") == "clay":
                for depth in layer.get("depths", []):
                    val = depth.get("values", {}).get("mean")
                    if val is not None:
                        clay_gkg = float(val) / 10.0
                        break
                break
    except Exception as e:
        logger.warning(f"  ISRIC: {e} — using 250 g/kg")

    logger.info(f"  Clay value: {clay_gkg:.0f} g/kg  grid: {w}×{h}  res: {res}")
    t = from_bounds(west, south, east, north, w, h)
    _save_layer(np.full((h, w), clay_gkg, dtype=np.float32), t, "EPSG:4326", output_path)
    logger.info(f"  ✅ Soil (ISRIC {clay_gkg:.0f} g/kg): {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_layers(config: dict, force: bool = False) -> Dict[str, Path]:
    """Fetch all raw raster layers. Skips cached unless force=True."""
    paths, county = config["_paths"], config["county"]
    raw_dir = paths["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 55)
    logger.info(f"  PC FETCHER: {config['display_name'].upper()}")
    logger.info("=" * 55)

    fetched = {}

    def out(n): return raw_dir / f"{county}_{n}.tif"
    def cached(n):
        logger.info(f"  ✓ {n}: cached")
        fetched[n] = out(n)

    if not force and out("elevation").exists(): cached("elevation")
    else:
        fetch_elevation(config, out("elevation"))
        fetched["elevation"] = out("elevation")

    if not force and out("slope").exists(): cached("slope")
    else:
        try:
            fetch_slope(config, out("elevation"), out("slope"))
            fetched["slope"] = out("slope")
        except Exception as e: logger.warning(f"  Slope: {e}")

    if not force and out("rainfall").exists(): cached("rainfall")
    else:
        try:
            fetch_rainfall(config, out("rainfall"))
            fetched["rainfall"] = out("rainfall")
        except Exception as e: logger.warning(f"  Rainfall: {e}")

    if not force and out("temperature").exists(): cached("temperature")
    else:
        try:
            fetch_temperature(config, out("temperature"))
            fetched["temperature"] = out("temperature")
        except Exception as e: logger.warning(f"  Temperature: {e}")

    if not force and out("soil").exists(): cached("soil")
    else:
        try:
            fetch_soil(config, out("soil"))
            fetched["soil"] = out("soil")
        except Exception as e:
            logger.warning(f"  PC soil: {e} — trying ISRIC...")
            try:
                _fetch_soil_isric_fallback(config, out("soil"))
                fetched["soil"] = out("soil")
            except Exception as e2: logger.warning(f"  ISRIC: {e2}")

    missing = set(config["layers"]) - set(fetched)
    logger.info(f"\n  Fetched : {sorted(fetched)}")
    if missing: logger.warning(f"  Missing : {sorted(missing)}")
    logger.info("=" * 55)
    return fetched


# ══════════════════════════════════════════════════════════════════════════════
# Standalone
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    sys.path.insert(0, str(Path(__file__).parent))
    from config import load_config

    config = load_config()
    logger.info(f"Testing PC fetcher for: {config['display_name']}")
    logger.info(f"Bbox: {_get_bbox(config)}")

    try:
        cat = _get_pc_catalog()
        logger.info(f"PC catalog connected: {cat.title}")
    except Exception as e:
        logger.error(f"PC connection failed: {e}")
        sys.exit(1)

    logger.info(f"Layers cached: {layers_are_cached(config)}")
    for name in config["layers"]:
        raw = config["_paths"]["raw_dir"] / f"{config['county']}_{name}.tif"
        logger.info(f"  {name}: {'✅ cached' if raw.exists() else '❌ missing'}")

    print("\nTo fetch missing layers: python src/pc_fetcher.py --fetch")

    if "--fetch" in sys.argv:
        result = fetch_all_layers(config)
        print(f"\nFetched {len(result)} layers:")
        for n, p in result.items():
            print(f"  {n}: {p.name} ({p.stat().st_size // 1024} KB)")