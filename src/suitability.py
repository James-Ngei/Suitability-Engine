"""
Suitability Analysis Engine
Combines normalized layers using weighted overlay to calculate final suitability scores
"""

import numpy as np
import rasterio
from pathlib import Path
from typing import Dict, List, Tuple
import json


class SuitabilityEngine:
    """
    Multi-criteria suitability analysis using weighted overlay
    Formula: Suitability = Σ(weight_i × normalized_layer_i) × constraint_mask
    """
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Default weights for cotton suitability (must sum to 1.0)
        self.default_weights = {
            'rainfall': 0.25,      # 25% - Most critical for cotton
            'elevation': 0.20,     # 20% - Altitude affects climate
            'temperature': 0.20,   # 20% - Growing season needs
            'soil': 0.20,          # 20% - Drainage critical
            'slope': 0.15          # 15% - Mechanization & erosion
        }
    
    def validate_weights(self, weights: Dict[str, float]) -> bool:
        """Ensure weights sum to 1.0 (100%)"""
        total = sum(weights.values())
        if not np.isclose(total, 1.0, atol=0.001):
            print(f"⚠️  Warning: Weights sum to {total:.3f}, not 1.0")
            print(f"   Normalizing weights...")
            return False
        return True
    
    def normalize_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Normalize weights to sum to 1.0"""
        total = sum(weights.values())
        return {k: v/total for k, v in weights.items()}
    
    def load_constraint_mask(self, constraint_paths: List[Path]) -> np.ndarray:
        """
        Load and combine constraint layers
        Constraints are binary: 0 = excluded, 1 = allowed
        
        Args:
            constraint_paths: List of paths to constraint rasters
            
        Returns:
            Combined binary mask (0 or 1)
        """
        print("=== Loading Constraints ===")
        
        mask = None
        
        for path in constraint_paths:
            print(f"  Loading: {path.name}")
            with rasterio.open(path) as src:
                data = src.read(1)
                
                if mask is None:
                    # Initialize mask (1 = allowed everywhere)
                    mask = np.ones_like(data, dtype=np.uint8)
                    mask_profile = src.profile.copy()
                
                # If it's a protected areas layer (1 = protected)
                # Convert to exclusion mask (0 = excluded)
                if 'protected' in path.name.lower():
                    mask[data == 1] = 0  # Protected areas excluded
                    print(f"    Excluded {np.sum(data == 1)} protected pixels")
                
                # If it's a slope threshold (>15° excluded)
                # Note: This assumes slope is already normalized (0-100)
                # We'll apply threshold in the original slope values
        
        print(f"  Final mask: {np.sum(mask == 1)} pixels allowed, {np.sum(mask == 0)} excluded")
        print()
        
        return mask, mask_profile
    
    def calculate_suitability(self, 
                             normalized_layers: Dict[str, Path],
                             weights: Dict[str, float] = None,
                             constraint_paths: List[Path] = None,
                             output_name: str = 'suitability.tif') -> Path:
        """
        Calculate suitability using weighted overlay
        
        Args:
            normalized_layers: Dict of {layer_name: path_to_normalized_raster}
            weights: Dict of {layer_name: weight}. If None, uses defaults
            constraint_paths: List of constraint raster paths
            output_name: Name for output suitability raster
            
        Returns:
            Path to suitability raster
        """
        print("=== Calculating Suitability ===\n")
        
        # Use default weights if not provided
        if weights is None:
            weights = self.default_weights
            print("Using default weights:")
        else:
            print("Using custom weights:")
        
        for layer, weight in weights.items():
            print(f"  {layer}: {weight:.2f} ({weight*100:.0f}%)")
        print()
        
        # Validate and normalize weights
        if not self.validate_weights(weights):
            weights = self.normalize_weights(weights)
            print("Normalized weights:")
            for layer, weight in weights.items():
                print(f"  {layer}: {weight:.2f} ({weight*100:.0f}%)")
            print()
        
        # Load all normalized layers
        print("Loading normalized layers...")
        layers_data = {}
        profile = None
        
        for name, path in normalized_layers.items():
            if name not in weights:
                print(f"  ⚠️  Skipping {name} (no weight assigned)")
                continue
            
            print(f"  Loading: {name}")
            with rasterio.open(path) as src:
                layers_data[name] = src.read(1).astype(np.float32)
                if profile is None:
                    profile = src.profile.copy()
        
        print()
        
        # Calculate weighted overlay
        print("Calculating weighted sum...")
        suitability = np.zeros_like(list(layers_data.values())[0], dtype=np.float32)
        
        for name, data in layers_data.items():
            weight = weights[name]
            weighted_layer = data * weight  # Data is 0-100, weight is 0-1
            suitability += weighted_layer
            print(f"  Added {name} (weight={weight:.2f})")
        
        print(f"  Range before constraints: {suitability.min():.1f} - {suitability.max():.1f}")
        print()
        
        # Apply constraints if provided
        if constraint_paths:
            mask, _ = self.load_constraint_mask(constraint_paths)
            suitability = suitability * mask
            print(f"Constraints applied: {np.sum(mask == 0)} pixels excluded")
            print()
        
        # Ensure 0-100 range
        suitability = np.clip(suitability, 0, 100)
        
        # Save suitability raster
        output_path = self.output_dir / output_name
        profile.update(dtype=rasterio.float32, compress='lzw', nodata=0)
        
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(suitability, 1)
        
        print(f"✅ Suitability raster saved: {output_path}")
        print(f"   Range: {suitability.min():.1f} - {suitability.max():.1f}")
        print(f"   Mean: {suitability.mean():.1f}")
        print()
        
        return output_path
    
    def classify_suitability(self, suitability_path: Path, 
                            thresholds: Dict[str, Tuple[float, float]] = None) -> Path:
        """
        Classify suitability into categories
        
        Args:
            suitability_path: Path to continuous suitability raster
            thresholds: Dict of {class_name: (min, max)}
        
        Returns:
            Path to classified raster
        """
        if thresholds is None:
            thresholds = {
                'Highly Suitable': (70, 100),
                'Moderately Suitable': (50, 70),
                'Marginally Suitable': (30, 50),
                'Not Suitable': (0, 30)
            }
        
        print("=== Classifying Suitability ===\n")
        
        with rasterio.open(suitability_path) as src:
            suitability = src.read(1)
            profile = src.profile.copy()
            
            # Create classified raster (1-4)
            classified = np.zeros_like(suitability, dtype=np.uint8)
            
            for class_id, (class_name, (min_val, max_val)) in enumerate(thresholds.items(), start=1):
                mask = (suitability >= min_val) & (suitability < max_val)
                classified[mask] = class_id
                
                area_pixels = np.sum(mask)
                area_percent = (area_pixels / mask.size) * 100
                
                print(f"Class {class_id}: {class_name} ({min_val}-{max_val})")
                print(f"  Pixels: {area_pixels:,} ({area_percent:.1f}%)")
            
            print()
            
            # Save classified raster
            output_path = self.output_dir / 'suitability_classified.tif'
            profile.update(dtype=rasterio.uint8, nodata=0)
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(classified, 1)
        
        print(f"✅ Classified suitability saved: {output_path}\n")
        return output_path
    
    def generate_statistics(self, suitability_path: Path) -> Dict:
        """Generate summary statistics"""
        with rasterio.open(suitability_path) as src:
            data = src.read(1)
            
            # Exclude zero values (constraints/nodata)
            valid_data = data[data > 0]
            
            stats = {
                'min': float(valid_data.min()),
                'max': float(valid_data.max()),
                'mean': float(valid_data.mean()),
                'std': float(valid_data.std()),
                'median': float(np.median(valid_data)),
                'total_pixels': int(data.size),
                'valid_pixels': int(valid_data.size),
                'excluded_pixels': int(np.sum(data == 0))
            }
            
            return stats
    
    def save_metadata(self, weights: Dict[str, float], 
                     stats: Dict, output_name: str = 'analysis_metadata.json'):
        """Save analysis metadata"""
        metadata = {
            'weights': weights,
            'statistics': stats,
            'criteria': list(weights.keys()),
            'suitability_range': [0, 100],
            'classification': {
                1: 'Highly Suitable (70-100)',
                2: 'Moderately Suitable (50-70)',
                3: 'Marginally Suitable (30-50)',
                4: 'Not Suitable (0-30)'
            }
        }
        
        output_path = self.output_dir / output_name
        with open(output_path, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        print(f"✅ Metadata saved: {output_path}\n")
        return output_path


def main():
    """Example usage"""
    from pathlib import Path
    
    # Paths
    normalized_dir = Path.home() / 'suitability-engine' / 'data' / 'normalized'
    output_dir = Path.home() / 'suitability-engine' / 'data' / 'results'
    
    # Define normalized layers
    normalized_layers = {
        'elevation': normalized_dir / 'normalized_elevation.tif',
        'rainfall': normalized_dir / 'normalized_rainfall.tif',
        'temperature': normalized_dir / 'normalized_temperature.tif',
        'soil': normalized_dir / 'normalized_soil.tif',
        'slope': normalized_dir / 'normalized_slope.tif'
    }
    
    # Check files exist
    print("=== Checking Normalized Layers ===\n")
    all_exist = True
    for name, path in normalized_layers.items():
        exists = path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {name}: {path.name}")
        if not exists:
            all_exist = False
    print()
    
    if not all_exist:
        print("⚠️  Run normalize.py first!")
        return
    
    # Constraint layers
    constraint_dir = Path.home() / 'suitability-engine' / 'data' / 'raw' / 'constraints'
    constraints = [
        constraint_dir / 'bungoma_protected.tif'
    ]
    
    # Create engine
    engine = SuitabilityEngine(output_dir)
    
    # Calculate suitability
    suitability_path = engine.calculate_suitability(
        normalized_layers=normalized_layers,
        weights=None,  # Use defaults
        constraint_paths=constraints,
        output_name='cotton_suitability.tif'
    )
    
    # Classify
    classified_path = engine.classify_suitability(suitability_path)
    
    # Generate statistics
    stats = engine.generate_statistics(suitability_path)
    print("=== Statistics ===")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    print()
    
    # Save metadata
    engine.save_metadata(engine.default_weights, stats)
    
    print("\n=== Analysis Complete ===")
    print(f"Results saved to: {output_dir}")
    print("\nNext steps:")
    print("1. Open cotton_suitability.tif in QGIS")
    print("2. Open suitability_classified.tif for categorized view")
    print("3. Check analysis_metadata.json for details")
    print("4. Proceed to sensitivity analysis")


if __name__ == '__main__':
    main()