"""
Unit tests for the weighted-overlay engine in `suitability.py`.

The engine reads and writes GeoTIFFs, so each test writes small in-memory
rasters to a temp directory using rasterio, runs the operation, and reads the
result back. The weighted-overlay arithmetic case mirrors the worked example
in evaluation.md §3.4.
"""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from suitability import SuitabilityEngine


def _write_raster(path, array, nodata=0):
    """Write a float32 single-band GeoTIFF for use as engine input."""
    array = np.asarray(array, dtype=np.float32)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": from_origin(0, array.shape[0], 1, 1),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array, 1)
    return path


# ── Weighted overlay arithmetic ────────────────────────────────────────────────

def test_weighted_overlay_equal_weights(tmp_path):
    # evaluation.md §3.4: layer_a=80, layer_b=60, weights 0.5/0.5 → 70 everywhere.
    a = _write_raster(tmp_path / "a.tif", np.full((5, 5), 80.0))
    b = _write_raster(tmp_path / "b.tif", np.full((5, 5), 60.0))

    engine = SuitabilityEngine(tmp_path / "out")
    out = engine.calculate_suitability(
        {"a": a, "b": b}, weights={"a": 0.5, "b": 0.5}, output_name="s.tif"
    )

    with rasterio.open(out) as src:
        data = src.read(1)
    assert np.allclose(data, 70.0)


def test_weighted_overlay_unequal_weights(tmp_path):
    a = _write_raster(tmp_path / "a.tif", np.full((4, 4), 90.0))
    b = _write_raster(tmp_path / "b.tif", np.full((4, 4), 30.0))

    engine = SuitabilityEngine(tmp_path / "out")
    out = engine.calculate_suitability(
        {"a": a, "b": b}, weights={"a": 0.75, "b": 0.25}, output_name="s.tif"
    )

    with rasterio.open(out) as src:
        data = src.read(1)
    # 0.75*90 + 0.25*30 = 67.5 + 7.5 = 75
    assert np.allclose(data, 75.0)


def test_weighted_overlay_clamps_to_100(tmp_path):
    a = _write_raster(tmp_path / "a.tif", np.full((3, 3), 100.0))
    b = _write_raster(tmp_path / "b.tif", np.full((3, 3), 100.0))

    engine = SuitabilityEngine(tmp_path / "out")
    out = engine.calculate_suitability(
        # Weights intentionally > 1 to force values above 100 before clamping.
        {"a": a, "b": b}, weights={"a": 0.8, "b": 0.8}, output_name="s.tif"
    )

    with rasterio.open(out) as src:
        data = src.read(1)
    assert data.max() <= 100.0


def test_no_valid_layers_raises(tmp_path):
    engine = SuitabilityEngine(tmp_path / "out")
    with pytest.raises(RuntimeError):
        engine.calculate_suitability({}, weights={})


# ── Classification ─────────────────────────────────────────────────────────────

def test_classify_assigns_expected_classes(tmp_path):
    # 85→Highly(1), 65→Moderately(2), 45→Marginally(3), 25→Not(4)
    p = _write_raster(tmp_path / "s.tif", np.array([[85, 65, 45, 25]]))
    engine = SuitabilityEngine(tmp_path)
    out = engine.classify_suitability(p)

    with rasterio.open(out) as src:
        classified = src.read(1)
    assert list(classified[0]) == [1, 2, 3, 4]


def test_classify_boundary_values(tmp_path):
    # Thresholds use [lo, hi): 70→Highly, exactly 50→Moderately, 30→Marginally.
    p = _write_raster(tmp_path / "s.tif", np.array([[70, 50, 30]]))
    engine = SuitabilityEngine(tmp_path)
    out = engine.classify_suitability(p)

    with rasterio.open(out) as src:
        classified = src.read(1)
    assert list(classified[0]) == [1, 2, 3]


# ── Statistics ─────────────────────────────────────────────────────────────────

def test_statistics_counts_and_percentages(tmp_path):
    arr = np.array([
        [80, 80, 80, 80],   # highly suitable (>=70)
        [60, 60, 55, 40],   # 3 moderately (50-70), 1 marginal
        [40, 35, 20, 15],   # 2 marginal (30-50), 2 not-suitable (<30)
        [75, 0, 0, 10],     # 1 highly, 2 zero (nodata), 1 not-suitable
    ])
    p = _write_raster(tmp_path / "s.tif", arr)
    engine = SuitabilityEngine(tmp_path)
    stats = engine.generate_statistics(p)

    assert stats["total_pixels"] == 16
    assert stats["zero_pixels"] == 2
    assert stats["valid_pixels"] == 14  # data > 0

    # Highly suitable (>=70): four 80s + one 75 = 5 pixels
    assert np.isclose(stats["highly_suitable_pct"], 5 / 16 * 100)
    # Moderately (50-70): 60, 60, 55 = 3 pixels
    assert np.isclose(stats["moderately_suitable_pct"], 3 / 16 * 100)


def test_statistics_empty_raster_is_safe(tmp_path):
    p = _write_raster(tmp_path / "s.tif", np.zeros((4, 4)))
    engine = SuitabilityEngine(tmp_path)
    stats = engine.generate_statistics(p)

    assert stats["valid_pixels"] == 0
    assert stats["mean"] == 0  # no division-by-zero on an all-nodata raster
