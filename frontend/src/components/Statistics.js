import React from 'react';

function Statistics({ result }) {
  if (!result) {
    return (
      <div className="no-result-placeholder">
        <div className="icon">📊</div>
        <p>
          Results will<br />
          appear here<br />
          after analysis
        </p>
      </div>
    );
  }

  const { statistics: s, classification: c } = result;

  const classes = [
    { label: 'Highly suitable',    pct: c.highly_suitable_pct,     fill: 'fill-high',  dot: '#5a9e40', threshold: '≥ 70' },
    { label: 'Moderately suitable', pct: c.moderately_suitable_pct, fill: 'fill-mod',   dot: '#96c85a', threshold: '50–70' },
    { label: 'Marginal',            pct: c.marginally_suitable_pct, fill: 'fill-marg',  dot: '#d4a040', threshold: '30–50' },
    { label: 'Not suitable',        pct: c.not_suitable_pct,        fill: 'fill-not',   dot: '#c05840', threshold: '< 30' },
  ];

  if (c.excluded_pct > 0) {
    classes.push({
      label: 'Excluded', pct: c.excluded_pct, fill: 'fill-excl', dot: '#4a5040', threshold: 'Protected',
    });
  }

  return (
    <>
      {/* ── Score summary ── */}
      <div className="panel-section">
        <div className="section-title">Score Summary</div>
        <div className="stat-grid">
          <div className="stat-card">
            <div className="stat-card-label">Mean</div>
            <div className="stat-card-value">{s.mean.toFixed(1)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">Max</div>
            <div className="stat-card-value">{s.max.toFixed(1)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">Min</div>
            <div className="stat-card-value">{s.min.toFixed(1)}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">Std Dev</div>
            <div className="stat-card-value">{s.std.toFixed(1)}</div>
          </div>
        </div>
      </div>

      {/* ── Classification ── */}
      <div className="panel-section">
        <div className="section-title">Land Classification</div>
        {classes.map(({ label, pct, fill, dot, threshold }) => (
          <div key={label} className="class-item">
            <div className="class-row">
              <div style={{ display: 'flex', alignItems: 'center', flex: 1 }}>
                <div className="class-dot" style={{ background: dot }} />
                <span className="class-label-text">{label}</span>
              </div>
              <span className="class-pct">{pct.toFixed(1)}%</span>
            </div>
            <div className="class-bar-track">
              <div
                className={`class-bar-fill ${fill}`}
                style={{ width: `${Math.min(pct, 100)}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* ── Weights used ── */}
      <div className="panel-section">
        <div className="section-title">Weights Used</div>
        {Object.entries(result.weights_used).map(([name, w]) => (
          <div key={name} style={{
            display: 'flex', justifyContent: 'space-between',
            fontSize: '0.7rem', color: '#6b7a5e',
            fontFamily: 'Courier New, monospace',
            padding: '0.18rem 0',
          }}>
            <span style={{ textTransform: 'capitalize', color: '#8a9a78' }}>{name}</span>
            <span style={{ color: '#a0b870' }}>{(w * 100).toFixed(0)}%</span>
          </div>
        ))}
      </div>

      <div className="result-timestamp">
        {new Date(result.timestamp).toLocaleTimeString()}
      </div>
    </>
  );
}

export default Statistics;