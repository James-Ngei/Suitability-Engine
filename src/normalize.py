"""
Normalization Module
Converts raw raster values to standardized 0-100 suitability scores
using fuzzy membership functions.

Thresholds are calibrated to actual Bungoma County data ranges:
  elevation  : 1247–4162 m  (highland county, Mt Elgon)
  rainfall   : 1367–2142 mm (high rainfall zone)
  temperature: 15–31 °C
  soil       : 234–495 g/kg clay content (SoilGrids)
  slope      : 0.4–14.6 degrees
"""

import numpy as np
import rasterio
from pathlib import Path
from typing import Dict
import matplotlib.pyplot as plt


class FuzzyNormalizer:
    """Fuzzy membership functions for converting raw values to 0-100 scores."""

    @staticmethod
    def linear_descending(value: np.ndarray, min_val: float, max_val: float) -> np.ndarray:
        """100 at min_val, 0 at max_val."""
        score = ((max_val - value) / (max_val - min_val)) * 100
        return np.clip(score, 0, 100)

    @staticmethod
    def gaussian(value: np.ndarray, optimal: float, spread: float) -> np.ndarray:
        """100 at optimal, bell-curve decline on both sides."""
        score = 100 * np.exp(-((value - optimal) ** 2) / (2 * spread ** 2))
        return np.clip(score, 0, 100)

    @staticmethod
    def trapezoidal(value: np.ndarray,
                    a: float, b: float, c: float, d: float) -> np.ndarray:
        """
        0 before a | linear rise a→b | plateau 100 b→c | linear fall c→d | 0 after d
        """
        score = np.zeros_like(value, dtype=float)

        # Rising edge
        mask = (value >= a) & (value < b)
        score[mask] = ((value[mask] - a) / (b - a)) * 100

        # Optimal plateau
        mask = (value >= b) & (value <= c)
        score[mask] = 100

        # Falling edge
        mask = (value > c) & (value <= d)
        score[mask] = ((d - value[mask]) / (d - c)) * 100

        return np.clip(score, 0, 100)


class CottonSuitabilityNormalizer:
    """
    Normalize all layers for cotton farming suitability in Bungoma County.
    Each function documents: the raw data range, the agronomic rationale,
    and the chosen fuzzy thresholds.
    """

    def __init__(self, input_dir: Path, output_dir: Path):
        self.input_dir  = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fuzzy = FuzzyNormalizer()

    # ── Elevation ──────────────────────────────────────────────────────────────
    def normalize_elevation(self, input_path: Path) -> Path:
        """
        Raw range : 1247–4162 m (mean 1857 m)
        Rationale : Cotton in East Africa grows well at 1000–1800 m.
                    Above 2000 m temperatures drop below cotton tolerance.
                    Mt Elgon pixels (>2500 m) are genuinely unsuitable.
        Thresholds: optimal 1200–1700 m, declining to 0 at 2400 m.
        """
        print("Normalizing Elevation...")
        output_path = self.output_dir / 'normalized_elevation.tif'

        with rasterio.open(input_path) as src:
            elevation = src.read(1).astype(float)
            profile   = src.profile.copy()

        score = self.fuzzy.trapezoidal(
            elevation,
            a=1000,   # score starts rising
            b=1200,   # optimal starts
            c=1700,   # optimal ends
            d=2400    # score reaches 0
        )

        profile.update(dtype=rasterio.float32, compress='lzw')
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(score.astype(np.float32), 1)

        print(f"  ✅ Saved: {output_path.name}")
        return output_path

    # ── Rainfall ───────────────────────────────────────────────────────────────
    def normalize_rainfall(self, input_path: Path) -> Path:
        """
        Raw range : 1367–2142 mm (mean 1794 mm, tightly clustered)
        Rationale : Cotton tolerates up to ~1800 mm with adequate drainage.
                    Above 2000 mm disease pressure and waterlogging increase.
                    Entire county is above classic optimal (700-1000 mm) so
                    thresholds are shifted to reflect highland cotton reality.
        Thresholds: optimal 1400–1800 mm, declining to 0 at 2200 mm.
        """
        print("Normalizing Rainfall...")
        output_path = self.output_dir / 'normalized_rainfall.tif'

        with rasterio.open(input_path) as src:
            rainfall = src.read(1).astype(float)
            profile  = src.profile.copy()

        score = self.fuzzy.trapezoidal(
            rainfall,
            a=1200,   # score starts rising
            b=1400,   # optimal starts
            c=1800,   # optimal ends
            d=2200    # score reaches 0
        )

        profile.update(dtype=rasterio.float32, compress='lzw')
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(score.astype(np.float32), 1)

        print(f"  ✅ Saved: {output_path.name}")
        return output_path

    # ── Temperature ────────────────────────────────────────────────────────────
    def normalize_temperature(self, input_path: Path) -> Path:
        """
        Raw range : 15–31 °C (mean 27 °C)
        Rationale : Cotton optimal 20–30 °C. Below 15 °C growth stops.
                    Gaussian centred at 25 °C captures the bell-curve response.
        Thresholds: Gaussian, optimal 25 °C, spread 5 °C.
        """
        print("Normalizing Temperature...")
        output_path = self.output_dir / 'normalized_temperature.tif'

        with rasterio.open(input_path) as src:
            temperature = src.read(1).astype(float)
            profile     = src.profile.copy()

        score = self.fuzzy.gaussian(temperature, optimal=25, spread=5)

        profile.update(dtype=rasterio.float32, compress='lzw')
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(score.astype(np.float32), 1)

        print(f"  ✅ Saved: {output_path.name}")
        return output_path

    # ── Soil ───────────────────────────────────────────────────────────────────
    def normalize_soil(self, input_path: Path) -> Path:
        """
        Raw range : 234–495 g/kg clay content (SoilGrids)
                    median 425, p25=406, p75=445
        Rationale : Cotton prefers well-drained loamy soils.
                    Moderate clay (250–380 g/kg) = good drainage + water retention.
                    High clay (>450 g/kg) = waterlogging risk, poor aeration.
                    Low clay (<200 g/kg) = too sandy for this dataset range.
        Thresholds: optimal 250–380 g/kg, declining to 0 at 500 g/kg.
                    Uses trapezoidal — lower clay scores better in this range.
        """
        print("Normalizing Soil (clay content g/kg)...")
        output_path = self.output_dir / 'normalized_soil.tif'

        with rasterio.open(input_path) as src:
            soil    = src.read(1).astype(float)
            profile = src.profile.copy()

        score = self.fuzzy.trapezoidal(
            soil,
            a=150,    # score starts rising
            b=250,    # optimal starts
            c=380,    # optimal ends
            d=500     # score reaches 0
        )

        profile.update(dtype=rasterio.float32, compress='lzw')
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(score.astype(np.float32), 1)

        print(f"  ✅ Saved: {output_path.name}")
        return output_path

    # ── Slope ──────────────────────────────────────────────────────────────────
    def normalize_slope(self, input_path: Path) -> Path:
        """
        Raw range : 0.4–14.6 degrees (mean 3.1°) — entirely within safe range.
        Rationale : Flat land is best for mechanisation and erosion control.
                    Cotton is not viable on slopes above 15°.
        Thresholds: linear descending 0→15°. No change needed from original.
        """
        print("Normalizing Slope...")
        output_path = self.output_dir / 'normalized_slope.tif'

        with rasterio.open(input_path) as src:
            slope   = src.read(1).astype(float)
            profile = src.profile.copy()

        score = self.fuzzy.linear_descending(slope, min_val=0, max_val=15)
        score[slope > 15] = 0

        profile.update(dtype=rasterio.float32, compress='lzw')
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(score.astype(np.float32), 1)

        print(f"  ✅ Saved: {output_path.name}")
        return output_path

    # ── Normalize all ──────────────────────────────────────────────────────────
    def normalize_all(self, aligned_rasters: Dict[str, Path]) -> Dict[str, Path]:
        print("=== Normalizing All Layers ===\n")
        normalized = {}

        if 'elevation'   in aligned_rasters:
            normalized['elevation']   = self.normalize_elevation(aligned_rasters['elevation'])
        if 'rainfall'    in aligned_rasters:
            normalized['rainfall']    = self.normalize_rainfall(aligned_rasters['rainfall'])
        if 'temperature' in aligned_rasters:
            normalized['temperature'] = self.normalize_temperature(aligned_rasters['temperature'])
        if 'soil'        in aligned_rasters:
            normalized['soil']        = self.normalize_soil(aligned_rasters['soil'])
        if 'slope'       in aligned_rasters:
            normalized['slope']       = self.normalize_slope(aligned_rasters['slope'])

        print("\n=== Normalization Complete ===")
        print(f"Normalized layers saved to: {self.output_dir}\n")
        return normalized

    # ── Visualize fuzzy functions ──────────────────────────────────────────────
    def visualize_fuzzy_functions(self, output_path: Path = None):
        """Plot all fuzzy membership functions so thresholds can be visually verified."""
        fig, axes = plt.subplots(2, 3, figsize=(16, 10))
        fig.suptitle('Cotton Suitability — Fuzzy Membership Functions\n(Calibrated to Bungoma County data)',
                     fontsize=14)

        # Elevation
        x = np.linspace(1000, 4500, 300)
        axes[0, 0].plot(x, self.fuzzy.trapezoidal(x, 1000, 1200, 1700, 2400), 'b-', lw=2)
        axes[0, 0].set_title('Elevation'); axes[0, 0].set_xlabel('m'); axes[0, 0].set_ylabel('Score')
        axes[0, 0].axvspan(1247, 4162, alpha=0.08, color='orange', label='Data range')
        axes[0, 0].legend(fontsize=8); axes[0, 0].grid(True, alpha=0.3)

        # Rainfall
        x = np.linspace(1000, 2400, 300)
        axes[0, 1].plot(x, self.fuzzy.trapezoidal(x, 1200, 1400, 1800, 2200), 'b-', lw=2)
        axes[0, 1].set_title('Rainfall'); axes[0, 1].set_xlabel('mm/year')
        axes[0, 1].axvspan(1367, 2142, alpha=0.08, color='orange', label='Data range')
        axes[0, 1].legend(fontsize=8); axes[0, 1].grid(True, alpha=0.3)

        # Temperature
        x = np.linspace(10, 35, 300)
        axes[0, 2].plot(x, self.fuzzy.gaussian(x, 25, 5), 'b-', lw=2)
        axes[0, 2].set_title('Temperature'); axes[0, 2].set_xlabel('°C')
        axes[0, 2].axvspan(15.4, 31.1, alpha=0.08, color='orange', label='Data range')
        axes[0, 2].legend(fontsize=8); axes[0, 2].grid(True, alpha=0.3)

        # Soil clay
        x = np.linspace(100, 550, 300)
        axes[1, 0].plot(x, self.fuzzy.trapezoidal(x, 150, 250, 380, 500), 'b-', lw=2)
        axes[1, 0].set_title('Soil (clay g/kg)'); axes[1, 0].set_xlabel('g/kg')
        axes[1, 0].axvspan(234, 495, alpha=0.08, color='orange', label='Data range')
        axes[1, 0].legend(fontsize=8); axes[1, 0].grid(True, alpha=0.3)

        # Slope
        x = np.linspace(0, 20, 300)
        axes[1, 1].plot(x, self.fuzzy.linear_descending(x, 0, 15), 'b-', lw=2)
        axes[1, 1].set_title('Slope'); axes[1, 1].set_xlabel('degrees')
        axes[1, 1].axvspan(0.4, 14.6, alpha=0.08, color='orange', label='Data range')
        axes[1, 1].legend(fontsize=8); axes[1, 1].grid(True, alpha=0.3)

        axes[1, 2].axis('off')
        axes[1, 2].text(0.1, 0.6,
            "Orange shading = actual\ndata range in Bungoma.\n\n"
            "Thresholds calibrated to\nhighland cotton farming\nin East Africa.",
            fontsize=10, va='top')

        plt.tight_layout()
        if output_path:
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            print(f"✅ Fuzzy functions plot saved: {output_path}")
        return fig


def main():
    from pathlib import Path

    input_dir  = Path.home() / 'suitability-engine' / 'data' / 'processed'
    output_dir = Path.home() / 'suitability-engine' / 'data' / 'normalized'

    aligned_rasters = {
        'elevation':   input_dir / 'aligned_elevation.tif',
        'rainfall':    input_dir / 'aligned_rainfall.tif',
        'temperature': input_dir / 'aligned_temperature.tif',
        'soil':        input_dir / 'aligned_soil.tif',
        'slope':       input_dir / 'aligned_slope.tif',
    }

    print("=== Checking Aligned Files ===\n")
    for name, path in aligned_rasters.items():
        print(f"{'✅' if path.exists() else '❌'} {name}: {path.name}")
    print()

    normalizer = CottonSuitabilityNormalizer(input_dir, output_dir)

    # Save fuzzy function plots for visual verification
    normalizer.visualize_fuzzy_functions(output_dir / 'fuzzy_functions_calibrated.png')

    # Run normalization
    normalizer.normalize_all(aligned_rasters)

    # Quick sanity check on output ranges
    print("\n=== Output Sanity Check ===\n")
    for name in ['elevation', 'rainfall', 'temperature', 'soil', 'slope']:
        path = output_dir / f'normalized_{name}.tif'
        with rasterio.open(path) as src:
            data  = src.read(1)
            valid = data[data > 0]
        if valid.size > 0:
            print(f"{name}: valid={valid.size}/{data.size} pixels, "
                  f"range={valid.min():.1f}-{valid.max():.1f}, mean={valid.mean():.1f}")
        else:
            print(f"{name}: ⚠️  NO VALID PIXELS — check thresholds")

    print("\n✅ Done. Restart the API to reload layers.")


if __name__ == '__main__':
    main()