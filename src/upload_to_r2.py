"""
upload_to_r2.py
---------------
Run this LOCALLY after completing the pipeline for a county.
Uploads normalized layers, boundary, and constraint mask to Cloudflare R2.

Render then pulls from R2 on startup instead of re-running the pipeline.

Setup (one-time):
  1. Create a Cloudflare account (free)
  2. Go to R2 → Create bucket → name it "suitability-engine"
  3. Go to R2 → Manage R2 API tokens → Create token (read+write)
  4. Copy your Account ID from the R2 dashboard URL
  5. Add to your local .env:
       R2_ACCOUNT_ID=your_account_id
       R2_ACCESS_KEY_ID=your_key_id
       R2_SECRET_ACCESS_KEY=your_secret
       R2_BUCKET=suitability-engine

Usage:
  python src/upload_to_r2.py --county kitui
  python src/upload_to_r2.py --county kitui --county bungoma
  python src/upload_to_r2.py --all          # upload all counties that have normalized layers
  python src/upload_to_r2.py --county kitui --dry-run  # preview without uploading

R2 bucket layout produced:
  <bucket>/
    <country>/           e.g. kenya/
      <county>/          e.g. kitui/
        normalized/      normalized_*.tif
        boundaries/      <county>_boundary.gpkg
        preprocessed/    <county>_constraints_mask.tif
"""

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.append(str(Path(__file__).parent))
from config import load_config, list_counties, get_active_county


# ── .env loader ───────────────────────────────────────────────────────────────

def _load_dotenv():
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()


# ── R2 client ─────────────────────────────────────────────────────────────────

def _r2_client():
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")

    missing = [k for k, v in {
        "R2_ACCOUNT_ID":     account_id,
        "R2_ACCESS_KEY_ID":  access_key,
        "R2_SECRET_ACCESS_KEY": secret_key,
    }.items() if not v]

    if missing:
        print(f"❌  Missing environment variables: {missing}")
        print(f"    Add them to your .env file at the project root.")
        sys.exit(1)

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _bucket() -> str:
    b = os.environ.get("R2_BUCKET", "suitability-engine")
    return b


# ── Upload helpers ─────────────────────────────────────────────────────────────

def _r2_key(country: str, county: str, subfolder: str, filename: str) -> str:
    """Build the R2 object key.  e.g. kenya/kitui/normalized/normalized_elevation.tif"""
    return f"{country}/{county}/{subfolder}/{filename}"


def _upload_file(client, local_path: Path, r2_key: str,
                 bucket: str, dry_run: bool = False) -> bool:
    """Upload a single file. Returns True on success."""
    size_kb = local_path.stat().st_size // 1024
    if dry_run:
        print(f"  [dry-run] would upload: {local_path.name} ({size_kb} KB) → {r2_key}")
        return True
    try:
        client.upload_file(str(local_path), bucket, r2_key)
        print(f"  ↑ {local_path.name} ({size_kb} KB) → {r2_key}")
        return True
    except ClientError as e:
        print(f"  ❌ Failed to upload {local_path.name}: {e}")
        return False


# ── Per-county upload ──────────────────────────────────────────────────────────

def upload_county(county: str, dry_run: bool = False,
                  include_raw: bool = False) -> dict:
    """
    Upload all pipeline outputs for a county to R2.

    What gets uploaded:
      normalized/   ← always (these are what Render needs)
      boundaries/   ← always (boundary polygon)
      preprocessed/ ← constraint mask only (small, needed for analysis)
      raw/          ← only if include_raw=True (large, ~250MB, optional)

    Returns summary dict.
    """
    try:
        config = load_config(county)
    except FileNotFoundError as e:
        print(f"❌  {e}")
        return {"county": county, "uploaded": 0, "failed": 0, "skipped": 0}

    paths   = config["_paths"]
    country = config.get("country", "kenya").lower()
    client  = _r2_client()
    bucket  = _bucket()

    print()
    print("=" * 55)
    print(f"  Uploading: {config['display_name']} → R2:{bucket}/{country}/{county}/")
    print("=" * 55)

    uploaded = 0
    failed   = 0
    skipped  = 0

    # ── Normalized layers ─────────────────────────────────────────────────────
    print("\n── Normalized layers ─────────────────────────────────────")
    normalized_found = False
    for name, path in paths["normalized_layers"].items():
        if not path.exists():
            print(f"  ⚠️  Missing: {path.name} — run normalize.py first")
            skipped += 1
            continue
        normalized_found = True
        key = _r2_key(country, county, "normalized", path.name)
        ok  = _upload_file(client, path, key, bucket, dry_run)
        uploaded += 1 if ok else 0
        failed   += 0 if ok else 1

    if not normalized_found:
        print(f"  ❌ No normalized layers found for '{county}'.")
        print(f"     Run the full pipeline first:")
        print(f"       python src/pc_fetcher.py --fetch")
        print(f"       python src/preprocess.py")
        print(f"       python src/realign_to_boundary.py")
        print(f"       python src/normalize.py")
        print(f"       python src/clip_to_boundary.py")

    # ── Boundary ──────────────────────────────────────────────────────────────
    print("\n── Boundary ──────────────────────────────────────────────")
    boundary = paths["boundary"]
    if boundary.exists():
        key = _r2_key(country, county, "boundaries", boundary.name)
        ok  = _upload_file(client, boundary, key, bucket, dry_run)
        uploaded += 1 if ok else 0
        failed   += 0 if ok else 1
    else:
        print(f"  ⚠️  Boundary not found: {boundary.name}")
        skipped += 1

    # ── Constraint mask ───────────────────────────────────────────────────────
    print("\n── Constraint mask ───────────────────────────────────────")
    mask = paths["constraint_mask"]
    if mask.exists():
        key = _r2_key(country, county, "preprocessed", mask.name)
        ok  = _upload_file(client, mask, key, bucket, dry_run)
        uploaded += 1 if ok else 0
        failed   += 0 if ok else 1
    else:
        print(f"  ⚠️  Constraint mask not found: {mask.name}")
        skipped += 1

    # ── Raw layers (optional) ─────────────────────────────────────────────────
    if include_raw:
        print("\n── Raw layers ────────────────────────────────────────────")
        raw_dir = paths["raw_dir"]
        raw_files = list(raw_dir.glob(f"{county}_*.tif")) if raw_dir.exists() else []
        if raw_files:
            for path in raw_files:
                key = _r2_key(country, county, "raw", path.name)
                ok  = _upload_file(client, path, key, bucket, dry_run)
                uploaded += 1 if ok else 0
                failed   += 0 if ok else 1
        else:
            print(f"  ⚠️  No raw files found in {raw_dir}")

    print()
    print(f"  Summary: {uploaded} uploaded, {failed} failed, {skipped} skipped")
    return {"county": county, "uploaded": uploaded, "failed": failed, "skipped": skipped}


# ── List what's in R2 for a county ────────────────────────────────────────────

def list_r2_county(county: str):
    """Show what's already in R2 for a county."""
    try:
        config = load_config(county)
    except FileNotFoundError as e:
        print(f"❌ {e}"); return

    country = config.get("country", "kenya").lower()
    client  = _r2_client()
    bucket  = _bucket()
    prefix  = f"{country}/{county}/"

    print(f"\nR2:{bucket}/{prefix}")
    try:
        paginator = client.get_paginator("list_objects_v2")
        pages     = paginator.paginate(Bucket=bucket, Prefix=prefix)
        total = 0
        for page in pages:
            for obj in page.get("Contents", []):
                kb = obj["Size"] // 1024
                print(f"  {obj['Key'].replace(prefix,'')}  ({kb} KB)  {obj['LastModified'].strftime('%Y-%m-%d %H:%M')}")
                total += obj["Size"]
        if total == 0:
            print("  (empty)")
        else:
            print(f"\n  Total: {total // (1024*1024)} MB")
    except ClientError as e:
        print(f"  ❌ {e}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Upload pipeline outputs for one or more counties to Cloudflare R2."
    )
    parser.add_argument("--county",   action="append", metavar="COUNTY_ID",
                        help="County to upload (repeatable). e.g. --county kitui --county bungoma")
    parser.add_argument("--all",      action="store_true",
                        help="Upload all counties that have normalized layers locally")
    parser.add_argument("--list",     action="store_true",
                        help="List what's already in R2 for the specified counties")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Preview uploads without actually uploading")
    parser.add_argument("--include-raw", action="store_true",
                        help="Also upload raw fetched rasters (large, ~250MB/county)")

    args = parser.parse_args()

    if not args.county and not args.all:
        # Default: active county
        args.county = [get_active_county()]

    if args.all:
        # Find all counties that have at least one normalized layer locally
        counties = []
        for c in list_counties():
            try:
                cfg = load_config(c)
                if any(p.exists() for p in cfg["_paths"]["normalized_layers"].values()):
                    counties.append(c)
            except Exception:
                pass
        print(f"Found {len(counties)} counties with local normalized layers: {counties}")
    else:
        counties = args.county or []

    if not counties:
        print("No counties specified. Use --county <id> or --all")
        sys.exit(1)

    if args.list:
        for county in counties:
            list_r2_county(county)
        return

    # Upload
    results = []
    for county in counties:
        result = upload_county(county, dry_run=args.dry_run, include_raw=args.include_raw)
        results.append(result)

    # Summary
    print()
    print("=" * 55)
    print("  UPLOAD COMPLETE")
    print("=" * 55)
    total_up   = sum(r["uploaded"] for r in results)
    total_fail = sum(r["failed"]   for r in results)
    for r in results:
        status = "✅" if r["failed"] == 0 and r["uploaded"] > 0 else "⚠️" if r["failed"] > 0 else "❌"
        print(f"  {status} {r['county']:20} {r['uploaded']} uploaded, {r['failed']} failed")
    print(f"\n  Total: {total_up} files uploaded, {total_fail} failed")

    if not args.dry_run and total_fail == 0 and total_up > 0:
        print()
        print("  Next steps:")
        print("  1. Set these in Render dashboard → Environment:")
        print("       R2_ACCOUNT_ID     = your Cloudflare account ID")
        print("       R2_ACCESS_KEY_ID  = your R2 token key ID")
        print("       R2_SECRET_ACCESS_KEY = your R2 token secret")
        print("       R2_BUCKET         = suitability-engine")
        print("  2. Redeploy on Render — startup will pull from R2 (~30s vs 15min)")


if __name__ == "__main__":
    main()