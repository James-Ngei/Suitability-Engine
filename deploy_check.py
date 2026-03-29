#!/usr/bin/env python3
"""
deploy_check.py
---------------
Run this locally before pushing to Render to catch any deployment issues.

    python deploy_check.py

All checks should show ✅ before deploying.
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

check("src/api.py exists",       (ROOT / "src" / "api.py").exists(),      "src/api.py missing")
check("src/config.py exists",    (ROOT / "src" / "config.py").exists(),   "src/config.py missing")
check("config/ directory",       (ROOT / "config").is_dir(),              "config/ directory missing")
check("config/kitui.json",       (ROOT / "config" / "kitui.json").exists(), "config/kitui.json missing")
check("render.yaml exists",      (ROOT / "render.yaml").exists(),         "render.yaml missing")
check("requirements.txt exists", (ROOT / "requirements.txt").exists(),    "requirements.txt missing")
print()

# ── 2. render.yaml ────────────────────────────────────────────────────────
print("── render.yaml ──────────────────────────────────────────────")
render_path = ROOT / "render.yaml"
if render_path.exists():
    content = render_path.read_text()
    check("startCommand uses src.api:app",
          "src.api:app" in content,
          "render.yaml startCommand should be: uvicorn src.api:app --host 0.0.0.0 --port $PORT")
    check("SUITABILITY_DATA_DIR set",
          "SUITABILITY_DATA_DIR" in content,
          "SUITABILITY_DATA_DIR env var missing from render.yaml")
    check("ACTIVE_COUNTY set",
          "ACTIVE_COUNTY" in content,
          "ACTIVE_COUNTY env var missing from render.yaml")
    check("AWS_S3_BUCKET set",
          "AWS_S3_BUCKET" in content,
          "AWS_S3_BUCKET env var missing from render.yaml")
    check("AWS credentials marked sync:false",
          content.count("sync: false") >= 2,
          "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY should be sync:false (secrets)")
print()

# ── 3. requirements.txt ───────────────────────────────────────────────────
print("── requirements.txt ─────────────────────────────────────────")
req_path = ROOT / "requirements.txt"
if req_path.exists():
    reqs = req_path.read_text().lower()
    for pkg in ["fastapi", "uvicorn", "rasterio", "numpy", "boto3",
                "geopandas", "pillow", "pydantic"]:
        check(f"{pkg} listed", pkg in reqs, f"{pkg} missing from requirements.txt")
print()

# ── 4. config/ JSON files ─────────────────────────────────────────────────
print("── County configs ───────────────────────────────────────────")
config_dir = ROOT / "config"
if config_dir.is_dir():
    jsons = list(config_dir.glob("*.json"))
    check(f"{len(jsons)} county config(s) found", len(jsons) > 0, "No county JSON configs found")

    for jpath in jsons:
        try:
            cfg = json.loads(jpath.read_text())
            required_keys = ["county", "display_name", "crop", "layers",
                             "normalization", "weights", "criteria_info",
                             "map_center", "map_zoom"]
            missing = [k for k in required_keys if k not in cfg]
            check(f"{jpath.name} valid",
                  len(missing) == 0,
                  f"{jpath.name} missing keys: {missing}")

            w_sum = sum(cfg.get("weights", {}).values())
            check(f"{jpath.name} weights sum to 1.0",
                  abs(w_sum - 1.0) < 0.01,
                  f"{jpath.name} weights sum to {w_sum:.3f}, not 1.0")
        except json.JSONDecodeError as e:
            check(f"{jpath.name} valid JSON", False, str(e))
print()

# ── 5. src/config.py path logic ───────────────────────────────────────────
print("── src/config.py path logic ─────────────────────────────────")
config_py = ROOT / "src" / "config.py"
if config_py.exists():
    src = config_py.read_text()
    check("CONFIG_DIR resolved from __file__",
          "__file__" in src and "CONFIG_DIR" in src,
          "config.py must resolve CONFIG_DIR relative to __file__, not BASE_DIR")
    check("ACTIVE_COUNTY env var supported",
          'ACTIVE_COUNTY' in src,
          "config.py must read ACTIVE_COUNTY from environment")
    check("SUITABILITY_DATA_DIR env var supported",
          'SUITABILITY_DATA_DIR' in src,
          "config.py must read SUITABILITY_DATA_DIR from environment")
print()

# ── 6. .gitignore ─────────────────────────────────────────────────────────
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

# ── 7. S3 sync map ────────────────────────────────────────────────────────
print("── S3 sync coverage in api.py ───────────────────────────────")
api_py = ROOT / "src" / "api.py"
if api_py.exists():
    api_src = api_py.read_text()
    check("normalized/ synced",   '"normalized/"'   in api_src or "normalized/" in api_src,  "normalized/ not in S3 sync map")
    check("boundary/ synced",     '"boundary/"'     in api_src or "boundary/" in api_src,    "boundary/ not in S3 sync map")
    check("constraints/ synced",  '"constraints/"'  in api_src or "constraints/" in api_src, "constraints/ not in S3 sync map")
    check("preprocessed/ synced", '"preprocessed/"' in api_src or "preprocessed/" in api_src,"preprocessed/ not in S3 sync map")
    check("/admin/reload endpoint exists", "/admin/reload" in api_src, "/admin/reload endpoint missing")
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