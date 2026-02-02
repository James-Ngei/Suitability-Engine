"""
Sensitivity Analysis Module
Tests how changes in criterion weights affect suitability results
Helps understand which factors drive the analysis most
"""

import numpy as np
import rasterio
from pathlib import Path
from typing import Dict, List, Tuple
import matplotlib.pyplot as plt
import pandas as pd
import json
from itertools import product


class SensitivityAnalyzer:
    """
    Analyze how weight variations affect suitability outcomes
    """
    
    def __init__(self, normalized_dir: Path, output_dir: Path):
        self.normalized_dir = Path(normalized_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Base weights (control scenario)
        self.base_weights = {
            'rainfall': 0.25,
            'elevation': 0.20,
            'temperature': 0.20,
            'soil': 0.20,
            'slope': 0.15
        }
        
        self.results = []
    
    def load_normalized_layers(self) -> Dict[str, np.ndarray]:
        """Load all normalized layers into memory"""
        print("=== Loading Normalized Layers ===\n")
        
        layers = {}
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
        Calculate suitability for given weights
        Returns numpy array, not saved to disk (for speed)
        """
        suitability = np.zeros_like(list(layers.values())[0], dtype=np.float32)
        
        for name, weight in weights.items():
            if name in layers:
                suitability += layers[name] * weight  # Data is 0-100, weight is 0-1
        
        return np.clip(suitability, 0, 100)
    
    def vary_single_weight(self, layers: Dict[str, np.ndarray],
                          criterion: str, 
                          weight_range: List[float]) -> List[Dict]:
        """
        Vary a single criterion's weight, adjust others proportionally
        
        Args:
            layers: Loaded normalized layers
            criterion: Which criterion to vary
            weight_range: List of weights to test (e.g., [0.1, 0.15, 0.2, ..., 0.4])
            
        Returns:
            List of results with statistics
        """
        print(f"=== Varying '{criterion}' weight ===\n")
        
        results = []
        
        for test_weight in weight_range:
            # Adjust other weights proportionally
            adjusted_weights = self.base_weights.copy()
            adjusted_weights[criterion] = test_weight
            
            # Redistribute remaining weight among others
            remaining_weight = 1.0 - test_weight
            other_criteria = [k for k in adjusted_weights.keys() if k != criterion]
            other_total = sum(self.base_weights[k] for k in other_criteria)
            
            for other in other_criteria:
                # Proportional redistribution
                adjusted_weights[other] = (self.base_weights[other] / other_total) * remaining_weight
            
            # Calculate suitability with these weights
            suitability = self.calculate_suitability_array(layers, adjusted_weights)
            
            # Calculate statistics
            valid_data = suitability[suitability > 0]
            
            result = {
                'criterion': criterion,
                'weight': test_weight,
                'weights': adjusted_weights.copy(),
                'mean_suitability': float(valid_data.mean()),
                'std_suitability': float(valid_data.std()),
                'highly_suitable_pct': float((suitability >= 70).sum() / suitability.size * 100),
                'suitable_pct': float((suitability >= 50).sum() / suitability.size * 100)
            }
            
            results.append(result)
            
            print(f"  {criterion} weight = {test_weight:.2f}")
            print(f"    Mean suitability: {result['mean_suitability']:.2f}")
            print(f"    Highly suitable area: {result['highly_suitable_pct']:.1f}%")
        
        print()
        return results
    
    def run_one_at_a_time_analysis(self, layers: Dict[str, np.ndarray],
                                   weight_steps: int = 5) -> pd.DataFrame:
        """
        One-at-a-time sensitivity analysis
        Vary each criterion independently while holding others constant
        
        Args:
            weight_steps: Number of weight values to test per criterion
        """
        print("=== One-at-a-Time Sensitivity Analysis ===\n")
        
        all_results = []
        
        for criterion in self.base_weights.keys():
            # Test weights from 0.05 to 0.50 (5% to 50%)
            weight_range = np.linspace(0.05, 0.50, weight_steps)
            
            results = self.vary_single_weight(layers, criterion, weight_range)
            all_results.extend(results)
        
        # Convert to DataFrame
        df = pd.DataFrame(all_results)
        
        # Save to CSV
        csv_path = self.output_dir / 'sensitivity_results.csv'
        df.to_csv(csv_path, index=False)
        print(f"✅ Results saved: {csv_path}\n")
        
        return df
    
    def plot_sensitivity_curves(self, df: pd.DataFrame) -> Path:
        """
        Plot how mean suitability changes with each criterion's weight
        """
        print("=== Generating Sensitivity Plots ===\n")
        
        fig, axes = plt.subplots(2, 3, figsize=(18, 12))
        fig.suptitle('Sensitivity Analysis: Impact of Weight Variations', fontsize=16)
        
        criteria = list(self.base_weights.keys())
        
        for idx, criterion in enumerate(criteria):
            ax = axes[idx // 3, idx % 3]
            
            # Filter data for this criterion
            criterion_data = df[df['criterion'] == criterion]
            
            # Plot mean suitability vs weight
            ax.plot(criterion_data['weight'], 
                   criterion_data['mean_suitability'],
                   'b-o', linewidth=2, markersize=8, label='Mean Suitability')
            
            # Mark baseline weight
            baseline_weight = self.base_weights[criterion]
            closest_idx = (criterion_data['weight'] - baseline_weight).abs().idxmin()
            baseline_suit = criterion_data.loc[closest_idx, 'mean_suitability']
            
            ax.axvline(baseline_weight, color='r', linestyle='--', 
                      alpha=0.5, label=f'Baseline ({baseline_weight:.2f})')
            ax.plot(baseline_weight, baseline_suit, 'r*', markersize=15)
            
            # Formatting
            ax.set_xlabel(f'{criterion.capitalize()} Weight', fontsize=11)
            ax.set_ylabel('Mean Suitability Score', fontsize=11)
            ax.set_title(f'{criterion.capitalize()} Sensitivity', fontsize=12, fontweight='bold')
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=9)
            ax.set_ylim([0, 100])
        
        # Hide extra subplot
        axes[1, 2].axis('off')
        
        plt.tight_layout()
        
        plot_path = self.output_dir / 'sensitivity_curves.png'
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"✅ Sensitivity curves saved: {plot_path}\n")
        
        return plot_path
    
    def plot_suitable_area_sensitivity(self, df: pd.DataFrame) -> Path:
        """
        Plot how % of highly suitable area changes with weights
        """
        fig, ax = plt.subplots(figsize=(12, 7))
        
        criteria = list(self.base_weights.keys())
        
        for criterion in criteria:
            criterion_data = df[df['criterion'] == criterion]
            
            ax.plot(criterion_data['weight'], 
                   criterion_data['highly_suitable_pct'],
                   '-o', linewidth=2, markersize=6, label=criterion.capitalize())
            
            # Mark baseline
            baseline_weight = self.base_weights[criterion]
            ax.axvline(baseline_weight, color='gray', linestyle=':', alpha=0.3)
        
        ax.set_xlabel('Weight', fontsize=12)
        ax.set_ylabel('Highly Suitable Area (%)', fontsize=12)
        ax.set_title('Impact on Highly Suitable Area (Score ≥70)', fontsize=14, fontweight='bold')
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=10)
        
        plot_path = self.output_dir / 'suitable_area_sensitivity.png'
        plt.savefig(plot_path, dpi=300, bbox_inches='tight')
        print(f"✅ Suitable area sensitivity saved: {plot_path}\n")
        
        return plot_path
    
    def calculate_elasticity(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate elasticity: % change in output / % change in input
        Shows which criteria have the most influence
        """
        print("=== Calculating Elasticity ===\n")
        
        elasticities = []
        
        for criterion in self.base_weights.keys():
            criterion_data = df[df['criterion'] == criterion].copy()
            criterion_data = criterion_data.sort_values('weight')
            
            # Calculate percentage changes
            weight_changes = criterion_data['weight'].pct_change()
            suit_changes = criterion_data['mean_suitability'].pct_change()
            
            # Elasticity = % change in suitability / % change in weight
            elasticity = (suit_changes / weight_changes).replace([np.inf, -np.inf], np.nan)
            
            avg_elasticity = elasticity.mean()
            
            elasticities.append({
                'criterion': criterion,
                'elasticity': avg_elasticity,
                'influence': 'High' if abs(avg_elasticity) > 0.5 else 'Moderate' if abs(avg_elasticity) > 0.2 else 'Low'
            })
            
            print(f"  {criterion.capitalize()}: {avg_elasticity:.3f} ({elasticities[-1]['influence']} influence)")
        
        print()
        
        elasticity_df = pd.DataFrame(elasticities).sort_values('elasticity', key=abs, ascending=False)
        
        # Save
        csv_path = self.output_dir / 'elasticity_analysis.csv'
        elasticity_df.to_csv(csv_path, index=False)
        print(f"✅ Elasticity results saved: {csv_path}\n")
        
        return elasticity_df
    
    def generate_report(self, df: pd.DataFrame, elasticity_df: pd.DataFrame) -> Path:
        """Generate a comprehensive sensitivity analysis report"""
        report_path = self.output_dir / 'sensitivity_report.txt'
        
        with open(report_path, 'w') as f:
            f.write("=" * 70 + "\n")
            f.write("SENSITIVITY ANALYSIS REPORT\n")
            f.write("Cotton Farming Suitability - Bungoma County\n")
            f.write("=" * 70 + "\n\n")
            
            # 1. Baseline scenario
            f.write("1. BASELINE WEIGHTS\n")
            f.write("-" * 70 + "\n")
            for criterion, weight in self.base_weights.items():
                f.write(f"  {criterion.capitalize():<15} {weight:.2f} ({weight*100:.0f}%)\n")
            f.write("\n")
            
            # 2. Elasticity ranking
            f.write("2. CRITERION INFLUENCE RANKING (by Elasticity)\n")
            f.write("-" * 70 + "\n")
            for idx, row in elasticity_df.iterrows():
                f.write(f"  {idx+1}. {row['criterion'].capitalize():<15} "
                       f"Elasticity: {row['elasticity']:>6.3f}  ({row['influence']})\n")
            f.write("\n")
            
            # 3. Key findings
            f.write("3. KEY FINDINGS\n")
            f.write("-" * 70 + "\n")
            
            most_influential = elasticity_df.iloc[0]['criterion']
            least_influential = elasticity_df.iloc[-1]['criterion']
            
            f.write(f"  • Most influential criterion: {most_influential.capitalize()}\n")
            f.write(f"  • Least influential criterion: {least_influential.capitalize()}\n")
            f.write(f"\n")
            
            # 4. Recommendations
            f.write("4. RECOMMENDATIONS\n")
            f.write("-" * 70 + "\n")
            f.write("  • High influence criteria require more accurate data collection\n")
            f.write("  • Consider field validation for top 2 influential factors\n")
            f.write("  • Low influence criteria can use coarser resolution data\n")
            f.write("\n")
            
            f.write("=" * 70 + "\n")
            f.write("For detailed results, see:\n")
            f.write("  - sensitivity_results.csv\n")
            f.write("  - sensitivity_curves.png\n")
            f.write("  - suitable_area_sensitivity.png\n")
            f.write("=" * 70 + "\n")
        
        print(f"✅ Sensitivity report saved: {report_path}\n")
        return report_path
    
    def run_full_analysis(self, weight_steps: int = 7) -> Dict:
        """
        Run complete sensitivity analysis
        
        Args:
            weight_steps: Number of weight values to test per criterion
            
        Returns:
            Dictionary with paths to all outputs
        """
        print("\n" + "=" * 70)
        print("RUNNING FULL SENSITIVITY ANALYSIS")
        print("=" * 70 + "\n")
        
        # Load data
        layers, profile = self.load_normalized_layers()
        
        if not layers:
            print("❌ No normalized layers found! Run normalize.py first.")
            return {}
        
        # Run one-at-a-time analysis
        df = self.run_one_at_a_time_analysis(layers, weight_steps)
        
        # Generate visualizations
        curves_plot = self.plot_sensitivity_curves(df)
        area_plot = self.plot_suitable_area_sensitivity(df)
        
        # Calculate elasticity
        elasticity_df = self.calculate_elasticity(df)
        
        # Generate report
        report = self.generate_report(df, elasticity_df)
        
        print("=" * 70)
        print("SENSITIVITY ANALYSIS COMPLETE")
        print("=" * 70)
        print(f"\nOutputs saved to: {self.output_dir}")
        print("\nGenerated files:")
        print("  1. sensitivity_results.csv - Raw data")
        print("  2. sensitivity_curves.png - Weight impact curves")
        print("  3. suitable_area_sensitivity.png - Area changes")
        print("  4. elasticity_analysis.csv - Influence ranking")
        print("  5. sensitivity_report.txt - Summary report")
        print()
        
        return {
            'results_csv': self.output_dir / 'sensitivity_results.csv',
            'curves_plot': curves_plot,
            'area_plot': area_plot,
            'elasticity_csv': self.output_dir / 'elasticity_analysis.csv',
            'report': report
        }


def main():
    """Example usage"""
    from pathlib import Path
    
    # Paths
    normalized_dir = Path.home() / 'suitability-engine' / 'data' / 'normalized'
    output_dir = Path.home() / 'suitability-engine' / 'data' / 'sensitivity'
    
    # Create analyzer
    analyzer = SensitivityAnalyzer(normalized_dir, output_dir)
    
    # Run full analysis (test 7 weight values per criterion)
    outputs = analyzer.run_full_analysis(weight_steps=7)
    
    print("\n=== Next Steps ===")
    print("1. Review sensitivity_report.txt for key findings")
    print("2. Check sensitivity_curves.png to see weight impacts")
    print("3. Use elasticity ranking to prioritize data quality")
    print("4. Proceed to API/frontend development")


if __name__ == '__main__':
    main()