import React from 'react';

function Statistics({ result }) {
  if (!result) return null;

  const { statistics, classification } = result;

  return (
    <div className="statistics">
      <h3>📈 Analysis Results</h3>
      
      <div className="stat-item">
        <span className="stat-label">Mean Suitability</span>
        <span className="stat-value">{statistics.mean.toFixed(1)}</span>
      </div>
      
      <div className="stat-item">
        <span className="stat-label">Range</span>
        <span className="stat-value">
          {statistics.min.toFixed(1)} - {statistics.max.toFixed(1)}
        </span>
      </div>
      
      <div className="stat-item">
        <span className="stat-label">Std Deviation</span>
        <span className="stat-value">{statistics.std.toFixed(1)}</span>
      </div>

      <div className="classification">
        <div className="class-bar">
          <div className="class-label">
            <span>Highly Suitable (≥70)</span>
            <strong>{classification.highly_suitable_pct.toFixed(1)}%</strong>
          </div>
          <div className="class-progress">
            <div 
              className="class-fill highly-suitable"
              style={{ width: `${classification.highly_suitable_pct}%` }}
            />
          </div>
        </div>

        <div className="class-bar">
          <div className="class-label">
            <span>Moderately Suitable (50-70)</span>
            <strong>{classification.moderately_suitable_pct.toFixed(1)}%</strong>
          </div>
          <div className="class-progress">
            <div 
              className="class-fill moderately-suitable"
              style={{ width: `${classification.moderately_suitable_pct}%` }}
            />
          </div>
        </div>

        <div className="class-bar">
          <div className="class-label">
            <span>Marginally Suitable (30-50)</span>
            <strong>{classification.marginally_suitable_pct.toFixed(1)}%</strong>
          </div>
          <div className="class-progress">
            <div 
              className="class-fill marginally-suitable"
              style={{ width: `${classification.marginally_suitable_pct}%` }}
            />
          </div>
        </div>

        <div className="class-bar">
          <div className="class-label">
            <span>Not Suitable (&lt;30)</span>
            <strong>{classification.not_suitable_pct.toFixed(1)}%</strong>
          </div>
          <div className="class-progress">
            <div 
              className="class-fill not-suitable"
              style={{ width: `${classification.not_suitable_pct}%` }}
            />
          </div>
        </div>

        {classification.excluded_pct > 0 && (
          <div className="class-bar">
            <div className="class-label">
              <span>Excluded (Protected)</span>
              <strong>{classification.excluded_pct.toFixed(1)}%</strong>
            </div>
            <div className="class-progress">
              <div 
                className="class-fill"
                style={{ width: `${classification.excluded_pct}%`, background: '#999' }}
              />
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: '1rem', fontSize: '0.8rem', color: '#999', textAlign: 'center' }}>
        Updated: {new Date(result.timestamp).toLocaleTimeString()}
      </div>
    </div>
  );
}

export default Statistics;
