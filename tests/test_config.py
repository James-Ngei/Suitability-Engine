"""
Unit tests for the config loading / merging layer in `config.py`.

These run purely against the JSON files committed under `config/` and need no
raster data, so they exercise the county/crop split described in design.md §7
(Configuration System) and guard the invariants the pipeline relies on:
weights sum to 1.0 and every declared criterion has a valid normalization type.
"""

import pytest

from config import (
    list_counties,
    list_crops,
    load_county_config,
    load_crop_config,
    load_config,
)
from normalize import FUZZY_FUNCTIONS


def test_counties_are_discovered():
    counties = list_counties()
    assert len(counties) >= 40
    assert "baringo" in counties


def test_crops_are_discovered():
    crops = list_crops()
    assert "cotton" in crops
    assert len(crops) >= 5


@pytest.mark.parametrize("crop", list_crops())
def test_every_crop_weights_sum_to_one(crop):
    cfg = load_crop_config(crop)
    total = sum(cfg["weights"].values())
    assert abs(total - 1.0) < 1e-3, f"{crop} weights sum to {total}"


@pytest.mark.parametrize("crop", list_crops())
def test_weights_and_normalization_cover_same_criteria(crop):
    cfg = load_crop_config(crop)
    assert set(cfg["weights"]) == set(cfg["normalization"]), crop


@pytest.mark.parametrize("crop", list_crops())
def test_every_normalization_type_is_known(crop):
    cfg = load_crop_config(crop)
    for name, norm in cfg["normalization"].items():
        assert norm["type"] in FUZZY_FUNCTIONS, f"{crop}.{name} -> {norm['type']}"


def test_load_config_merges_county_and_crop():
    cfg = load_config("baringo", "cotton")
    assert cfg["county"] == "baringo"
    assert cfg["crop_id"] == "cotton"
    # County geography + crop agronomy line up on the same criteria set.
    assert set(cfg["weights"]) == set(cfg["layers"])
    assert "_paths" in cfg


def test_missing_county_raises():
    with pytest.raises(FileNotFoundError):
        load_county_config("atlantis")


def test_missing_crop_raises():
    with pytest.raises(FileNotFoundError):
        load_crop_config("unobtainium")
