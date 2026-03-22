"""
suitability.py
--------------
Weighted-overlay suitability engine.
Reads all paths and weights from the active county config.

Run from the project root:
    python src/suitability.py
"""

import sys
import json
import numpy as np
import rasterio
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs


class SuitabilityEngine:
    """Calculate, classify, and summarise suitability rasters."""

    default_weights: Dict[str, float] = {}  # populated from config in main()

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Constraint mask ────────────────────────────────────────────────────────

    def load_constraint_mask(self, constraint_paths: List[Path]):
        """
        Build a binary mask from constraint rasters.
        0 = excluded, 1 = allowed.
        """
        print("=== Building Constraint Mask ===\n")

        mask      = None
        profile   = None

        for path in constraint_paths:
            if not path.exists():
                print(f"  ⚠️  Constraint not found, skipping: {path.name}")
                continue

            with rasterio.open(path) as src:
                data = src.read(1)
                if mask is None:
                    mask    = np.ones_like(data, dtype=np.uint8)
                    profile = src.profile.copy()

                if 'protected' in path.name.lower():
                    mask[data == 1] = 0
                    print(f"  Excluded {np.sum(data == 1):,} protected pixels")

        if mask is None:
            # No constraints — allow everything
            return None, None

        total   = mask.size
        allowed = int(mask.sum())
        print(f"  Final mask: {allowed:,}/{total:,} pixels allowed\n")
        return mask, profile

    # ── Weighted overlay ───────────────────────────────────────────────────────

    def calculate_suitability(self,
                              normalized_layers: Dict[str, Path],
                              weights: Dict[str, float] = None,
                              constraint_paths: List[Path] = None,
                              output_name: str = 'suitability.tif') -> Path:
        """
        Calculate suitability using weighted overlay.

        Args:
            normalized_layers: {layer_name: path_to_normalized_raster}
            weights:           {layer_name: weight}. Uses default_weights if None.
            constraint_paths:  list of constraint raster paths (optional).
            output_name:       filename for the output raster.

        Returns:
            Path to the saved suitability raster.
        """
        if weights is None:
            weights = self.default_weights

        print("=== Calculating Suitability ===\n")
        print("Weights:")
        for name, w in weights.items():
            print(f"  {name}: {w:.2f}")
        print()

        suitability = None
        profile     = None

        for name, path in normalized_layers.items():
            if name not in weights:
                continue
            if not path.exists():
                print(f"  ⚠️  Missing layer, skipping: {path.name}")
                continue

            with rasterio.open(path) as src:
                data = src.read(1).astype(np.float32)
                if profile is None:
                    profile = src.profile.copy()

            weighted_layer = data * weights[name]

            if suitability is None:
                suitability = weighted_layer
            else:
                suitability += weighted_layer

            print(f"  Added {name} (weight={weights[name]:.2f})")

        if suitability is None:
            raise RuntimeError("No valid normalized layers found.")

        print(f"\n  Range before constraints: {suitability.min():.1f}–{suitability.max():.1f}\n")

        # Apply constraints
        if constraint_paths:
            mask_arr, _ = self.load_constraint_mask(constraint_paths)
            if mask_arr is not None:
                suitability = suitability * mask_arr
                print(f"  Constraints applied: {np.sum(mask_arr == 0):,} pixels excluded\n")

        suitability = np.clip(suitability, 0, 100)

        output_path = self.output_dir / output_name
        profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)

        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(suitability, 1)

        print(f"✅ Suitability raster saved: {output_path}")
        print(f"   Range: {suitability.min():.1f}–{suitability.max():.1f}")
        print(f"   Mean:  {suitability[suitability > 0].mean():.1f}\n")

        return output_path

    # ── Classification ─────────────────────────────────────────────────────────

    def classify_suitability(self, suitability_path: Path,
                             thresholds: Dict[str, Tuple[float, float]] = None) -> Path:
        """Classify continuous suitability into four named classes."""
        if thresholds is None:
            thresholds = {
                'Highly Suitable':     (70, 100),
                'Moderately Suitable': (50, 70),
                'Marginally Suitable': (30, 50),
                'Not Suitable':        (0,  30),
            }

        print("=== Classifying Suitability ===\n")

        with rasterio.open(suitability_path) as src:
            data    = src.read(1).astype(np.float32)
            profile = src.profile.copy()

        classified = np.zeros_like(data, dtype=np.uint8)
        class_map  = {1: 'Highly Suitable', 2: 'Moderately Suitable',
                      3: 'Marginally Suitable', 4: 'Not Suitable'}

        for class_id, (name, (lo, hi)) in enumerate(thresholds.items(), start=1):
            pixels = np.sum((data >= lo) & (data < hi))
            classified[(data >= lo) & (data < hi)] = class_id
            pct = pixels / data.size * 100
            print(f"  Class {class_id} ({name}): {pixels:,} px ({pct:.1f}%)")

        output_path = suitability_path.parent / suitability_path.name.replace(
            '.tif', '_classified.tif')
        profile.update(dtype=rasterio.uint8, compress='lzw', nodata=0)

        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(classified, 1)

        print(f"\n✅ Classified raster saved: {output_path}\n")
        return output_path

    # ── Statistics ─────────────────────────────────────────────────────────────

    def generate_statistics(self, suitability_path: Path) -> Dict:
        """Compute descriptive statistics for a suitability raster."""
        print("=== Generating Statistics ===\n")

        with rasterio.open(suitability_path) as src:
            data = src.read(1).astype(np.float32)

        valid = data[data > 0]

        stats = {
            'total_pixels':   int(data.size),
            'valid_pixels':   int(valid.size),
            'mean':           round(float(valid.mean()),   2) if valid.size else 0,
            'std':            round(float(valid.std()),    2) if valid.size else 0,
            'min':            round(float(valid.min()),    2) if valid.size else 0,
            'max':            round(float(valid.max()),    2) if valid.size else 0,
            'highly_suitable_pct':     round(float(np.sum(data >= 70) / data.size * 100), 2),
            'moderately_suitable_pct': round(float(np.sum((data >= 50) & (data < 70)) / data.size * 100), 2),
            'marginally_suitable_pct': round(float(np.sum((data >= 30) & (data < 50)) / data.size * 100), 2),
            'not_suitable_pct':        round(float(np.sum((data > 0) & (data < 30)) / data.size * 100), 2),
            'zero_pixels':    int(np.sum(data == 0)),
        }

        return stats

    # ── Metadata ───────────────────────────────────────────────────────────────

    def save_metadata(self, weights: Dict[str, float],
                      stats: Dict, output_name: str = 'analysis_metadata.json') -> Path:
        """Save analysis metadata as JSON."""
        metadata = {
            'weights':    weights,
            'statistics': stats,
            'criteria':   list(weights.keys()),
            'suitability_range': [0, 100],
            'classification': {
                1: 'Highly Suitable (70-100)',
                2: 'Moderately Suitable (50-70)',
                3: 'Marginally Suitable (30-50)',
                4: 'Not Suitable (0-30)',
            },
        }
        output_path = self.output_dir / output_name
        with open(output_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        print(f"✅ Metadata saved: {output_path}\n")
        return output_path


def main():
    config = load_config()
    paths  = config['_paths']

    create_county_dirs(config)

    print("=" * 55)
    print(f"  SUITABILITY ENGINE: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    # Check normalized layers
    print("── Checking normalized layers ───────────────────────────")
    all_exist = True
    for name, path in paths['normalized_layers'].items():
        exists = path.exists()
        print(f"  {'✅' if exists else '❌'} {name}: {path.name}")
        if not exists:
            all_exist = False
    print()

    if not all_exist:
        print("⚠️  Missing normalized layers — run normalize.py first.")
        return

    # Constraint mask
    constraint_paths = [paths['constraint_mask']] if paths['constraint_mask'].exists() else []

    # Engine
    engine = SuitabilityEngine(paths['results_dir'])
    engine.default_weights = config['weights']

    crop = config['crop'].lower().replace(' ', '_')

    # Calculate
    suitability_path = engine.calculate_suitability(
        normalized_layers=paths['normalized_layers'],
        weights=config['weights'],
        constraint_paths=constraint_paths,
        output_name=f"{config['county']}_{crop}_suitability.tif",
    )

    # Classify
    engine.classify_suitability(suitability_path)

    # Statistics
    stats = engine.generate_statistics(suitability_path)
    print("── Statistics ───────────────────────────────────────────")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print()

    # Save metadata
    engine.save_metadata(config['weights'], stats)

    print("=" * 55)
    print("  ANALYSIS COMPLETE")
    print(f"  Results saved to: {paths['results_dir']}")
    print()
    print("  Next steps:")
    print("    python src/api.py")
    print("=" * 55)


if __name__ == '__main__':
    main()