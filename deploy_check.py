#!/usr/bin/env python3
"""
deploy_check.py
---------------
Run this locally before pushing to Render to catch any deployment issues.

    python deploy_check.py

All checks should show ✅ before deploying. The engine fetches data on demand
from open sources (Planetary Computer / NASA POWER / OSM) and caches prepared
layers to Cloudflare R2, so there is no manual data-upload step to verify.
"""

import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

errors   = []
warnings = []

def check(label, condition, error_msg, warn=False):
    if condition:
        print(f"  {PASS}  {label}")
    else:
        symbol = WARN if warn else FAIL
        print(f"  {symbol}  {label}")
        (warnings if warn else errors).append(error_msg)


print()
print("=" * 60)
print("  DEPLOYMENT READINESS CHECK")
print("=" * 60)
print()

# ── 1. Repo structure ──────────────────────────────────────────────────────
print("── Repo structure ───────────────────────────────────────────")

check("src/api.py exists",        (ROOT / "src" / "api.py").exists(),         "src/api.py missing")
check("src/config.py exists",     (ROOT / "src" / "config.py").exists(),      "src/config.py missing")
check("src/pc_fetcher.py exists", (ROOT / "src" / "pc_fetcher.py").exists(),  "src/pc_fetcher.py missing (on-demand data fetch)")
check("config/counties/ dir",     (ROOT / "config" / "counties").is_dir(),    "config/counties/ directory missing")
check("config/crops/ dir",        (ROOT / "config" / "crops").is_dir(),       "config/crops/ directory missing")
check("render.yaml exists",       (ROOT / "render.yaml").exists(),            "render.yaml missing")
check("requirements.txt exists",  (ROOT / "requirements.txt").exists(),       "requirements.txt missing")
print()

# ── 2. render.yaml ────────────────────────────────────────────────────────
print("── render.yaml ──────────────────────────────────────────────")
render_path = ROOT / "render.yaml"
if render_path.exists():
    content = render_path.read_text()
    check("startCommand uses src.api:app",
          "src.api:app" in content,
          "render.yaml startCommand should be: uvicorn src.api:app --host 0.0.0.0 --port $PORT")
    check("healthCheckPath is /ping",
          "/ping" in content,
          "render.yaml healthCheckPath should be /ping (returns instantly on startup)")
    check("SUITABILITY_DATA_DIR set",
          "SUITABILITY_DATA_DIR" in content,
          "SUITABILITY_DATA_DIR env var missing from render.yaml")
    check("ACTIVE_COUNTY set",
          "ACTIVE_COUNTY" in content,
          "ACTIVE_COUNTY env var missing from render.yaml")
    check("ACTIVE_CROP set",
          "ACTIVE_CROP" in content,
          "ACTIVE_CROP env var missing from render.yaml")
    check("R2_BUCKET set",
          "R2_BUCKET" in content,
          "R2_BUCKET env var missing from render.yaml")
    check("secret credentials marked sync:false",
          content.count("sync: false") >= 2,
          "R2 credentials + GROQ_API_KEY should be marked sync:false (never stored in git)")
print()

# ── 3. requirements.txt ───────────────────────────────────────────────────
print("── requirements.txt ─────────────────────────────────────────")
req_path = ROOT / "requirements.txt"
if req_path.exists():
    reqs = req_path.read_text().lower()
    for pkg in ["fastapi", "uvicorn", "rasterio", "numpy", "boto3",
                "geopandas", "pillow", "pydantic",
                "planetary-computer", "pystac-client"]:
        check(f"{pkg} listed", pkg in reqs, f"{pkg} missing from requirements.txt")
print()

# ── 4. County configs (geography) ─────────────────────────────────────────
print("── County configs (config/counties/) ───────────────────────")
counties_dir = ROOT / "config" / "counties"
if counties_dir.is_dir():
    jsons = list(counties_dir.glob("*.json"))
    check(f"{len(jsons)} county config(s) found", len(jsons) > 0, "No county JSON configs found")

    required_keys = ["county", "display_name", "layers", "map_center", "map_zoom"]
    for jpath in jsons:
        try:
            cfg = json.loads(jpath.read_text())
            missing = [k for k in required_keys if k not in cfg]
            check(f"{jpath.name} valid",
                  len(missing) == 0,
                  f"{jpath.name} missing keys: {missing}")
        except json.JSONDecodeError as e:
            check(f"{jpath.name} valid JSON", False, str(e))
print()

# ── 5. Crop configs (agronomy) ────────────────────────────────────────────
print("── Crop configs (config/crops/) ─────────────────────────────")
crops_dir = ROOT / "config" / "crops"
VALID_NORM = {"trapezoidal", "gaussian", "linear_descending"}
if crops_dir.is_dir():
    jsons = list(crops_dir.glob("*.json"))
    check(f"{len(jsons)} crop config(s) found", len(jsons) > 0, "No crop JSON configs found")

    required_keys = ["crop_id", "display_name", "normalization", "weights", "criteria_info"]
    for jpath in jsons:
        try:
            cfg = json.loads(jpath.read_text())
            missing = [k for k in required_keys if k not in cfg]
            check(f"{jpath.name} valid",
                  len(missing) == 0,
                  f"{jpath.name} missing keys: {missing}")

            w_sum = sum(cfg.get("weights", {}).values())
            check(f"{jpath.name} weights sum to 1.0",
                  abs(w_sum - 1.0) < 0.01,
                  f"{jpath.name} weights sum to {w_sum:.3f}, not 1.0")

            bad = [f"{n}={c.get('type')}" for n, c in cfg.get("normalization", {}).items()
                   if c.get("type") not in VALID_NORM]
            check(f"{jpath.name} normalization types valid",
                  len(bad) == 0,
                  f"{jpath.name} has invalid normalization types: {bad}")
        except json.JSONDecodeError as e:
            check(f"{jpath.name} valid JSON", False, str(e))
print()

# ── 6. src/config.py path logic ───────────────────────────────────────────
print("── src/config.py path logic ─────────────────────────────────")
config_py = ROOT / "src" / "config.py"
if config_py.exists():
    src = config_py.read_text()
    check("CONFIG_DIR resolved from __file__",
          "__file__" in src and "CONFIG_DIR" in src,
          "config.py must resolve CONFIG_DIR relative to __file__, not the working directory")
    check("ACTIVE_COUNTY env var supported",
          "ACTIVE_COUNTY" in src,
          "config.py must read ACTIVE_COUNTY from environment")
    check("ACTIVE_CROP env var supported",
          "ACTIVE_CROP" in src,
          "config.py must read ACTIVE_CROP from environment")
    check("SUITABILITY_DATA_DIR env var supported",
          "SUITABILITY_DATA_DIR" in src,
          "config.py must read SUITABILITY_DATA_DIR from environment")
print()

# ── 7. .gitignore ─────────────────────────────────────────────────────────
print("── .gitignore ───────────────────────────────────────────────")
gitignore = ROOT / ".gitignore"
if gitignore.exists():
    gi = gitignore.read_text()
    check("data/ ignored",   "data/" in gi,   "data/ should be in .gitignore (rasters not committed)", warn=True)
    check("venv/ ignored",   "venv/" in gi,   "venv/ should be in .gitignore", warn=True)
    check("*.tif ignored",   "*.tif" in gi,   "*.tif should be in .gitignore", warn=True)
else:
    check(".gitignore exists", False, ".gitignore missing", warn=True)
print()

# ── 8. R2 sync + fetch coverage in api.py ─────────────────────────────────
print("── R2 sync / fetch coverage in api.py ───────────────────────")
api_py = ROOT / "src" / "api.py"
if api_py.exists():
    api_src = api_py.read_text()
    check("sync_county_from_r2 present",   "sync_county_from_r2" in api_src,  "R2 sync function missing")
    check("PC fetch fallback present",     "fetch_all_layers"    in api_src,  "on-demand PC fetch fallback missing")
    check("normalized/ synced",            "normalized/"         in api_src,  "normalized/ not in R2 sync map")
    check("boundaries/ synced",            "boundaries/"         in api_src,  "boundaries/ not in R2 sync map")
    check("preprocessed/ synced",          "preprocessed/"       in api_src,  "preprocessed/ not in R2 sync map")
    check("/admin/reload endpoint exists", "/admin/reload"       in api_src,  "/admin/reload endpoint missing")
    check("/admin/load-county endpoint",   "/admin/load-county"  in api_src,  "/admin/load-county endpoint missing")
print()

# ── Summary ────────────────────────────────────────────────────────────────
print("=" * 60)
if not errors and not warnings:
    print(f"  {PASS}  ALL CHECKS PASSED — ready to deploy")
else:
    if errors:
        print(f"  {FAIL}  {len(errors)} error(s) must be fixed before deploying:")
        for e in errors:
            print(f"        • {e}")
    if warnings:
        print(f"  {WARN}  {len(warnings)} warning(s) to review:")
        for w in warnings:
            print(f"        • {w}")
print("=" * 60)
print()

sys.exit(1 if errors else 0)
