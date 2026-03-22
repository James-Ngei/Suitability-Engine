"""
align_rasters.py
----------------
Aligns all preprocessed rasters to a common grid (CRS, resolution,
extent, pixel alignment). Reads paths from the active county config.

Run from the project root:
    python src/align_rasters.py

Usually run before realign_to_boundary.py; that script snaps everything
to the boundary extent at the configured resolution.
"""

import sys
import math
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, calculate_default_transform
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs


class RasterAligner:
    """Align a set of rasters to a shared grid."""

    def __init__(self, output_dir: Path, target_resolution: float = 0.01,
                 target_crs: str = 'EPSG:4326'):
        self.output_dir        = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.target_resolution = target_resolution
        self.target_crs        = target_crs

    # ── Inspection ─────────────────────────────────────────────────────────────

    def inspect(self, path: Path) -> dict:
        """Return basic metadata for a single raster."""
        if not path.exists():
            return {'path': path, 'exists': False}
        with rasterio.open(path) as src:
            return {
                'path':       path,
                'exists':     True,
                'crs':        str(src.crs),
                'resolution': src.res,
                'shape':      (src.height, src.width),
                'bounds':     src.bounds,
                'dtype':      src.dtypes[0],
                'nodata':     src.nodata,
            }

    def inspect_all(self, paths: list) -> None:
        """Print metadata for every raster in the list."""
        print("── Raster Inspection ────────────────────────────────────")
        for path in paths:
            info = self.inspect(path)
            if not info['exists']:
                print(f"  ❌ {path.name}: NOT FOUND")
                continue
            print(f"  {path.name}")
            print(f"     CRS: {info['crs']}  res: {info['resolution']}  "
                  f"shape: {info['shape']}")
        print()

    # ── Alignment ──────────────────────────────────────────────────────────────

    def align_one(self, src_path: Path, output_path: Path,
                  resampling: Resampling = Resampling.bilinear) -> Path:
        """Reproject a single raster to the target CRS and resolution."""
        with rasterio.open(src_path) as src:
            transform, width, height = calculate_default_transform(
                src.crs, self.target_crs, src.width, src.height, *src.bounds,
                resolution=self.target_resolution,
            )

            profile = src.profile.copy()
            profile.update(
                crs=self.target_crs,
                transform=transform,
                width=width,
                height=height,
                compress='lzw',
            )

            data = np.zeros((src.count, height, width), dtype=src.dtypes[0])

            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=data[i - 1],
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=self.target_crs,
                    resampling=resampling,
                    src_nodata=src.nodata,
                    dst_nodata=src.nodata,
                )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(data)

        return output_path

    def align_all(self, rasters: Dict[str, Path],
                  categorical: set = None) -> Dict[str, Path]:
        """
        Align all rasters.  Returns {name: aligned_path}.

        categorical: set of layer names that should use nearest-neighbour
                     resampling (e.g. {'soil', 'landcover'}).
        """
        if categorical is None:
            categorical = {'soil', 'landcover'}

        aligned = {}
        print("── Aligning rasters ─────────────────────────────────────")
        for name, path in rasters.items():
            if not path.exists():
                print(f"  ⚠️  {name}: not found — skipping")
                continue

            out_path   = self.output_dir / f'aligned_{name}.tif'
            resampling = (Resampling.nearest if name in categorical
                          else Resampling.bilinear)

            self.align_one(path, out_path, resampling)
            aligned[name] = out_path
            print(f"  ✅ {name} → {out_path.name}")

        print()
        return aligned

    # ── Verification ───────────────────────────────────────────────────────────

    def verify_alignment(self, aligned: Dict[str, Path]) -> bool:
        """Check that all aligned rasters share the same grid."""
        print("── Verifying alignment ──────────────────────────────────")
        reference = None
        all_match = True

        for name, path in aligned.items():
            if not path.exists():
                print(f"  ⚠️  {name}: file missing")
                all_match = False
                continue

            with rasterio.open(path) as src:
                info = (src.crs, src.transform, src.width, src.height)

            if reference is None:
                reference = info
                print(f"  Reference: {name}")
                continue

            if info == reference:
                print(f"  ✅ {name}: aligned")
            else:
                print(f"  ❌ {name}: MISMATCH")
                all_match = False

        print()
        if all_match:
            print("✅ All rasters aligned successfully.")
        else:
            print("❌ Alignment verification failed!")
        return all_match


def main():
    config = load_config()
    paths  = config['_paths']

    create_county_dirs(config)

    print("=" * 55)
    print(f"  ALIGN RASTERS: {config['display_name'].upper()}")
    print("=" * 55)
    print()

    # Check preprocessed inputs
    print("── Checking preprocessed inputs ─────────────────────────")
    for name, path in paths['layers'].items():
        status = '✅' if path.exists() else '❌'
        print(f"  {status} {name}: {path.name}")
    print()

    aligner = RasterAligner(
        output_dir=paths['processed_dir'],
        target_resolution=config['resolution'],
    )

    # Inspect
    aligner.inspect_all(list(paths['layers'].values()))

    # Align
    aligned = aligner.align_all(paths['layers'])

    # Verify
    aligner.verify_alignment(aligned)

    print()
    print("=" * 55)
    print("  DONE")
    print()
    print("  Next steps:")
    print("    python src/realign_to_boundary.py")
    print("    python src/normalize.py")
    print("    python src/clip_to_boundary.py")
    print("=" * 55)


if __name__ == '__main__':
    main()