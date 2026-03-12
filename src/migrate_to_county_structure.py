"""
migrate_to_county_structure.py
-------------------------------
One-time migration: moves existing flat data/ layout into the new
per-county structure under data/counties/<county>/.

Run ONCE from the project root:
    python src/migrate_to_county_structure.py

Safe to re-run — won't overwrite files that already exist at destination.
"""

import shutil
import sys
from pathlib import Path

BASE_DIR = Path.home() / 'suitability-engine'

# Map: old path → new path (relative to BASE_DIR)
MIGRATIONS = {
    # Boundaries
    'data/boundaries/bungoma_boundary.gpkg':
        'data/counties/bungoma/boundaries/bungoma_boundary.gpkg',

    # Preprocessed rasters
    'data/preprocessed/bungoma_elevation.tif':
        'data/counties/bungoma/preprocessed/bungoma_elevation.tif',
    'data/preprocessed/bungoma_rainfall.tif':
        'data/counties/bungoma/preprocessed/bungoma_rainfall.tif',
    'data/preprocessed/bungoma_temperature.tif':
        'data/counties/bungoma/preprocessed/bungoma_temperature.tif',
    'data/preprocessed/bungoma_soil.tif':
        'data/counties/bungoma/preprocessed/bungoma_soil.tif',
    'data/preprocessed/bungoma_slope.tif':
        'data/counties/bungoma/preprocessed/bungoma_slope.tif',
    'data/preprocessed/bungoma_constraints_mask.tif':
        'data/counties/bungoma/preprocessed/bungoma_constraints_mask.tif',

    # Aligned (processed) rasters
    'data/processed/aligned_elevation.tif':
        'data/counties/bungoma/processed/aligned_elevation.tif',
    'data/processed/aligned_rainfall.tif':
        'data/counties/bungoma/processed/aligned_rainfall.tif',
    'data/processed/aligned_temperature.tif':
        'data/counties/bungoma/processed/aligned_temperature.tif',
    'data/processed/aligned_soil.tif':
        'data/counties/bungoma/processed/aligned_soil.tif',
    'data/processed/aligned_slope.tif':
        'data/counties/bungoma/processed/aligned_slope.tif',

    # Normalized rasters
    'data/normalized/normalized_elevation.tif':
        'data/counties/bungoma/normalized/normalized_elevation.tif',
    'data/normalized/normalized_rainfall.tif':
        'data/counties/bungoma/normalized/normalized_rainfall.tif',
    'data/normalized/normalized_temperature.tif':
        'data/counties/bungoma/normalized/normalized_temperature.tif',
    'data/normalized/normalized_soil.tif':
        'data/counties/bungoma/normalized/normalized_soil.tif',
    'data/normalized/normalized_slope.tif':
        'data/counties/bungoma/normalized/normalized_slope.tif',
}

# API results (copy whole directory)
OLD_RESULTS = BASE_DIR / 'data' / 'api_results'
NEW_RESULTS = BASE_DIR / 'data' / 'counties' / 'bungoma' / 'api_results'


def main():
    print("=" * 55)
    print("  MIGRATING TO COUNTY-AGNOSTIC STRUCTURE")
    print("=" * 55)
    print()

    moved   = 0
    skipped = 0
    missing = 0

    for old_rel, new_rel in MIGRATIONS.items():
        src = BASE_DIR / old_rel
        dst = BASE_DIR / new_rel

        if not src.exists():
            print(f"  ⚠️  Not found (skip): {old_rel}")
            missing += 1
            continue

        if dst.exists():
            print(f"  ✓  Already exists:  {new_rel}")
            skipped += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"  ✅ Copied: {old_rel}")
        print(f"       → {new_rel}")
        moved += 1

    # Copy api_results directory
    print()
    if OLD_RESULTS.exists():
        NEW_RESULTS.mkdir(parents=True, exist_ok=True)
        for f in OLD_RESULTS.glob('*'):
            dst = NEW_RESULTS / f.name
            if not dst.exists():
                shutil.copy2(f, dst)
                print(f"  ✅ Copied result: {f.name}")
            else:
                print(f"  ✓  Result exists: {f.name}")

    # Set bungoma as active county
    config_dir = BASE_DIR / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)
    active_file = config_dir / 'active_county.txt'
    active_file.write_text('bungoma\n')
    print(f"\n  ✅ Set active county: bungoma")

    print()
    print(f"  Moved:   {moved}")
    print(f"  Skipped: {skipped}")
    print(f"  Missing: {missing}")
    print()
    print("=" * 55)
    print("  DONE")
    print()
    print("  New structure:")
    print("  data/counties/")
    print("    bungoma/")
    print("      boundaries/  preprocessed/  processed/")
    print("      normalized/  api_results/")
    print()
    print("  Next steps:")
    print("  1. Copy bungoma.json → config/bungoma.json")
    print("  2. Copy kitui.json   → config/kitui.json")
    print("  3. python src/api.py   (confirm Bungoma still works)")
    print()
    print("  To switch to Kitui later:")
    print("    echo 'kitui' > config/active_county.txt")
    print("    # drop Kitui rasters into data/counties/kitui/preprocessed/")
    print("    python src/realign_to_boundary.py")
    print("    python src/normalize.py")
    print("    python src/clip_to_boundary.py")
    print("    python src/api.py")
    print("=" * 55)


if __name__ == '__main__':
    main()