import React from 'react';

/**
 * ReportPanel (stateless)
 * -----------------------
 * Sits in the right panel below Statistics.
 * All state lives in App.js — this component only renders controls
 * and delegates actions via props.
 *
 * Props:
 *   hasAnalysis   — bool, whether an analysis has been run
 *   depth         — 'summary' | 'full'
 *   onDepthChange — fn(depth)
 *   generating    — bool
 *   error         — string | null
 *   pdfReady      — bool, whether a PDF blob URL exists
 *   onGenerate    — fn()
 *   onView        — fn()  — opens the overlay
 *   onDownload    — fn()
 */
function ReportPanel({
  hasAnalysis, depth, onDepthChange,
  generating, error, pdfReady,
  onGenerate, onView, onDownload,
}) {
  if (!hasAnalysis) {
    return (
      <div className="no-result-placeholder" style={{ minHeight: '120px' }}>
        <div className="icon" style={{ fontSize: '1.8rem' }}>📄</div>
        <p style={{ fontSize: '0.74rem' }}>
          Run an analysis to<br />enable report generation
        </p>
      </div>
    );
  }

  return (
    <div className="panel-section">
      <div className="section-title">PDF Report</div>

      {/* Depth selector */}
      <div className="report-depth-options" style={{ marginBottom: '0.6rem' }}>
        {['summary', 'full'].map(d => (
          <label key={d}
            className={`report-depth-option ${depth === d ? 'active' : ''}`}>
            <input type="radio" name="rp-depth" value={d}
              checked={depth === d} onChange={() => onDepthChange(d)} />
            <div className="report-depth-title">{d.charAt(0).toUpperCase() + d.slice(1)}</div>
            <div className="report-depth-desc">
              {d === 'summary' ? '2 pages · map · stats · narrative'
                               : '4 pages · adds criteria grid · methodology'}
            </div>
          </label>
        ))}
      </div>

      {error && <div className="report-error">⚠ {error}</div>}

      <button className="analyze-button" onClick={onGenerate} disabled={generating}>
        {generating ? '⏳ Generating...' : '📄 Generate Report'}
      </button>

      {pdfReady && (
        <div style={{ display:'flex', gap:'0.4rem', marginTop:'0.4rem' }}>
          <button className="reset-button" style={{ flex:1 }} onClick={onView}>
            View
          </button>
          <button className="reset-button" style={{ flex:1 }} onClick={onDownload}>
            ↓ Download
          </button>
        </div>
      )}

      {/* What's included */}
      <div style={{ marginTop:'0.65rem', borderTop:'1px solid #dde5d4', paddingTop:'0.55rem' }}>
        <div style={{ fontSize:'0.62rem', fontWeight:700, textTransform:'uppercase',
          letterSpacing:'0.08em', color:'#5a7a42', marginBottom:'0.4rem' }}>
          Includes
        </div>
        {[
          { label:'Suitability map',      fullOnly: false },
          { label:'Score statistics',     fullOnly: false },
          { label:'Classification chart', fullOnly: false },
          { label:'AI narrative',         fullOnly: false },
          { label:'Criterion layer maps', fullOnly: true  },
          { label:'Weight distribution',  fullOnly: true  },
          { label:'Methodology section',  fullOnly: true  },
        ].map(({ label, fullOnly }) => {
          const active = !fullOnly || depth === 'full';
          return (
            <div key={label} style={{
              display:'flex', alignItems:'center', gap:'0.4rem',
              fontSize:'0.71rem', color: active ? '#3a4f2a' : '#b0b8a8',
              padding:'0.15rem 0',
            }}>
              <span style={{ fontSize:'0.65rem', color: active ? '#3d7a22' : '#c8d4bc' }}>
                {active ? '✓' : '○'}
              </span>
              {label}
              {fullOnly && depth === 'summary' && (
                <span style={{ fontSize:'0.58rem', color:'#b0b8a8',
                  marginLeft:'auto', fontStyle:'italic' }}>full only</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ReportPanel;