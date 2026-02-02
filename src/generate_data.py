"""
Generate REALISTIC sample data with spatial variation
This creates data where not everything scores 100
"""

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from pathlib import Path

# Bungoma bounding box
BBOX = {
    'west': 34.3,
    'south': 0.2,
    'east': 35.0,
    'north': 1.3
}

# Resolution: ~1km (0.01 degrees)
WIDTH = int((BBOX['east'] - BBOX['west']) / 0.01)
HEIGHT = int((BBOX['north'] - BBOX['south']) / 0.01)

OUTPUT_DIR = Path.home() / 'suitability-engine' / 'data' / 'raw'

def create_sample_raster(data, filename, description):
    """Create a sample GeoTIFF"""
    
    transform = from_bounds(
        BBOX['west'], BBOX['south'], 
        BBOX['east'], BBOX['north'],
        WIDTH, HEIGHT
    )
    
    filepath = OUTPUT_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with rasterio.open(
        filepath,
        'w',
        driver='GTiff',
        height=HEIGHT,
        width=WIDTH,
        count=1,
        dtype=data.dtype,
        crs='EPSG:4326',
        transform=transform,
        compress='lzw'
    ) as dst:
        dst.write(data, 1)
    
    print(f"✅ Created {description}: {filepath}")
    return filepath

def add_spatial_gradient(base_array, gradient_direction='north-south', strength=0.3):
    """Add realistic spatial gradient"""
    h, w = base_array.shape
    
    if gradient_direction == 'north-south':
        gradient = np.linspace(1.0 - strength, 1.0 + strength, h)[:, np.newaxis]
    elif gradient_direction == 'east-west':
        gradient = np.linspace(1.0 - strength, 1.0 + strength, w)[np.newaxis, :]
    else:  # diagonal
        gradient_ns = np.linspace(1.0 - strength/2, 1.0 + strength/2, h)[:, np.newaxis]
        gradient_ew = np.linspace(1.0 - strength/2, 1.0 + strength/2, w)[np.newaxis, :]
        gradient = gradient_ns * gradient_ew
    
    return base_array * gradient

def main():
    print(f"Generating REALISTIC sample data for Bungoma County")
    print(f"Extent: {BBOX}")
    print(f"Size: {WIDTH} x {HEIGHT} pixels\n")
    
    np.random.seed(42)
    
    # 1. ELEVATION - More variation (300-1800m)
    # Higher in east, lower in west (realistic for Western Kenya)
    elevation_base = np.random.randint(500, 1200, (HEIGHT, WIDTH), dtype=np.int16)
    
    # Add east-west gradient (higher elevation in east)
    for j in range(WIDTH):
        elevation_factor = 1.0 + (j / WIDTH) * 0.8  # 0-80% increase west to east
        elevation_base[:, j] = (elevation_base[:, j] * elevation_factor).astype(np.int16)
    
    # Add hills and valleys
    for i in range(HEIGHT):
        for j in range(WIDTH):
            terrain_var = int(200 * np.sin(i/5) * np.cos(j/5))
            elevation_base[i, j] = np.clip(elevation_base[i, j] + terrain_var, 300, 1800)
    
    elevation = elevation_base.astype(np.int16)
    create_sample_raster(elevation, 'elevation/bungoma_elevation.tif', 'Elevation (m)')
    
    # 2. RAINFALL - Variation (500-1500mm)
    # More rain at higher elevations and in south
    rainfall_base = 700 + (elevation - 800) * 0.4  # Orographic effect
    
    # Add north-south gradient (more rain in south)
    for i in range(HEIGHT):
        ns_factor = 1.0 + ((HEIGHT - i) / HEIGHT) * 0.3
        rainfall_base[i, :] *= ns_factor
    
    # Add random variation
    rainfall_noise = np.random.randint(-150, 150, (HEIGHT, WIDTH))
    rainfall = np.clip(rainfall_base + rainfall_noise, 500, 1500).astype(np.int16)
    
    create_sample_raster(rainfall, 'rainfall/bungoma_rainfall.tif', 'Rainfall (mm/year)')
    
    # 3. TEMPERATURE - Varies with elevation (16-30°C)
    # Temperature decreases with elevation (lapse rate ~0.6°C per 100m)
    temperature = 29 - (elevation - 300) * 0.0065
    
    # Add random micro-climate variation
    temp_noise = np.random.uniform(-1.5, 1.5, (HEIGHT, WIDTH))
    temperature = np.clip(temperature + temp_noise, 16, 30).astype(np.float32)
    
    create_sample_raster(temperature, 'temperature/bungoma_temperature.tif', 'Temperature (°C)')
    
    # 4. SOIL DRAINAGE - Mixed quality (not all perfect!)
    # Better drainage at higher elevations, poorer in valleys
    soil = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    
    # Base on elevation
    soil[elevation < 700] = 1    # Poor drainage in valleys (30%)
    soil[(elevation >= 700) & (elevation < 1200)] = 2  # Moderate (50%)
    soil[elevation >= 1200] = 3  # Good drainage on hills (20%)
    
    # Add some randomness (real world isn't perfectly stratified)
    random_mask = np.random.random((HEIGHT, WIDTH))
    soil[random_mask < 0.2] = np.random.choice([1, 2, 3], size=np.sum(random_mask < 0.2))
    
    create_sample_raster(soil, 'soil/bungoma_soil_drainage.tif', 'Soil Drainage (1-3)')
    
    # 5. SLOPE - Realistic variation (0-35°)
    # Calculate from elevation
    slope = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    
    # Gradient-based slope estimation
    for i in range(1, HEIGHT-1):
        for j in range(1, WIDTH-1):
            dz_dx = abs(elevation[i, j+1] - elevation[i, j-1]) / 2000  # ~2km
            dz_dy = abs(elevation[i+1, j] - elevation[i-1, j]) / 2000
            slope[i, j] = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2)) * 180 / np.pi
    
    # Add some noise
    slope += np.random.uniform(0, 2, (HEIGHT, WIDTH))
    slope = np.clip(slope, 0, 35).astype(np.float32)
    
    create_sample_raster(slope, 'constraints/bungoma_slope.tif', 'Slope (degrees)')
    
    # 6. PROTECTED AREAS - 15% of area
    protected = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    
    # Create realistic protected patches
    num_protected_areas = 8
    for _ in range(num_protected_areas):
        # Random center
        cy, cx = np.random.randint(10, HEIGHT-10), np.random.randint(10, WIDTH-10)
        # Random size
        radius = np.random.randint(3, 12)
        
        # Create circular protected area
        for i in range(max(0, cy-radius), min(HEIGHT, cy+radius)):
            for j in range(max(0, cx-radius), min(WIDTH, cx+radius)):
                if (i-cy)**2 + (j-cx)**2 <= radius**2:
                    protected[i, j] = 1
    
    create_sample_raster(protected, 'constraints/bungoma_protected.tif', 'Protected Areas (binary)')
    
    print("\n=== Sample Data Statistics ===")
    print(f"Elevation: {elevation.min()}-{elevation.max()}m (mean: {elevation.mean():.0f}m)")
    print(f"Rainfall: {rainfall.min()}-{rainfall.max()}mm (mean: {rainfall.mean():.0f}mm)")
    print(f"Temperature: {temperature.min():.1f}-{temperature.max():.1f}°C (mean: {temperature.mean():.1f}°C)")
    print(f"Soil: Poor={np.sum(soil==1)} ({np.sum(soil==1)/soil.size*100:.1f}%), "
          f"Moderate={np.sum(soil==2)} ({np.sum(soil==2)/soil.size*100:.1f}%), "
          f"Good={np.sum(soil==3)} ({np.sum(soil==3)/soil.size*100:.1f}%)")
    print(f"Slope: {slope.min():.1f}-{slope.max():.1f}° (mean: {slope.mean():.1f}°)")
    print(f"Protected: {np.sum(protected==1)/protected.size*100:.1f}% of area")
    
    print("\n=== Realistic Data Generation Complete ===")
    print("This data now has spatial variation - not everything scores 100!")
    print("\nNext steps:")
    print("1. Re-run alignment: python src/align_rasters.py")
    print("2. Re-run normalization: python src/normalize.py")
    print("3. Re-run suitability: python src/suitability.py")
    print("4. Re-run sensitivity: python src/sensitivity_analysis.py")

if __name__ == '__main__':
    main()