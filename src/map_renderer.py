"""
map_renderer.py
---------------
Renders suitability GeoTIFFs as publication-quality static map PNGs
for inclusion in PDF reports.

Produces two outputs per analysis:
  - suitability_map_{analysis_id}.png   — main 4-class suitability map
  - criteria_grid_{analysis_id}.png     — 2×N grid of individual criterion layers

Colors exactly match the dashboard (MapView.js + /map-image endpoint):
  >= 70  →  #2e7d32  Highly suitable
  50–70  →  #66bb6a  Moderately suitable
  30–50  →  #ffa726  Marginally suitable
  <  30  →  #ef5350  Not suitable
   = 0   →  transparent / white (excluded)

Usage (from api.py):
    from map_renderer import render_suitability_map, render_criteria_grid

    map_png  = render_suitability_map(tif_path, boundary_path, analysis_id, config)
    grid_png = render_criteria_grid(normalized_layers, boundary_path, analysis_id, config)
"""

import numpy as np
import rasterio
from rasterio.plot import reshape_as_image
import matplotlib
matplotlib.use('Agg')                      # no display needed — server-side
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib_scalebar.scalebar import ScaleBar
import geopandas as gpd
from pathlib import Path
from typing import Dict, Optional
import warnings
warnings.filterwarnings('ignore', category=UserWarning)


# ── Colour system (matches dashboard exactly) ──────────────────────────────────

SUITABILITY_COLORS = {
    'highly':     '#2e7d32',
    'moderately': '#66bb6a',
    'marginally': '#ffa726',
    'not':        '#ef5350',
    'excluded':   '#f0f0f0',
}

SUITABILITY_CMAP = ListedColormap([
    SUITABILITY_COLORS['not'],
    SUITABILITY_COLORS['marginally'],
    SUITABILITY_COLORS['moderately'],
    SUITABILITY_COLORS['highly'],
])
SUITABILITY_NORM = BoundaryNorm([0, 30, 50, 70, 100], SUITABILITY_CMAP.N)

# Per-criterion colormaps — intuitive for each layer type
CRITERION_CMAPS = {
    'elevation':    'terrain',
    'rainfall':     'YlGnBu',
    'temperature':  'RdYlBu_r',
    'soil':         'YlOrBr',
    'slope':        'copper_r',
    'ndvi':         'RdYlGn',
    'default':      'viridis',
}

FIGURE_DPI = 150          # high enough for print, fast enough to generate
MAP_FIGSIZE = (10, 8)     # inches — A4-ish proportions
GRID_FIGSIZE = (12, 8)    # wider for the 2×N criterion grid


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_boundary(boundary_path: Path) -> Optional[gpd.GeoDataFrame]:
    """Load boundary GeoPackage, return None if not found."""
    if not boundary_path or not boundary_path.exists():
        return None
    gdf = gpd.read_file(boundary_path)
    if str(gdf.crs) != 'EPSG:4326':
        gdf = gdf.to_crs('EPSG:4326')
    return gdf


def _add_boundary(ax, boundary_gdf: Optional[gpd.GeoDataFrame]):
    """Overlay county boundary as a dashed green line."""
    if boundary_gdf is None:
        return
    boundary_gdf.boundary.plot(
        ax=ax,
        color='#1a5c0a',
        linewidth=1.2,
        linestyle='--',
        alpha=0.85,
    )


def _add_north_arrow(ax, x=0.96, y=0.96):
    """Add a simple north arrow in the top-right corner."""
    ax.annotate(
        'N',
        xy=(x, y), xytext=(x, y - 0.07),
        xycoords='axes fraction', textcoords='axes fraction',
        ha='center', va='center',
        fontsize=10, fontweight='bold', color='#1a1a1a',
        arrowprops=dict(arrowstyle='->', color='#1a1a1a', lw=1.5),
    )


def _add_scalebar(ax):
    """Add a metric scale bar using matplotlib-scalebar."""
    try:
        scalebar = ScaleBar(
            1,                          # 1 metre per unit (data is geographic degrees
                                        # but scalebar will compute from axes extent)
            units='m',
            dimension='si-length',
            location='lower left',
            pad=0.5,
            border_pad=0.5,
            sep=3,
            frameon=True,
            color='#1a1a1a',
            box_color='white',
            box_alpha=0.7,
            font_properties={'size': 8},
        )
        ax.add_artist(scalebar)
    except Exception:
        pass                            # scalebar is best-effort


def _add_suitability_legend(fig, ax, county_name: str = '', crop: str = ''):
    """
    Place legend OUTSIDE the axes in the right figure margin.
    Prevents any overlap with the map content.
    The axes width is reduced to 78% to leave room for the legend.
    """
    patches = [
        mpatches.Patch(color=SUITABILITY_COLORS['highly'],     label='Highly suitable  (≥ 70)'),
        mpatches.Patch(color=SUITABILITY_COLORS['moderately'], label='Moderately suitable  (50–70)'),
        mpatches.Patch(color=SUITABILITY_COLORS['marginally'], label='Marginally suitable  (30–50)'),
        mpatches.Patch(color=SUITABILITY_COLORS['not'],        label='Not suitable  (< 30)'),
        mpatches.Patch(color=SUITABILITY_COLORS['excluded'],   label='Excluded / no data',
                       linewidth=0.5, edgecolor='#aaaaaa'),
    ]
    legend = fig.legend(
        handles=patches,
        loc='center left',
        bbox_to_anchor=(0.80, 0.50),
        framealpha=0.95,
        edgecolor='#cccccc',
        fontsize=8,
        title=f'{crop} Suitability' if crop else 'Suitability Index',
        title_fontsize=8,
        borderpad=0.9,
        handlelength=1.2,
        handleheight=1.1,
    )
    legend.get_frame().set_linewidth(0.5)


def _style_axes(ax, title: str, extent=None):
    """Apply clean styling to a map axes."""
    ax.set_title(title, fontsize=11, fontweight='bold', pad=8, color='#1a1a1a')
    ax.set_xlabel('Longitude', fontsize=7, color='#555555')
    ax.set_ylabel('Latitude',  fontsize=7, color='#555555')
    ax.tick_params(labelsize=7, colors='#555555')
    for spine in ax.spines.values():
        spine.set_edgecolor('#cccccc')
        spine.set_linewidth(0.5)
    if extent:
        ax.set_xlim(extent[0], extent[2])
        ax.set_ylim(extent[1], extent[3])


# ── Main suitability map ───────────────────────────────────────────────────────

def render_suitability_map(
    tif_path: Path,
    boundary_path: Path,
    analysis_id: str,
    config: dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """
    Render the main suitability GeoTIFF as a 4-class map PNG.

    Args:
        tif_path:      Path to the suitability GeoTIFF (from /analyze).
        boundary_path: Path to county boundary .gpkg.
        analysis_id:   Used to name the output file.
        config:        Active county config dict.
        output_dir:    Where to save the PNG. Defaults to tif_path.parent.

    Returns:
        Path to the saved PNG.
    """
    if output_dir is None:
        output_dir = tif_path.parent

    output_path = output_dir / f'suitability_map_{analysis_id}.png'

    with rasterio.open(tif_path) as src:
        data      = src.read(1).astype(np.float32)
        bounds    = src.bounds
        transform = src.transform

    # Mask nodata (0 = excluded)
    masked = np.ma.masked_where(data == 0, data)

    # Geographic extent for axes
    extent_geo = [bounds.left, bounds.bottom, bounds.right, bounds.top]
    img_extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]  # imshow order

    boundary_gdf = _load_boundary(boundary_path)

    county_name = config.get('display_name', '')
    crop        = config.get('crop', '')

    fig, ax = plt.subplots(figsize=MAP_FIGSIZE, dpi=FIGURE_DPI)
    fig.patch.set_facecolor('white')

    # Background for excluded areas
    ax.set_facecolor(SUITABILITY_COLORS['excluded'])

    # Suitability raster
    im = ax.imshow(
        masked,
        cmap=SUITABILITY_CMAP,
        norm=SUITABILITY_NORM,
        extent=img_extent,
        origin='upper',
        interpolation='nearest',
        alpha=0.92,
    )

    _add_boundary(ax, boundary_gdf)
    _add_north_arrow(ax)
    _add_scalebar(ax)

    title = f'{county_name} — {crop} Suitability Analysis'
    _style_axes(ax, title, extent_geo)

    # Shrink axes to 78% width so legend sits in right margin without overlap
    ax.set_position([0.08, 0.10, 0.70, 0.82])
    _add_suitability_legend(fig, ax, county_name, crop)

    fig.savefig(output_path, dpi=FIGURE_DPI, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)

    print(f'  ✅ Suitability map saved: {output_path.name}')
    return output_path


# ── Criterion layer grid ───────────────────────────────────────────────────────

def render_criteria_grid(
    normalized_layer_paths: Dict[str, Path],
    boundary_path: Path,
    analysis_id: str,
    config: dict,
    output_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    Render each normalized criterion layer (0–100) as a 2×N grid PNG.

    Args:
        normalized_layer_paths: {layer_name: path_to_normalized_tif}
        boundary_path:          Path to county boundary .gpkg.
        analysis_id:            Used to name the output file.
        config:                 Active county config dict (for weights + descriptions).
        output_dir:             Where to save. Defaults to first layer's parent dir.

    Returns:
        Path to the saved PNG, or None if no layers found.
    """
    # Filter to existing layers
    available = {
        name: path for name, path in normalized_layer_paths.items()
        if path.exists()
    }
    if not available:
        return None

    n          = len(available)
    n_cols     = 2
    n_rows     = (n + 1) // 2
    figsize    = (GRID_FIGSIZE[0], n_rows * 3.8)

    if output_dir is None:
        output_dir = next(iter(available.values())).parent

    output_path = output_dir / f'criteria_grid_{analysis_id}.png'

    boundary_gdf = _load_boundary(boundary_path)
    weights      = config.get('weights', {})
    criteria_info = config.get('criteria_info', {})

    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize, dpi=FIGURE_DPI)
    fig.patch.set_facecolor('white')

    # Flatten axes — handle case where n_rows=1 (single row)
    axes_flat = np.array(axes).flatten()

    for idx, (name, path) in enumerate(available.items()):
        ax = axes_flat[idx]

        with rasterio.open(path) as src:
            data   = src.read(1).astype(np.float32)
            bounds = src.bounds

        masked     = np.ma.masked_where(data == 0, data)
        img_extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
        geo_extent = [bounds.left, bounds.bottom, bounds.right, bounds.top]

        cmap = CRITERION_CMAPS.get(name, CRITERION_CMAPS['default'])
        ax.set_facecolor('#f0f0f0')

        ax.imshow(
            masked,
            cmap=cmap,
            vmin=0, vmax=100,
            extent=img_extent,
            origin='upper',
            interpolation='nearest',
            alpha=0.9,
        )

        _add_boundary(ax, boundary_gdf)

        # Check for empty layer (all-zero after masking)
        valid_pixels = np.sum(~masked.mask) if np.ma.is_masked(masked) else np.sum(masked > 0)
        if valid_pixels == 0:
            ax.text(0.5, 0.5, f'{name.capitalize()}\nNo valid data',
                    transform=ax.transAxes, ha='center', va='center',
                    fontsize=9, color='#888888',
                    bbox=dict(boxstyle='round,pad=0.4', facecolor='#f5f5f5',
                              edgecolor='#cccccc', linewidth=0.5))
            ax.set_facecolor('#f5f5f5')
        else:
            # Minimal colorbar — only when there is data
            sm = plt.cm.ScalarMappable(cmap=cmap, norm=mcolors.Normalize(0, 100))
            sm.set_array([])
            cbar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
            cbar.ax.tick_params(labelsize=6)
            cbar.set_label('Score (0–100)', fontsize=6)

        # Title set AFTER colorbar so tight_layout respects it
        weight_pct = f'{weights.get(name, 0) * 100:.0f}%'
        optimal    = criteria_info.get(name, {}).get('optimal_range', '')
        title_str  = f'{name.capitalize()}  ·  weight {weight_pct}'
        if optimal:
            title_str += f'\n{optimal}'
        # _style_axes called with empty string so it does NOT overwrite
        # the title we just set above
        _style_axes(ax, '', geo_extent)
        # Re-apply title after _style_axes since it resets to ''
        ax.set_title(title_str, fontsize=8, fontweight='bold',
                     color='#1a1a1a', pad=8, loc='center')

    # If odd number of panels, hide the last slot and centre the final panel
    if n % 2 == 1:
        last_ax   = axes_flat[n - 1]
        empty_ax  = axes_flat[n]
        empty_ax.set_visible(False)
        # Move last panel to span centred position in its row
        pos       = last_ax.get_position()
        empty_pos = empty_ax.get_position()
        new_x     = (pos.x0 + empty_pos.x0 + empty_pos.width) / 2 - pos.width / 2
        last_ax.set_position([new_x, pos.y0, pos.width, pos.height])
    else:
        for idx in range(n, len(axes_flat)):
            axes_flat[idx].set_visible(False)

    county_name = config.get('display_name', '')
    crop        = config.get('crop', '')

    # Use subplots_adjust (not tight_layout + suptitle y>1) so titles stay visible
    fig.subplots_adjust(top=0.88, hspace=0.55, wspace=0.35,
                        left=0.08, right=0.96, bottom=0.06)
    fig.suptitle(
        f'{county_name} — {crop} Analysis: Individual Criterion Layers',
        fontsize=11, fontweight='bold', y=0.96, color='#1a1a1a',
    )

    fig.savefig(output_path, dpi=FIGURE_DPI, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)

    print(f'  ✅ Criteria grid saved: {output_path.name}')
    return output_path


# ── Classification bar chart ───────────────────────────────────────────────────

def render_classification_chart(
    classification: dict,
    analysis_id: str,
    output_dir: Path,
) -> Path:
    """
    Render a horizontal bar chart of suitability class percentages.

    Args:
        classification: The classification dict from /analyze response.
        analysis_id:    Used to name the output file.
        output_dir:     Where to save the PNG.

    Returns:
        Path to the saved PNG.
    """
    output_path = output_dir / f'classification_chart_{analysis_id}.png'

    labels = ['Highly suitable', 'Moderately suitable', 'Marginally suitable', 'Not suitable']
    keys   = ['highly_suitable_pct', 'moderately_suitable_pct',
              'marginally_suitable_pct', 'not_suitable_pct']
    colors = [
        SUITABILITY_COLORS['highly'],
        SUITABILITY_COLORS['moderately'],
        SUITABILITY_COLORS['marginally'],
        SUITABILITY_COLORS['not'],
    ]

    values = [classification.get(k, 0) for k in keys]

    # Add excluded if present
    excl = classification.get('excluded_pct', 0)
    if excl > 0.1:
        labels.append('Excluded')
        values.append(excl)
        colors.append(SUITABILITY_COLORS['excluded'])

    fig, ax = plt.subplots(figsize=(7, 3.2), dpi=FIGURE_DPI)
    fig.patch.set_facecolor('white')

    bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1],
                   height=0.55, edgecolor='white', linewidth=0.5)

    # Value labels on bars
    for bar, val in zip(bars, values[::-1]):
        if val > 1:
            ax.text(
                bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                f'{val:.1f}%',
                va='center', ha='left', fontsize=8, color='#333333',
            )

    ax.set_xlim(0, max(values) * 1.18 if values else 100)
    ax.set_xlabel('Percentage of analysis area (%)', fontsize=8, color='#555555')
    ax.tick_params(axis='y', labelsize=8)
    ax.tick_params(axis='x', labelsize=7)
    ax.set_title('Land suitability classification', fontsize=9,
                 fontweight='bold', pad=6, color='#1a1a1a')

    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(False)
    for spine in ['bottom', 'left']:
        ax.spines[spine].set_edgecolor('#dddddd')
        ax.spines[spine].set_linewidth(0.5)

    ax.xaxis.grid(True, linestyle='--', alpha=0.4, linewidth=0.5)
    ax.set_axisbelow(True)

    plt.tight_layout(pad=1.0)
    fig.savefig(output_path, dpi=FIGURE_DPI, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)

    print(f'  ✅ Classification chart saved: {output_path.name}')
    return output_path


# ── Weight contribution chart ──────────────────────────────────────────────────

def render_weight_chart(
    weights: dict,
    analysis_id: str,
    output_dir: Path,
) -> Path:
    """
    Render a horizontal bar chart showing criterion weight distribution.

    Args:
        weights:      {criterion: weight_float} from the analysis.
        analysis_id:  Used to name the output file.
        output_dir:   Where to save the PNG.

    Returns:
        Path to the saved PNG.
    """
    output_path = output_dir / f'weight_chart_{analysis_id}.png'

    labels = [k.capitalize() for k in weights.keys()]
    values = [v * 100 for v in weights.values()]

    # Use a consistent muted palette
    palette = ['#4a7c59', '#6aaa64', '#92c46a', '#c4e08a', '#e8f4b8']
    colors  = (palette * ((len(labels) // len(palette)) + 1))[:len(labels)]

    fig, ax = plt.subplots(figsize=(6, 2.8), dpi=FIGURE_DPI)
    fig.patch.set_facecolor('white')

    bars = ax.barh(labels[::-1], values[::-1], color=colors[::-1],
                   height=0.5, edgecolor='white', linewidth=0.5)

    for bar, val in zip(bars, values[::-1]):
        ax.text(
            bar.get_width() + 0.4, bar.get_y() + bar.get_height() / 2,
            f'{val:.0f}%',
            va='center', ha='left', fontsize=8, color='#333333',
        )

    ax.set_xlim(0, max(values) * 1.25 if values else 50)
    ax.set_xlabel('Weight (%)', fontsize=8, color='#555555')
    ax.tick_params(axis='y', labelsize=8)
    ax.tick_params(axis='x', labelsize=7)
    ax.set_title('Criterion weight distribution', fontsize=9,
                 fontweight='bold', pad=6, color='#1a1a1a')

    for spine in ['top', 'right']:
        ax.spines[spine].set_visible(False)
    for spine in ['bottom', 'left']:
        ax.spines[spine].set_edgecolor('#dddddd')
        ax.spines[spine].set_linewidth(0.5)

    ax.xaxis.grid(True, linestyle='--', alpha=0.4, linewidth=0.5)
    ax.set_axisbelow(True)

    plt.tight_layout(pad=1.0)
    fig.savefig(output_path, dpi=FIGURE_DPI, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)

    print(f'  ✅ Weight chart saved: {output_path.name}')
    return output_path


# ── Convenience: render all visuals for one analysis ──────────────────────────

def render_all(
    analysis_id: str,
    classification: dict,
    weights: dict,
    config: dict,
    paths: dict,
) -> dict:
    """
    Render all map and chart visuals for a completed analysis.
    Called from api.py after /analyze saves the GeoTIFF.

    Args:
        analysis_id:    ID string from the analysis.
        classification: Classification dict from the analysis response.
        weights:        Weights dict used in the analysis.
        config:         Active county config.
        paths:          The _paths dict from config (has all directory refs).

    Returns:
        Dict of {asset_name: Path} for each rendered file.
    """
    api_dir       = paths['api_results_dir']
    tif_path      = api_dir / f'suitability_{analysis_id}.tif'
    boundary_path = paths['boundary']

    outputs = {}

    if tif_path.exists():
        outputs['suitability_map'] = render_suitability_map(
            tif_path, boundary_path, analysis_id, config, api_dir
        )

    outputs['criteria_grid'] = render_criteria_grid(
        paths['normalized_layers'],
        boundary_path,
        analysis_id,
        config,
        api_dir,
    )

    outputs['classification_chart'] = render_classification_chart(
        classification, analysis_id, api_dir
    )

    outputs['weight_chart'] = render_weight_chart(
        weights, analysis_id, api_dir
    )

    return outputs


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    sys.path.append(str(Path(__file__).parent))
    from config import load_config

    config = load_config()
    paths  = config['_paths']

    # Find the most recent analysis GeoTIFF
    api_dir = paths['api_results_dir']
    tifs    = sorted(api_dir.glob('suitability_*.tif'))

    if not tifs:
        print('No analysis GeoTIFFs found.')
        print(f'Run the API and POST to /analyze first, then re-run this script.')
        raise SystemExit(1)

    latest_tif  = tifs[-1]
    analysis_id = latest_tif.stem.replace('suitability_', '')
    print(f'Rendering visuals for analysis: {analysis_id}')

    render_suitability_map(latest_tif, paths['boundary'], analysis_id, config)
    render_criteria_grid(paths['normalized_layers'], paths['boundary'], analysis_id, config)

    dummy_classification = {
        'highly_suitable_pct':     22.4,
        'moderately_suitable_pct': 35.1,
        'marginally_suitable_pct': 18.9,
        'not_suitable_pct':         8.3,
        'excluded_pct':            15.3,
    }
    render_classification_chart(dummy_classification, analysis_id, api_dir)
    render_weight_chart(config['weights'], analysis_id, api_dir)

    print(f'\nAll visuals saved to: {api_dir}')