import React from 'react';

function WeightControls({ weights, criteria, onWeightChange, onReset }) {
  const getCriterionInfo = (name) => {
    return criteria.find(c => c.name === name) || { description: '', optimal_range: '' };
  };

  return (
    <div className="weight-controls">
      <h2>📊 Criterion Weights</h2>
      
      {Object.keys(weights).map(criterion => {
        const info = getCriterionInfo(criterion);
        const percentage = (weights[criterion] * 100).toFixed(0);
        
        return (
          <div key={criterion} className="weight-item">
            <div className="weight-header">
              <span className="criterion-name">{criterion}</span>
              <span className="weight-value">{percentage}%</span>
            </div>
            
            {info.description && (
              <div className="weight-description">
                {info.description}
                <br />
                <small><strong>Optimal:</strong> {info.optimal_range}</small>
              </div>
            )}
            
            <input
              type="range"
              min="0.05"
              max="0.50"
              step="0.01"
              value={weights[criterion]}
              onChange={(e) => onWeightChange(criterion, parseFloat(e.target.value))}
              className="weight-slider"
            />
          </div>
        );
      })}
      
      <div style={{ marginTop: '1rem', padding: '0.75rem', background: '#f5f5f5', borderRadius: '6px' }}>
        <small style={{ color: '#666' }}>
          <strong>Total:</strong> {(Object.values(weights).reduce((a, b) => a + b, 0) * 100).toFixed(0)}%
          {Math.abs(Object.values(weights).reduce((a, b) => a + b, 0) - 1.0) > 0.01 && (
            <span style={{ color: '#f44336', marginLeft: '0.5rem' }}>⚠ Must equal 100%</span>
          )}
        </small>
      </div>
    </div>
  );
}

export default WeightControls;
