"""
Normalization Module
Converts raw raster values to standardized 0-100 suitability scores
using fuzzy membership functions.

Each criterion has optimal ranges - we score how well a pixel fits those ranges.
"""

import numpy as np
import rasterio
from pathlib import Path
from typing import Callable, Dict, Tuple
import matplotlib.pyplot as plt


class FuzzyNormalizer:
    """
    Fuzzy membership functions for converting raw values to suitability scores
    """
    
    @staticmethod
    def linear_ascending(value: np.ndarray, min_val: float, max_val: float) -> np.ndarray:
        """
        Linear increase: 0 at min_val, 100 at max_val
        Example: More rainfall is better (up to a point)
        """
        score = ((value - min_val) / (max_val - min_val)) * 100
        return np.clip(score, 0, 100)
    
    @staticmethod
    def linear_descending(value: np.ndarray, min_val: float, max_val: float) -> np.ndarray:
        """
        Linear decrease: 100 at min_val, 0 at max_val
        Example: Lower slope is better
        """
        score = ((max_val - value) / (max_val - min_val)) * 100
        return np.clip(score, 0, 100)
    
    @staticmethod
    def gaussian(value: np.ndarray, optimal: float, spread: float) -> np.ndarray:
        """
        Bell curve: 100 at optimal, decreasing on both sides
        Example: Temperature has an optimal range
        
        Args:
            optimal: Peak value (gets score of 100)
            spread: How quickly score drops (standard deviation)
        """
        score = 100 * np.exp(-((value - optimal) ** 2) / (2 * spread ** 2))
        return np.clip(score, 0, 100)
    
    @staticmethod
    def trapezoidal(value: np.ndarray, a: float, b: float, c: float, d: float) -> np.ndarray:
        """
        Trapezoid: 0 before a, linear rise to b, flat 100 from b to c, 
        linear drop to d, 0 after d
        
        Example: Rainfall 600-800mm = rising, 800-1000mm = optimal, 1000-1200mm = declining
        
        Args:
            a: Start of rise
            b: Start of optimal plateau
            c: End of optimal plateau
            d: End of decline
        """
        score = np.zeros_like(value, dtype=float)
        
        # Rising edge (a to b)
        mask = (value >= a) & (value < b)
        score[mask] = ((value[mask] - a) / (b - a)) * 100
        
        # Optimal plateau (b to c)
        mask = (value >= b) & (value <= c)
        score[mask] = 100
        
        # Falling edge (c to d)
        mask = (value > c) & (value <= d)
        score[mask] = ((d - value[mask]) / (d - c)) * 100
        
        return np.clip(score, 0, 100)
    
    @staticmethod
    def categorical(value: np.ndarray, score_map: Dict[int, float]) -> np.ndarray:
        """
        Map categorical values to scores
        Example: Soil drainage - 1=poor(30), 2=moderate(70), 3=good(100)
        
        Args:
            score_map: {category_value: score}
        """
        score = np.zeros_like(value, dtype=float)
        for category, category_score in score_map.items():
            score[value == category] = category_score
        return score


class CottonSuitabilityNormalizer:
    """
    Normalize all layers for cotton farming suitability
    Based on agronomic requirements for cotton cultivation
    """
    
    def __init__(self, input_dir: Path, output_dir: Path):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.fuzzy = FuzzyNormalizer()
    
    def normalize_elevation(self, input_path: Path) -> Path:
        """
        Elevation normalization
        Cotton optimal: 0-1000m (100 score)
        Acceptable: up to 1500m
        Poor: >1500m
        """
        print("Normalizing Elevation...")
        output_path = self.output_dir / 'normalized_elevation.tif'
        
        with rasterio.open(input_path) as src:
            elevation = src.read(1).astype(float)
            profile = src.profile.copy()
            
            # Trapezoidal: optimal 0-1000m, acceptable to 1500m
            score = self.fuzzy.trapezoidal(
                elevation,
                a=0,      # Start score rise
                b=200,    # Optimal starts
                c=1000,   # Optimal ends
                d=1500    # Acceptable ends
            )
            
            # Update profile for output
            profile.update(dtype=rasterio.float32, compress='lzw')
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(score.astype(np.float32), 1)
        
        print(f"  ✅ Saved: {output_path.name}")
        return output_path
    
    def normalize_rainfall(self, input_path: Path) -> Path:
        """
        Rainfall normalization
        Cotton optimal: 600-1200mm annually
        """
        print("Normalizing Rainfall...")
        output_path = self.output_dir / 'normalized_rainfall.tif'
        
        with rasterio.open(input_path) as src:
            rainfall = src.read(1).astype(float)
            profile = src.profile.copy()
            
            # Trapezoidal: optimal 700-1000mm
            score = self.fuzzy.trapezoidal(
                rainfall,
                a=500,    # Too dry
                b=700,    # Good starts
                c=1000,   # Good ends
                d=1400    # Too wet
            )
            
            profile.update(dtype=rasterio.float32, compress='lzw')
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(score.astype(np.float32), 1)
        
        print(f"  ✅ Saved: {output_path.name}")
        return output_path
    
    def normalize_temperature(self, input_path: Path) -> Path:
        """
        Temperature normalization
        Cotton optimal: 20-30°C
        """
        print("Normalizing Temperature...")
        output_path = self.output_dir / 'normalized_temperature.tif'
        
        with rasterio.open(input_path) as src:
            temperature = src.read(1).astype(float)
            profile = src.profile.copy()
            
            # Gaussian: optimal at 25°C, spread of 5°C
            score = self.fuzzy.gaussian(
                temperature,
                optimal=25.0,
                spread=5.0
            )
            
            profile.update(dtype=rasterio.float32, compress='lzw')
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(score.astype(np.float32), 1)
        
        print(f"  ✅ Saved: {output_path.name}")
        return output_path
    
    def normalize_soil(self, input_path: Path) -> Path:
        """
        Soil drainage normalization
        Categorical: 1=poor, 2=moderate, 3=good
        Cotton prefers well-drained soils
        """
        print("Normalizing Soil Drainage...")
        output_path = self.output_dir / 'normalized_soil.tif'
        
        with rasterio.open(input_path) as src:
            soil = src.read(1).astype(float)
            profile = src.profile.copy()
            
            # Map categories to scores
            score = self.fuzzy.categorical(
                soil,
                score_map={
                    1: 30,   # Poor drainage
                    2: 70,   # Moderate drainage
                    3: 100   # Good drainage
                }
            )
            
            profile.update(dtype=rasterio.float32, compress='lzw')
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(score.astype(np.float32), 1)
        
        print(f"  ✅ Saved: {output_path.name}")
        return output_path
    
    def normalize_slope(self, input_path: Path) -> Path:
        """
        Slope normalization
        Cotton optimal: <5°, acceptable up to 15°
        """
        print("Normalizing Slope...")
        output_path = self.output_dir / 'normalized_slope.tif'
        
        with rasterio.open(input_path) as src:
            slope = src.read(1).astype(float)
            profile = src.profile.copy()
            
            # Linear descending: lower slope is better
            score = self.fuzzy.linear_descending(
                slope,
                min_val=0,
                max_val=15
            )
            
            # Very steep slopes (>15°) get 0
            score[slope > 15] = 0
            
            profile.update(dtype=rasterio.float32, compress='lzw')
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(score.astype(np.float32), 1)
        
        print(f"  ✅ Saved: {output_path.name}")
        return output_path
    
    def normalize_all(self, aligned_rasters: Dict[str, Path]) -> Dict[str, Path]:
        """
        Normalize all criterion layers
        
        Args:
            aligned_rasters: Dict of {layer_name: path_to_aligned_raster}
            
        Returns:
            Dict of {layer_name: path_to_normalized_raster}
        """
        print("=== Normalizing All Layers ===\n")
        
        normalized = {}
        
        if 'elevation' in aligned_rasters:
            normalized['elevation'] = self.normalize_elevation(aligned_rasters['elevation'])
        
        if 'rainfall' in aligned_rasters:
            normalized['rainfall'] = self.normalize_rainfall(aligned_rasters['rainfall'])
        
        if 'temperature' in aligned_rasters:
            normalized['temperature'] = self.normalize_temperature(aligned_rasters['temperature'])
        
        if 'soil' in aligned_rasters:
            normalized['soil'] = self.normalize_soil(aligned_rasters['soil'])
        
        if 'slope' in aligned_rasters:
            normalized['slope'] = self.normalize_slope(aligned_rasters['slope'])
        
        print("\n=== Normalization Complete ===")
        print(f"All normalized layers (0-100 scores) saved to: {self.output_dir}\n")
        
        return normalized
    
    def visualize_fuzzy_functions(self, output_path: Path = None):
        """Create visualization of all fuzzy membership functions"""
        fig, axes = plt.subplots(2, 3, figsize=(15, 10))
        fig.suptitle('Cotton Suitability Fuzzy Membership Functions', fontsize=16)
        
        # 1. Elevation
        elev = np.linspace(0, 2000, 100)
        elev_score = self.fuzzy.trapezoidal(elev, 0, 200, 1000, 1500)
        axes[0, 0].plot(elev, elev_score, 'b-', linewidth=2)
        axes[0, 0].set_xlabel('Elevation (m)')
        axes[0, 0].set_ylabel('Suitability Score')
        axes[0, 0].set_title('Elevation')
        axes[0, 0].grid(True, alpha=0.3)
        axes[0, 0].axhline(y=100, color='g', linestyle='--', alpha=0.3)
        
        # 2. Rainfall
        rain = np.linspace(300, 1600, 100)
        rain_score = self.fuzzy.trapezoidal(rain, 500, 700, 1000, 1400)
        axes[0, 1].plot(rain, rain_score, 'b-', linewidth=2)
        axes[0, 1].set_xlabel('Rainfall (mm/year)')
        axes[0, 1].set_ylabel('Suitability Score')
        axes[0, 1].set_title('Rainfall')
        axes[0, 1].grid(True, alpha=0.3)
        axes[0, 1].axhline(y=100, color='g', linestyle='--', alpha=0.3)
        
        # 3. Temperature
        temp = np.linspace(10, 40, 100)
        temp_score = self.fuzzy.gaussian(temp, 25, 5)
        axes[0, 2].plot(temp, temp_score, 'b-', linewidth=2)
        axes[0, 2].set_xlabel('Temperature (°C)')
        axes[0, 2].set_ylabel('Suitability Score')
        axes[0, 2].set_title('Temperature')
        axes[0, 2].grid(True, alpha=0.3)
        axes[0, 2].axhline(y=100, color='g', linestyle='--', alpha=0.3)
        
        # 4. Soil (categorical)
        soil_cats = [1, 2, 3]
        soil_scores = [30, 70, 100]
        axes[1, 0].bar(soil_cats, soil_scores, color='b', alpha=0.7)
        axes[1, 0].set_xlabel('Soil Drainage Class')
        axes[1, 0].set_ylabel('Suitability Score')
        axes[1, 0].set_title('Soil Drainage')
        axes[1, 0].set_xticks(soil_cats)
        axes[1, 0].set_xticklabels(['Poor', 'Moderate', 'Good'])
        axes[1, 0].grid(True, alpha=0.3, axis='y')
        
        # 5. Slope
        slope = np.linspace(0, 30, 100)
        slope_score = np.where(slope <= 15, 
                              100 * (1 - slope/15), 
                              0)
        axes[1, 1].plot(slope, slope_score, 'b-', linewidth=2)
        axes[1, 1].set_xlabel('Slope (degrees)')
        axes[1, 1].set_ylabel('Suitability Score')
        axes[1, 1].set_title('Slope')
        axes[1, 1].grid(True, alpha=0.3)
        axes[1, 1].axhline(y=100, color='g', linestyle='--', alpha=0.3)
        
        # Hide extra subplot
        axes[1, 2].axis('off')
        
        plt.tight_layout()
        
        if output_path:
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            print(f"✅ Fuzzy functions visualization saved: {output_path}")
        
        return fig


def main():
    """Example usage"""
    from pathlib import Path
    
    # Paths
    input_dir = Path.home() / 'suitability-engine' / 'data' / 'processed'
    output_dir = Path.home() / 'suitability-engine' / 'data' / 'normalized'
    
    # Define aligned rasters
    aligned_rasters = {
        'elevation': input_dir / 'aligned_elevation.tif',
        'rainfall': input_dir / 'aligned_rainfall.tif',
        'temperature': input_dir / 'aligned_temperature.tif',
        'soil': input_dir / 'aligned_soil.tif',
        'slope': input_dir / 'aligned_slope.tif'
    }
    
    # Check files exist
    print("=== Checking Aligned Files ===\n")
    all_exist = True
    for name, path in aligned_rasters.items():
        exists = path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {name}: {path.name}")
        if not exists:
            all_exist = False
    print()
    
    if not all_exist:
        print("⚠️  Run align_rasters.py first!")
        return
    
    # Create normalizer
    normalizer = CottonSuitabilityNormalizer(input_dir, output_dir)
    
    # Visualize fuzzy functions
    viz_path = output_dir / 'fuzzy_functions.png'
    normalizer.visualize_fuzzy_functions(viz_path)
    
    # Normalize all layers
    normalized = normalizer.normalize_all(aligned_rasters)
    
    print("\n=== Next Steps ===")
    print("1. Open normalized rasters in QGIS")
    print("2. Verify all values are 0-100")
    print("3. Check fuzzy_functions.png to understand scoring")
    print("4. Proceed to weighted overlay")


if __name__ == '__main__':
    main()