import React from 'react';

function WeightControls({ weights, criteria, onWeightChange, totalWeight }) {
  const getCriterionInfo = (name) => {
    return criteria.find(c => c.name === name) || { description: '', optimal_range: '' };
  };

  const weightOk = Math.abs(totalWeight - 1.0) <= 0.01;

  return (
    <div className="panel-section">
      <div className="section-title">Criterion Weights</div>

      {Object.keys(weights).map(criterion => {
        const info       = getCriterionInfo(criterion);
        const percentage = (weights[criterion] * 100).toFixed(0);

        return (
          <div key={criterion} className="weight-item">
            <div className="weight-header">
              <span className="criterion-name">{criterion}</span>
              <span className="weight-value">{percentage}%</span>
            </div>

            {info.optimal_range && (
              <div className="weight-description">
                {info.optimal_range}
              </div>
            )}

            <input
              type="range"
              min="0.05"
              max="0.50"
              step="0.01"
              value={weights[criterion]}
              onChange={e => onWeightChange(criterion, parseFloat(e.target.value))}
              className="weight-slider"
            />
          </div>
        );
      })}

      <div className="total-weight-row">
        <span>Total</span>
        <span className={weightOk ? '' : 'warn'}>
          {(totalWeight * 100).toFixed(0)}%
          {!weightOk && ' ⚠'}
        </span>
      </div>
    </div>
  );
}

export default WeightControls;