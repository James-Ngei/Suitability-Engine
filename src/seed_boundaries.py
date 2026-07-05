"""
seed_boundaries.py
------------------
One-off maintenance: generate the REAL polygon boundary for every county and
upload it to Cloudflare R2, overwriting any stale bbox-rectangle fallback.

Source of truth is the local GADM file (gadm41_KEN.gpkg, ADM level 1 = the 47
counties) — offline, deterministic, no Overpass rate limits. If a county has no
GADM match, it falls back to a retrying OSM/Overpass fetch.

Why: production /boundary-geojson self-heals a stale bbox by (1) pulling the
boundary from R2, then (2) falling back to a LIVE Overpass fetch at request
time. Overpass rate-limits aggressively, so tier (2) is a coin flip — that's why
only some counties render a real outline while others show the bbox rectangle.
Seeding real polygons into R2 makes tier (1) succeed every time; the live fetch
never runs in production.

Run this LOCALLY (the 46 MB GADM file is gitignored, not on the server).

Setup: same R2 env vars as upload_to_r2.py (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
R2_SECRET_ACCESS_KEY, R2_BUCKET), read from the project-root .env.
The GADM file path defaults to the repo root; override with GADM_PATH.

Usage:
  python src/seed_boundaries.py                  # all counties
  python src/seed_boundaries.py --county kitui   # one or more (repeatable)
  python src/seed_boundaries.py --dry-run        # generate + validate, skip upload
  python src/seed_boundaries.py --force          # regenerate even if local is already real
  python src/seed_boundaries.py --retries 5      # OSM fallback attempts (default 3)
  python src/seed_boundaries.py --sleep 8        # seconds between OSM attempts (default 5)
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from config import load_config, list_counties
from pc_fetcher import fetch_boundary, boundary_from_gadm, _is_bbox_rectangle
from upload_to_r2 import (
    _r2_client, _bucket, _r2_key, _upload_file, _load_dotenv,
)

_load_dotenv()


def _read_boundary(path: Path):
    import geopandas as gpd
    return gpd.read_file(path)


def _fetch_real_boundary(config: dict, retries: int, sleep_s: int) -> bool:
    """
    Fetch a real (non-bbox) boundary for a county, retrying to ride out Overpass
    throttling. Returns True if the on-disk boundary is a real polygon.
    """
    county      = config["county"]
    output_path = config["_paths"]["boundary"]

    for attempt in range(1, retries + 1):
        # Start clean so a failed attempt can't leave a stale bbox that a later
        # read mistakes for success.
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

        print(f"  [{county}] OSM fetch attempt {attempt}/{retries} …")
        try:
            fetch_boundary(config)
        except Exception as e:
            print(f"    ⚠️  fetch_boundary raised: {type(e).__name__}: {e}")

        if output_path.exists():
            try:
                if not _is_bbox_rectangle(_read_boundary(output_path)):
                    print(f"    ✅ real polygon fetched")
                    return True
            except Exception as e:
                print(f"    ⚠️  could not read fetched boundary: {e}")
            print(f"    ✗ got bbox rectangle (Overpass throttled/unreachable)")

        if attempt < retries:
            time.sleep(sleep_s)

    return False


def seed_county(county: str, dry_run: bool, force: bool,
                retries: int, sleep_s: int) -> dict:
    result = {"county": county, "status": "failed"}
    try:
        config = load_config(county)
    except FileNotFoundError as e:
        print(f"❌  {e}")
        return result

    output_path = config["_paths"]["boundary"]
    country     = config.get("country", "kenya").lower()

    print()
    print("=" * 60)
    print(f"  {config['display_name']}  ({county})")
    print("=" * 60)

    # Skip regeneration if we already have a real polygon locally and aren't forcing.
    have_real = False
    if output_path.exists() and not force:
        try:
            have_real = not _is_bbox_rectangle(_read_boundary(output_path))
        except Exception:
            have_real = False
        if have_real:
            print(f"  local boundary already a real polygon — skipping regeneration")

    # 1) GADM (authoritative, offline). 2) OSM/Overpass fallback with retries.
    if not have_real:
        gadm = boundary_from_gadm(config)
        if gadm is not None and len(gadm) > 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.unlink(missing_ok=True)
            gadm.to_file(output_path, driver="GPKG")
            print(f"  ✅ boundary from GADM")
            have_real = True
        else:
            print(f"  no GADM match — falling back to OSM/Overpass")
            have_real = _fetch_real_boundary(config, retries, sleep_s)

    if not have_real:
        print(f"  ❌ could not obtain a real boundary for '{county}' — NOT uploading")
        result["status"] = "no_real_boundary"
        return result

    if dry_run:
        print(f"  [dry-run] would upload real boundary → "
              f"{country}/{county}/boundaries/{output_path.name}")
        result["status"] = "dry_run"
        return result

    client = _r2_client()
    bucket = _bucket()
    key    = _r2_key(country, county, "boundaries", output_path.name)
    if _upload_file(client, output_path, key, bucket):
        result["status"] = "uploaded"
    else:
        result["status"] = "upload_failed"
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Generate real county boundaries from GADM (OSM fallback) and seed them to R2."
    )
    parser.add_argument("--county", action="append", metavar="COUNTY_ID",
                        help="County to seed (repeatable). Default: all counties.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + validate, but do not upload to R2.")
    parser.add_argument("--force", action="store_true",
                        help="Refetch from OSM even if the local boundary is already real.")
    parser.add_argument("--retries", type=int, default=3,
                        help="OSM fetch attempts per county (default 3).")
    parser.add_argument("--sleep", type=int, default=5,
                        help="Seconds to wait between OSM attempts (default 5).")
    args = parser.parse_args()

    counties = args.county or list_counties()
    print(f"Seeding boundaries for {len(counties)} county(ies): {counties}")

    results = []
    for county in counties:
        results.append(
            seed_county(county, args.dry_run, args.force, args.retries, args.sleep)
        )

    print()
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    icon = {
        "uploaded": "✅", "dry_run": "🔎", "no_real_boundary": "❌",
        "upload_failed": "⚠️", "failed": "❌",
    }
    for r in results:
        print(f"  {icon.get(r['status'], '?')} {r['county']:20} {r['status']}")

    bad = [r for r in results if r["status"] in ("no_real_boundary", "upload_failed", "failed")]
    if bad:
        print(f"\n  {len(bad)} county(ies) still need attention. "
              f"Re-run for them (Overpass throttling is transient):")
        print(f"    python src/seed_boundaries.py "
              + " ".join(f"--county {r['county']}" for r in bad))
        sys.exit(1)

    if any(r["status"] == "dry_run" for r in results):
        print(f"\n  Dry run OK — all {len(results)} county(ies) produced real boundaries. "
              f"Re-run without --dry-run to upload to R2.")
    else:
        print(f"\n  All {len(results)} county(ies) have real boundaries in R2. "
              f"Production /boundary-geojson will now self-heal deterministically.")


if __name__ == "__main__":
    main()
