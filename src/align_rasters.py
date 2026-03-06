"""
Raster Alignment Module
Ensures all input layers have identical:
- CRS (Coordinate Reference System)
- Extent (bounding box)
- Resolution (pixel size)
- Array dimensions

This is critical for weighted overlay analysis.
"""

import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.enums import Resampling
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple


class RasterAligner:
    """Align multiple rasters to a common grid"""
    
    def __init__(self, output_dir: Path, target_crs: str = 'EPSG:4326', 
                 target_resolution: float = 0.01):
        """
        Initialize aligner
        
        Args:
            output_dir: Where to save aligned rasters
            target_crs: Target coordinate system (default WGS84)
            target_resolution: Target pixel size in degrees (0.01 = ~1km)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.target_crs = target_crs
        self.target_resolution = target_resolution
        self.target_bounds = None
        self.target_shape = None
        
    def inspect_raster(self, filepath: Path) -> Dict:
        """Get metadata from a raster file"""
        with rasterio.open(filepath) as src:
            info = {
                'path': filepath,
                'crs': str(src.crs),
                'bounds': src.bounds,
                'shape': (src.height, src.width),
                'resolution': (src.res[0], src.res[1]),
                'dtype': src.dtypes[0],
                'nodata': src.nodata
            }
        return info
    
    def inspect_all(self, raster_files: List[Path]) -> List[Dict]:
        """Inspect all input rasters"""
        print("=== Inspecting Input Rasters ===\n")
        
        all_info = []
        for raster in raster_files:
            info = self.inspect_raster(raster)
            all_info.append(info)
            
            print(f"File: {raster.name}")
            print(f"  CRS: {info['crs']}")
            print(f"  Bounds: {info['bounds']}")
            print(f"  Shape: {info['shape']}")
            print(f"  Resolution: {info['resolution']}")
            print()
        
        return all_info
    
    def calculate_common_extent(self, raster_files: List[Path]) -> Tuple:
        """
        Calculate the intersection extent of all rasters
        Returns: (minx, miny, maxx, maxy)
        """
        print("=== Calculating Common Extent ===")
        
        # Get bounds of all rasters in target CRS
        all_bounds = []
        for raster in raster_files:
            with rasterio.open(raster) as src:
                if str(src.crs) == self.target_crs:
                    bounds = src.bounds
                else:
                    # Transform bounds to target CRS
                    left, bottom, right, top = src.bounds
                    # For simplicity, use the bounds as-is if already in geographic
                    bounds = src.bounds
                
                all_bounds.append(bounds)
        
        # Find intersection (common area)
        minx = max(b.left for b in all_bounds)
        miny = max(b.bottom for b in all_bounds)
        maxx = min(b.right for b in all_bounds)
        maxy = min(b.top for b in all_bounds)
        
        self.target_bounds = (minx, miny, maxx, maxy)
        
        # Calculate target shape based on resolution
        width = int((maxx - minx) / self.target_resolution)
        height = int((maxy - miny) / self.target_resolution)
        self.target_shape = (height, width)
        
        print(f"Common extent: {self.target_bounds}")
        print(f"Target shape: {self.target_shape}")
        print(f"Target resolution: {self.target_resolution}°\n")
        
        return self.target_bounds
    
    def align_raster(self, input_path: Path, output_name: str) -> Path:
        """
        Align a single raster to the target grid
        
        Args:
            input_path: Path to input raster
            output_name: Name for output file
            
        Returns:
            Path to aligned raster
        """
        output_path = self.output_dir / output_name
        
        print(f"Aligning: {input_path.name} -> {output_name}")
        
        with rasterio.open(input_path) as src:
            # Define target transform
            from rasterio.transform import from_bounds
            target_transform = from_bounds(
                self.target_bounds[0], self.target_bounds[1],
                self.target_bounds[2], self.target_bounds[3],
                self.target_shape[1], self.target_shape[0]
            )
            
            # Prepare output array
            aligned_data = np.zeros(self.target_shape, dtype=src.dtypes[0])
            
            # Reproject to target grid
            reproject(
                source=rasterio.band(src, 1),
                destination=aligned_data,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=target_transform,
                dst_crs=self.target_crs,
                resampling=Resampling.bilinear
            )
            
            # Write aligned raster
            profile = {
                'driver': 'GTiff',
                'dtype': src.dtypes[0],
                'width': self.target_shape[1],
                'height': self.target_shape[0],
                'count': 1,
                'crs': self.target_crs,
                'transform': target_transform,
                'compress': 'lzw',
                'nodata': src.nodata
            }
            
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(aligned_data, 1)
        
        print(f"  ✅ Saved to: {output_path}\n")
        return output_path
    
    def align_all(self, raster_dict: Dict[str, Path]) -> Dict[str, Path]:
        """
        Align all rasters
        
        Args:
            raster_dict: {'layer_name': Path} mapping
            
        Returns:
            Dictionary of aligned raster paths
        """
        # First, calculate common extent
        self.calculate_common_extent(list(raster_dict.values()))
        
        # Align each raster
        print("=== Aligning Rasters ===\n")
        aligned_paths = {}
        
        for name, path in raster_dict.items():
            output_name = f"aligned_{name}.tif"
            aligned_path = self.align_raster(path, output_name)
            aligned_paths[name] = aligned_path
        
        print("=== Alignment Complete ===")
        print(f"All aligned rasters saved to: {self.output_dir}\n")
        
        return aligned_paths
    
    def verify_alignment(self, aligned_paths: Dict[str, Path]) -> bool:
        """Verify all rasters are properly aligned"""
        print("=== Verifying Alignment ===\n")
        
        reference = None
        all_match = True
        
        for name, path in aligned_paths.items():
            with rasterio.open(path) as src:
                if reference is None:
                    reference = {
                        'crs': src.crs,
                        'bounds': src.bounds,
                        'shape': (src.height, src.width),
                        'transform': src.transform
                    }
                    print(f"Reference: {name}")
                    print(f"  CRS: {reference['crs']}")
                    print(f"  Bounds: {reference['bounds']}")
                    print(f"  Shape: {reference['shape']}\n")
                else:
                    # Check if matches reference
                    matches = (
                        src.crs == reference['crs'] and
                        src.bounds == reference['bounds'] and
                        (src.height, src.width) == reference['shape']
                    )
                    
                    status = "✅" if matches else "❌"
                    print(f"{status} {name}: {'Aligned' if matches else 'MISMATCH'}")
                    
                    if not matches:
                        all_match = False
                        print(f"  Expected shape: {reference['shape']}, Got: {(src.height, src.width)}")
                        print(f"  Expected bounds: {reference['bounds']}, Got: {src.bounds}")
        
        print()
        if all_match:
            print("✅ All rasters properly aligned!")
        else:
            print("❌ Alignment verification failed!")
        
        return all_match


def main():
    """Example usage"""
    from pathlib import Path
    
    # Define input rasters
    data_dir = Path.home() / 'suitability-engine' / 'data' / 'preprocessed'
    
    rasters = {
        'elevation': data_dir / 'bungoma_elevation.tif',
        'rainfall': data_dir / 'bungoma_rainfall.tif',
        'temperature': data_dir / 'bungoma_temperature.tif',
        'soil': data_dir / 'bungoma_soil.tif',
        'slope': data_dir / 'bungoma_slope.tif',
        'protected': data_dir / 'bungoma_protected.tif'
    }
    
    # Check all files exist
    print("=== Checking Input Files ===\n")
    for name, path in rasters.items():
        exists = "✅" if path.exists() else "❌"
        print(f"{exists} {name}: {path}")
    print()
    
    # Create aligner
    output_dir = Path.home() / 'suitability-engine' / 'data' / 'processed'
    aligner = RasterAligner(output_dir, target_resolution=0.01)
    
    # Inspect all rasters
    aligner.inspect_all(list(rasters.values()))
    
    # Align all rasters
    aligned = aligner.align_all(rasters)
    
    # Verify alignment
    aligner.verify_alignment(aligned)
    
    print("\n=== Next Steps ===")
    print("1. Open aligned rasters in QGIS")
    print("2. Verify they stack perfectly")
    print("3. Proceed to normalization")


if __name__ == '__main__':
    main()