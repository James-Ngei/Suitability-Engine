"""
pc_fetcher.py
-------------
Fetches all raster inputs for a county on demand from Planetary Computer
and NASA POWER. Also fetches county boundary from OSM if not present.

"""

import logging
import math
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import requests
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling, calculate_default_transform

logger = logging.getLogger("pc-fetcher")

_PC_CATALOG_URL  = "https://planetarycomputer.microsoft.com/api/stac/v1"
_NASA_POWER_URL  = "https://power.larc.nasa.gov/api/temporal/climatology/point"
_DEFAULT_RES     = 0.005
_POWER_GRID_STEP = 0.1   # fixed — gives ≥9 points even for tiny counties
_ISRIC_GRID_STEP = 0.15


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
# Boundary — fetch from OSM using relation ID
# ══════════════════════════════════════════════════════════════════════════════

def fetch_boundary(config: dict) -> Path:
    import geopandas as gpd
    import shapely.geometry as geom
    from shapely.ops import unary_union

    paths       = config["_paths"]
    county      = config["county"]
    output_path = paths["boundary"]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    relation_id = config.get("osm_relation_id")

    if relation_id:
        logger.info(f"[{county}] Fetching boundary from OSM relation {relation_id}")
        overpass_mirrors = [
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
        ]
        query = f"[out:json][timeout:30];relation({relation_id});out geom;"
        raw_json = None
        for mirror in overpass_mirrors:
            try:
                resp = requests.post(mirror, data={"data": query}, timeout=35)
                resp.raise_for_status()
                raw_json = resp.json()
                logger.info(f"[{county}] Overpass OK via {mirror}")
                break
            except Exception as e:
                logger.warning(f"[{county}] Overpass mirror {mirror}: {e}")

        if raw_json:
            gdf = _overpass_to_gdf(raw_json, county)
            if gdf is not None and len(gdf) > 0:
                gdf = gdf.to_crs("EPSG:4326")
                gdf.to_file(output_path, driver="GPKG")
                logger.info(f"[{county}] Boundary saved from OSM: {output_path.name}")
                return output_path

    logger.warning(f"[{county}] OSM boundary fetch failed — using bbox rectangle")
    west, south, east, north = _bbox_from_config(config)
    box = geom.box(west, south, east, north)
    gdf = gpd.GeoDataFrame({"geometry": [box]}, crs="EPSG:4326")
    gdf.to_file(output_path, driver="GPKG")
    logger.info(f"[{county}] Bbox boundary saved: {output_path.name}")
    return output_path


def _overpass_to_gdf(raw_json: dict, county: str):
    try:
        import geopandas as gpd
        import shapely.geometry as geom
        from shapely.ops import unary_union, polygonize
        from shapely.validation import make_valid

        elements = raw_json.get("elements", [])
        if not elements:
            return None

        members     = elements[0].get("members", [])
        outer_lines = []
        inner_lines = []
        for m in members:
            if m.get("type") != "way":
                continue
            coords = [(pt["lon"], pt["lat"]) for pt in m.get("geometry", [])]
            if len(coords) < 2:
                continue
            line = geom.LineString(coords)
            (inner_lines if m.get("role") == "inner" else outer_lines).append(line)

        outer_polys = list(polygonize(unary_union(outer_lines)))
        if not outer_polys and outer_lines:
            coords = []
            for line in outer_lines:
                coords.extend(list(line.coords))
            try:
                outer_polys = [geom.Polygon(coords)]
            except Exception:
                pass

        if not outer_polys:
            return None

        boundary_poly = make_valid(unary_union(outer_polys))
        return gpd.GeoDataFrame(
            {"name": [f"{county.capitalize()} County"], "geometry": [boundary_poly]},
            crs="EPSG:4326",
        )
    except Exception as e:
        logger.warning(f"Overpass → GeoDataFrame failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Bounding box
# ══════════════════════════════════════════════════════════════════════════════

def _bbox_from_config(config: dict) -> Tuple[float, float, float, float]:
    bbox = config.get("bbox")
    if bbox:
        buf = 0.05
        return (bbox["west"] - buf, bbox["south"] - buf,
                bbox["east"] + buf, bbox["north"] + buf)
    lat, lon = config["map_center"]
    return (lon - 1.5, lat - 1.5, lon + 1.5, lat + 1.5)


def _get_bbox(config: dict) -> Tuple[float, float, float, float]:
    bp = config["_paths"]["boundary"]
    if bp.exists():
        try:
            import geopandas as gpd
            gdf = gpd.read_file(bp)
            if str(gdf.crs) != "EPSG:4326":
                gdf = gdf.to_crs("EPSG:4326")
            b = gdf.total_bounds
            return (b[0] - 0.05, b[1] - 0.05, b[2] + 0.05, b[3] + 0.05)
        except Exception as e:
            logger.warning(f"Could not read boundary file: {e}")
    return _bbox_from_config(config)


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
    import planetary_computer
    import pystac_client
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
    items   = list(catalog.search(collections=["cop-dem-glo-30"], bbox=bbox).items())
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
        srcs      = [rasterio.open(p) for p in tile_paths]
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
# Slope (derived from DEM)
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
# NASA POWER — shared grid sampler
# ══════════════════════════════════════════════════════════════════════════════

def _power_fetch_point(lon: float, lat: float, parameter: str) -> Optional[float]:
    url = (
        f"{_NASA_POWER_URL}"
        f"?parameters={parameter}&community=AG"
        f"&longitude={lon:.4f}&latitude={lat:.4f}&format=JSON"
    )
    try:
        r   = requests.get(url, timeout=20)
        r.raise_for_status()
        val = r.json()["properties"]["parameter"][parameter].get("ANN")
        return float(val) if val is not None and val != -999 else None
    except Exception as e:
        logger.debug(f"  POWER ({lon:.2f},{lat:.2f}): {e}")
        return None


def _power_to_raster(config: dict, parameter: str,
                      transform_fn=None) -> Tuple[np.ndarray, object, str]:
    from scipy.interpolate import griddata

    west, south, east, north = _get_bbox(config)
    res  = config.get("resolution", _DEFAULT_RES)

    step = _POWER_GRID_STEP   # fixed 0.1°
    lons = np.arange(west  + step / 2, east,  step)
    lats = np.arange(south + step / 2, north, step)

    # Guarantee minimum 3×3 = 9 points
    if len(lons) < 3:
        lons = np.linspace(west + 0.03, east - 0.03, 4)
    if len(lats) < 3:
        lats = np.linspace(south + 0.03, north - 0.03, 4)

    logger.info(f"  Sampling {len(lons)*len(lats)} points ({len(lons)}×{len(lats)} grid)")

    pts_lon, pts_lat, pts_val = [], [], []
    for lat_pt in lats:
        for lon_pt in lons:
            val = _power_fetch_point(lon_pt, lat_pt, parameter)
            if val is not None:
                pts_lon.append(lon_pt)
                pts_lat.append(lat_pt)
                pts_val.append(transform_fn(val) if transform_fn else val)

    if not pts_val:
        raise RuntimeError(
            f"NASA POWER: no valid points for {parameter}. "
            f"Bbox: {west:.2f},{south:.2f},{east:.2f},{north:.2f}"
        )

    logger.info(f"  {len(pts_val)} valid — range {min(pts_val):.1f}–{max(pts_val):.1f}")

    out_w = max(int((east - west)  / res), 10)
    out_h = max(int((north - south) / res), 10)
    t     = from_bounds(west, south, east, north, out_w, out_h)
    col_c = np.linspace(west  + res / 2, east  - res / 2, out_w)
    row_c = np.linspace(north - res / 2, south + res / 2, out_h)
    gx, gy = np.meshgrid(col_c, row_c)
    pts    = np.column_stack([pts_lon, pts_lat])

    if len(pts_val) >= 4:
        interp = griddata(pts, pts_val, (gx, gy), method="cubic")
        nans   = np.isnan(interp)
        if nans.any():
            interp[nans] = griddata(pts, pts_val, (gx[nans], gy[nans]), method="linear")
    elif len(pts_val) >= 3:
        interp = griddata(pts, pts_val, (gx, gy), method="linear")
    else:
        interp = np.full_like(gx, float(np.mean(pts_val)))

    nans = np.isnan(interp)
    if nans.any():
        interp[nans] = griddata(pts, pts_val, (gx[nans], gy[nans]), method="nearest")

    return interp.astype(np.float32), t, "EPSG:4326"


# ══════════════════════════════════════════════════════════════════════════════
# Rainfall + Temperature
# ══════════════════════════════════════════════════════════════════════════════

def fetch_rainfall(config: dict, output_path: Path) -> Path:
    logger.info("── Fetching rainfall (NASA POWER) ────────────────────────")
    data, transform, crs = _power_to_raster(
        config, parameter="PRECTOTCORR", transform_fn=lambda x: x * 365)
    logger.info(f"  Range {data.min():.0f}–{data.max():.0f} mm/yr  mean {data.mean():.0f}")
    _save_layer(data, transform, crs, output_path)
    logger.info(f"  ✅ Rainfall: {output_path.name}")
    return output_path


def fetch_temperature(config: dict, output_path: Path) -> Path:
    logger.info("── Fetching temperature (NASA POWER) ─────────────────────")
    data, transform, crs = _power_to_raster(config, parameter="T2M")
    logger.info(f"  Range {data.min():.1f}–{data.max():.1f} °C  mean {data.mean():.1f}")
    _save_layer(data, transform, crs, output_path)
    logger.info(f"  ✅ Temperature: {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Soil — PC first, ISRIC spatial grid fallback
# ══════════════════════════════════════════════════════════════════════════════

def fetch_soil(config: dict, output_path: Path) -> Path:
    logger.info("── Fetching soil clay (SoilGrids / PC) ──────────────────")
    bbox    = _get_bbox(config)
    catalog = _get_pc_catalog()
    items   = list(catalog.search(collections=["soilgrids"], bbox=bbox).items())
    clay    = [i for i in items
               if "clay" in i.id.lower() and ("0-30" in i.id or "030" in i.id)]
    if not clay:
        clay = [i for i in items if "clay" in i.id.lower()]
    if not clay:
        raise RuntimeError("No SoilGrids clay items on PC")

    logger.info(f"  {len(clay)} item(s)")
    tmp        = Path(tempfile.mkdtemp(prefix="pc_soil_"))
    tile_paths = []
    for item in clay:
        key = next(
            (k for k in item.assets if "clay" in k.lower() or k in ["mean", "data"]),
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
        srcs      = [rasterio.open(p) for p in tile_paths]
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
    # PC SoilGrids stores values as g/kg * 10 — divide by 10 to get g/kg
    clay_gkg = np.where(valid, data / 10.0, nd)
    conv     = tmp / "soil_conv.tif"
    _save_layer(clay_gkg, transform, crs, conv, nodata=nd)
    _reproject_to_wgs84(conv, output_path,
                         resolution=config.get("resolution", _DEFAULT_RES),
                         resampling=Resampling.nearest)
    logger.info(f"  ✅ Soil (PC): {output_path.name}")
    return output_path


def _fetch_isric_point(lon: float, lat: float) -> Optional[float]:
    """
    Fetch clay content (g/kg) from SoilGrids v2 REST API.

    IMPORTANT — unit handling:
    The SoilGrids v2 REST API returns values ALREADY converted to the mapped
    unit (g/kg). The d_factor division has been applied server-side.
    Do NOT divide by d_factor again — that was the previous bug causing
    values like 350 g/kg to appear as 35 g/kg.

    Depth params: SoilGrids v2 uses individual depth labels, not ranges.
    Valid values: "0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"
    We query 0-5cm and 5-15cm and average them to represent topsoil.

    val=0 is valid (very sandy soils) — do NOT filter with val > 0.
    """
    url = (
        "https://rest.isric.org/soilgrids/v2.0/properties/query"
        f"?lon={lon:.4f}&lat={lat:.4f}"
        "&property=clay"
        "&depth=0-5cm&depth=5-15cm"
        "&value=mean"
    )
    try:
        r = requests.get(url, timeout=25, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()

        layers     = data.get("properties", {}).get("layers", [])
        clay_layer = next((l for l in layers if l.get("name") == "clay"), None)
        if clay_layer is None:
            return None

        depths = clay_layer.get("depths", [])
        values = []
        for depth_entry in depths:
            label    = depth_entry.get("label", "")
            mean_val = depth_entry.get("values", {}).get("mean")
            # Accept 0-5cm and 5-15cm; use `is not None` so sandy soils (0 g/kg) are valid
            if label in ("0-5cm", "5-15cm") and mean_val is not None:
                # REST API returns value already in g/kg — NO d_factor division needed
                values.append(float(mean_val))

        return float(np.mean(values)) if values else None

    except Exception as e:
        logger.debug(f"  ISRIC point ({lon:.2f},{lat:.2f}) failed: {e}")
        return None


def _fetch_soil_isric_fallback(config: dict, output_path: Path) -> Path:
    """
    Spatial ISRIC SoilGrids v2 fallback — samples a grid and interpolates.
    Returns real spatial variation. The constant-fill fallback is only used
    if all API calls fail completely.
    """
    from scipy.interpolate import griddata
    logger.info("── Fetching soil clay (ISRIC spatial grid fallback) ─────")

    west, south, east, north = _get_bbox(config)
    res  = config.get("resolution", _DEFAULT_RES)

    step = _ISRIC_GRID_STEP
    lons = np.arange(west  + step / 2, east,  step)
    lats = np.arange(south + step / 2, north, step)

    if len(lons) < 3:
        lons = np.linspace(west + 0.04, east - 0.04, 4)
    if len(lats) < 3:
        lats = np.linspace(south + 0.04, north - 0.04, 4)

    total = len(lons) * len(lats)
    logger.info(f"  Sampling {total} ISRIC points ({len(lons)}×{len(lats)} grid)")

    pts_lon, pts_lat, pts_val = [], [], []
    for lat_pt in lats:
        for lon_pt in lons:
            val = _fetch_isric_point(lon_pt, lat_pt)
            if val is not None:
                pts_lon.append(lon_pt)
                pts_lat.append(lat_pt)
                pts_val.append(val)

    logger.info(f"  {len(pts_val)}/{total} ISRIC points valid")

    out_w = max(int((east - west)  / res), 10)
    out_h = max(int((north - south) / res), 10)
    t     = from_bounds(west, south, east, north, out_w, out_h)
    col_c = np.linspace(west  + res / 2, east  - res / 2, out_w)
    row_c = np.linspace(north - res / 2, south + res / 2, out_h)
    gx, gy = np.meshgrid(col_c, row_c)

    if len(pts_val) >= 4:
        pts    = np.column_stack([pts_lon, pts_lat])
        interp = griddata(pts, pts_val, (gx, gy), method="cubic")
        nans   = np.isnan(interp)
        if nans.any():
            interp[nans] = griddata(pts, pts_val, (gx[nans], gy[nans]), method="linear")
        nans = np.isnan(interp)
        if nans.any():
            interp[nans] = griddata(pts, pts_val, (gx[nans], gy[nans]), method="nearest")
        clay_data = np.clip(interp, 0, 1000).astype(np.float32)
        logger.info(
            f"  ISRIC interpolated: range {clay_data.min():.0f}–{clay_data.max():.0f} g/kg "
            f"mean {clay_data.mean():.0f} g/kg"
        )
    elif len(pts_val) >= 1:
        mean_val  = float(np.mean(pts_val))
        clay_data = np.full((out_h, out_w), mean_val, dtype=np.float32)
        logger.warning(
            f"  ISRIC: only {len(pts_val)} point(s) — "
            f"uniform fill at {mean_val:.0f} g/kg"
        )
    else:
        clay_data = np.full((out_h, out_w), 280.0, dtype=np.float32)
        logger.warning(
            "  ISRIC: all points failed — using 280 g/kg constant. "
            "Check network access to rest.isric.org"
        )

    _save_layer(clay_data, t, "EPSG:4326", output_path)
    logger.info(f"  ✅ Soil (ISRIC spatial): {output_path.name}")
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Main orchestrator
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all_layers(config: dict, force: bool = False) -> Dict[str, Path]:
    paths, county = config["_paths"], config["county"]
    raw_dir = paths["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 55)
    logger.info(f"  PC FETCHER: {config['display_name'].upper()}")
    logger.info("=" * 55)

    boundary_path = paths["boundary"]
    if not boundary_path.exists() or force:
        try:
            fetch_boundary(config)
        except Exception as e:
            logger.error(f"[{county}] Boundary fetch failed: {e}")
    else:
        logger.info(f"[{county}] Boundary cached: {boundary_path.name}")

    fetched = {}

    def out(n):
        return raw_dir / f"{county}_{n}.tif"

    def cached(n):
        logger.info(f"  ✓ {n}: cached")
        fetched[n] = out(n)

    if not force and out("elevation").exists():
        cached("elevation")
    else:
        try:
            fetch_elevation(config, out("elevation"))
            fetched["elevation"] = out("elevation")
        except Exception as e:
            logger.error(f"  Elevation: {e}")

    if not force and out("slope").exists():
        cached("slope")
    else:
        try:
            fetch_slope(config, fetched.get("elevation") or out("elevation"), out("slope"))
            fetched["slope"] = out("slope")
        except Exception as e:
            logger.warning(f"  Slope: {e}")

    if not force and out("rainfall").exists():
        cached("rainfall")
    else:
        try:
            fetch_rainfall(config, out("rainfall"))
            fetched["rainfall"] = out("rainfall")
        except Exception as e:
            logger.warning(f"  Rainfall: {e}")

    if not force and out("temperature").exists():
        cached("temperature")
    else:
        try:
            fetch_temperature(config, out("temperature"))
            fetched["temperature"] = out("temperature")
        except Exception as e:
            logger.warning(f"  Temperature: {e}")

    if not force and out("soil").exists():
        cached("soil")
    else:
        try:
            fetch_soil(config, out("soil"))
            fetched["soil"] = out("soil")
        except Exception as e:
            logger.warning(f"  PC soil: {e} — trying ISRIC spatial grid...")
            try:
                _fetch_soil_isric_fallback(config, out("soil"))
                fetched["soil"] = out("soil")
            except Exception as e2:
                logger.warning(f"  ISRIC spatial: {e2}")

    missing = set(config["layers"]) - set(fetched)
    logger.info(f"\n  Fetched : {sorted(fetched)}")
    if missing:
        logger.warning(f"  Missing : {sorted(missing)}")
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
    logger.info(f"County: {config['display_name']}")
    logger.info(f"Bbox:   {_get_bbox(config)}")
    logger.info(f"Boundary cached: {config['_paths']['boundary'].exists()}")
    logger.info(f"Layers cached:   {layers_are_cached(config)}")
    for name in config["layers"]:
        raw = config["_paths"]["raw_dir"] / f"{config['county']}_{name}.tif"
        logger.info(f"  {name}: {'✅ cached' if raw.exists() else '❌ missing'}")

    if "--fetch" in sys.argv:
        result = fetch_all_layers(config)
        print(f"\nFetched {len(result)} layers:")
        for n, p in result.items():
            print(f"  {n}: {p.name} ({p.stat().st_size // 1024} KB)")