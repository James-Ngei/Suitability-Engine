"""
sensitivity_analysis.py
-----------------------
Tests how changes in criterion weights affect suitability results.
Helps understand which factors drive the analysis most.

Run from the project root:
    python src/sensitivity_analysis.py
"""

import sys
import numpy as np
import rasterio
from pathlib import Path
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
import pandas as pd
import json
from itertools import product

sys.path.append(str(Path(__file__).parent))
from config import load_config, create_county_dirs


class SensitivityAnalyzer:
    """
    Analyze how weight variations affect suitability outcomes.
    """

    def __init__(self, normalized_dir: Path, output_dir: Path,
                 base_weights: Dict[str, float]):
        self.normalized_dir = Path(normalized_dir)
        self.output_dir     = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.base_weights   = base_weights
        self.results        = []

    def load_normalized_layers(self) -> Tuple[Dict[str, np.ndarray], dict]:
        """Load all normalized layers into memory."""
        print("=== Loading Normalized Layers ===\n")

        layers  = {}
        profile = None

        for name in self.base_weights.keys():
            path = self.normalized_dir / f'normalized_{name}.tif'

            if not path.exists():
                print(f"⚠️  Warning: {path.name} not found, skipping...")
                continue

            with rasterio.open(path) as src:
                layers[name] = src.read(1).astype(np.float32)
                if profile is None:
                    profile = src.profile.copy()

            print(f"  ✅ Loaded: {name}")

        print()
        return layers, profile

    def calculate_suitability_array(self, layers: Dict[str, np.ndarray],
                                    weights: Dict[str, float]) -> np.ndarray:
        """
        Calculate suitability for given weights.
        Returns numpy array (not saved to disk, for speed).
        """
        suitability = np.zeros_like(list(layers.values())[0], dtype=np.float32)

        for name, weight in weights.items():
            if name in layers:
                suitability += layers[name] * weight  # data is 0-100, weight is fraction

        return np.clip(suitability, 0, 100)

    def run_one_at_a_time_analysis(self, layers: Dict[str, np.ndarray],
                                   weight_steps: int = 7) -> pd.DataFrame:
        """
        One-at-a-time (OAT) sensitivity analysis.
        Vary each criterion weight independently while keeping others proportional.
        """
        print("=== Running One-At-A-Time Sensitivity Analysis ===\n")

        weight_values = np.linspace(0.0, 1.0, weight_steps)
        records = []

        for criterion in self.base_weights.keys():
            if criterion not in layers:
                continue

            print(f"  Testing {criterion}...")

            for test_weight in weight_values:
                # Redistribute remaining weight proportionally among others
                remaining = 1.0 - test_weight
                other_criteria = [k for k in self.base_weights if k != criterion and k in layers]

                if other_criteria:
                    base_sum = sum(self.base_weights[k] for k in other_criteria)
                    adjusted = {k: (self.base_weights[k] / base_sum) * remaining
                                for k in other_criteria}
                else:
                    adjusted = {}

                adjusted[criterion] = test_weight

                suit = self.calculate_suitability_array(layers, adjusted)
                valid = suit[suit > 0]

                records.append({
                    'criterion':      criterion,
                    'weight':         test_weight,
                    'mean_suit':      float(valid.mean()) if valid.size else 0,
                    'suitable_pct':   float((suit >= 50).sum() / suit.size * 100),
                    'highly_suit_pct': float((suit >= 70).sum() / suit.size * 100),
                })

        df = pd.DataFrame(records)
        df.to_csv(self.output_dir / 'sensitivity_results.csv', index=False)
        print(f"\n  ✅ Results saved: sensitivity_results.csv\n")
        return df

    def calculate_elasticity(self, df: pd.DataFrame) -> pd.DataFrame:
        """Rank criteria by how much a weight change moves suitability."""
        records = []

        for criterion, grp in df.groupby('criterion'):
            grp = grp.sort_values('weight')
            weight_range = grp['weight'].max() - grp['weight'].min()
            suit_range   = grp['mean_suit'].max() - grp['mean_suit'].min()

            mid_weight = grp['weight'].median()
            mid_suit   = grp.loc[(grp['weight'] - mid_weight).abs().idxmin(), 'mean_suit']

            elasticity = (suit_range / (mid_suit + 1e-9)) / (weight_range + 1e-9)

            records.append({
                'criterion':  criterion,
                'suit_range': round(suit_range, 2),
                'elasticity': round(elasticity, 3),
                'influence':  'High' if elasticity > 1 else ('Medium' if elasticity > 0.5 else 'Low'),
            })

        result = (pd.DataFrame(records)
                  .sort_values('elasticity', ascending=False)
                  .reset_index(drop=True))
        result.to_csv(self.output_dir / 'elasticity_analysis.csv', index=False)
        print("  ✅ Elasticity saved: elasticity_analysis.csv")
        return result

    def plot_sensitivity_curves(self, df: pd.DataFrame) -> Path:
        """Plot weight vs mean suitability for each criterion."""
        fig, ax = plt.subplots(figsize=(10, 6))

        for criterion, grp in df.groupby('criterion'):
            grp = grp.sort_values('weight')
            ax.plot(grp['weight'], grp['mean_suit'], marker='o', label=criterion)

        ax.set_xlabel('Weight')
        ax.set_ylabel('Mean Suitability Score')
        ax.set_title('Sensitivity: Weight vs Mean Suitability')
        ax.legend()
        ax.grid(True, alpha=0.3)

        out = self.output_dir / 'sensitivity_curves.png'
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"  ✅ Plot saved: sensitivity_curves.png")
        return out

    def plot_suitable_area_sensitivity(self, df: pd.DataFrame) -> Path:
        """Plot how the ≥50% suitable area changes with weight."""
        fig, ax = plt.subplots(figsize=(10, 6))

        for criterion, grp in df.groupby('criterion'):
            grp = grp.sort_values('weight')
            ax.plot(grp['weight'], grp['suitable_pct'], marker='s', label=criterion)

        ax.set_xlabel('Weight')
        ax.set_ylabel('% Pixels ≥50 Suitability')
        ax.set_title('Sensitivity: Weight vs Suitable Area')
        ax.legend()
        ax.grid(True, alpha=0.3)

        out = self.output_dir / 'suitable_area_sensitivity.png'
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"  ✅ Plot saved: suitable_area_sensitivity.png")
        return out

    def generate_report(self, df: pd.DataFrame,
                        elasticity_df: pd.DataFrame) -> Path:
        """Write a plain-text summary report."""
        report_path = self.output_dir / 'sensitivity_report.txt'

        with open(report_path, 'w') as f:
            f.write("=" * 70 + "\n")
            f.write("SENSITIVITY ANALYSIS REPORT\n")
            f.write("=" * 70 + "\n\n")

            f.write("1. WEIGHT RANGES TESTED\n")
            f.write("-" * 70 + "\n")
            for criterion in df['criterion'].unique():
                grp = df[df['criterion'] == criterion]
                f.write(f"  {criterion:<15} "
                        f"weight {grp['weight'].min():.2f}–{grp['weight'].max():.2f}  "
                        f"suit {grp['mean_suit'].min():.1f}–{grp['mean_suit'].max():.1f}\n")
            f.write("\n")

            f.write("2. CRITERION INFLUENCE RANKING (by Elasticity)\n")
            f.write("-" * 70 + "\n")
            for idx, row in elasticity_df.iterrows():
                f.write(f"  {idx+1}. {row['criterion'].capitalize():<15} "
                        f"Elasticity: {row['elasticity']:>6.3f}  ({row['influence']})\n")
            f.write("\n")

            f.write("3. KEY FINDINGS\n")
            f.write("-" * 70 + "\n")
            most   = elasticity_df.iloc[0]['criterion']
            least  = elasticity_df.iloc[-1]['criterion']
            f.write(f"  • Most influential criterion:  {most.capitalize()}\n")
            f.write(f"  • Least influential criterion: {least.capitalize()}\n\n")

            f.write("4. RECOMMENDATIONS\n")
            f.write("-" * 70 + "\n")
            f.write("  • High influence criteria require more accurate data collection\n")
            f.write("  • Consider field validation for top 2 influential factors\n")
            f.write("  • Low influence criteria can use coarser resolution data\n\n")

            f.write("=" * 70 + "\n")
            f.write("For detailed results, see:\n")
            f.write("  - sensitivity_results.csv\n")
            f.write("  - sensitivity_curves.png\n")
            f.write("  - suitable_area_sensitivity.png\n")
            f.write("=" * 70 + "\n")

        print(f"  ✅ Report saved: sensitivity_report.txt\n")
        return report_path

    def run_full_analysis(self, weight_steps: int = 7) -> Dict:
        """Run complete sensitivity analysis."""
        print("\n" + "=" * 70)
        print("RUNNING FULL SENSITIVITY ANALYSIS")
        print("=" * 70 + "\n")

        layers, profile = self.load_normalized_layers()

        if not layers:
            print("❌ No normalized layers found! Run normalize.py first.")
            return {}

        df           = self.run_one_at_a_time_analysis(layers, weight_steps)
        curves_plot  = self.plot_sensitivity_curves(df)
        area_plot    = self.plot_suitable_area_sensitivity(df)
        elasticity_df = self.calculate_elasticity(df)
        report       = self.generate_report(df, elasticity_df)

        print("=" * 70)
        print("SENSITIVITY ANALYSIS COMPLETE")
        print("=" * 70)
        print(f"\nOutputs saved to: {self.output_dir}")
        print("\nGenerated files:")
        print("  1. sensitivity_results.csv      - Raw data")
        print("  2. sensitivity_curves.png       - Weight impact curves")
        print("  3. suitable_area_sensitivity.png - Area changes")
        print("  4. elasticity_analysis.csv      - Influence ranking")
        print("  5. sensitivity_report.txt       - Summary report")
        print()

        return {
            'results_csv':  self.output_dir / 'sensitivity_results.csv',
            'curves_plot':  curves_plot,
            'area_plot':    area_plot,
            'elasticity_csv': self.output_dir / 'elasticity_analysis.csv',
            'report':       report,
        }


def main():
    config = load_config()
    paths  = config['_paths']

    create_county_dirs(config)

    print("=" * 55)
    print(f"  SENSITIVITY ANALYSIS: {config['display_name'].upper()}")
    print("=" * 55)

    analyzer = SensitivityAnalyzer(
        normalized_dir=paths['normalized_dir'],
        output_dir=paths['sensitivity_dir'],
        base_weights=config['weights'],
    )

    outputs = analyzer.run_full_analysis(weight_steps=7)

    print("\n=== Next Steps ===")
    print("1. Review sensitivity_report.txt for key findings")
    print("2. Check sensitivity_curves.png to see weight impacts")
    print("3. Use elasticity ranking to prioritize data quality")


if __name__ == '__main__':
    main()