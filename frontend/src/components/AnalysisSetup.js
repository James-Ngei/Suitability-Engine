import React, { useState, useEffect, useRef } from 'react';

const LAYER_OPTIONS = [
  { id: 'suitability', label: 'Suitability result' },
  { id: 'elevation',   label: 'Elevation' },
  { id: 'rainfall',    label: 'Rainfall' },
  { id: 'temperature', label: 'Temperature' },
  { id: 'soil',        label: 'Soil clay' },
  { id: 'slope',       label: 'Slope' },
];

// ── Generic compact inline dropdown ──────────────────────────────────────────
function InlineDropdown({ label, value, options, onSelect, disabled, badge }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const h = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const current = options.find(o => o.id === value);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div style={{
        display:     'flex',
        alignItems:  'center',
        gap:         '6px',
        minWidth:    0,
        overflow:    'hidden',
      }}>
        {/* Label */}
        <span style={{
          fontSize:      '0.68rem',
          fontWeight:    600,
          color:         '#7a8f68',
          width:         '48px',
          minWidth:      '48px',
          flexShrink:    0,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          whiteSpace:    'nowrap',
          overflow:      'hidden',
        }}>
          {label}
        </span>

        {/* Trigger button */}
        <button
          onClick={() => !disabled && setOpen(o => !o)}
          disabled={disabled}
          style={{
            flex:          '1 1 0',
            minWidth:      0,
            display:       'flex',
            alignItems:    'center',
            justifyContent:'space-between',
            padding:       '4px 7px',
            background:    open ? '#f0f7e8' : '#ffffff',
            border:        `1px solid ${open ? '#3d7a22' : '#c8d8b0'}`,
            borderRadius:  '5px',
            cursor:        disabled ? 'not-allowed' : 'pointer',
            opacity:       disabled ? 0.5 : 1,
            transition:    'all 0.12s',
            gap:           '4px',
            overflow:      'hidden',
          }}
        >
          <span style={{
            fontSize:     '0.78rem',
            fontWeight:   600,
            color:        '#1a2010',
            flex:         '1 1 0',
            minWidth:     0,
            textAlign:    'left',
            overflow:     'hidden',
            textOverflow: 'ellipsis',
            whiteSpace:   'nowrap',
          }}>
            {current?.label || '—'}
          </span>

          {badge && (
            <span style={{
              fontSize:     '0.55rem',
              fontWeight:   700,
              background:   '#3d7a22',
              color:        '#ffffff',
              padding:      '1px 5px',
              borderRadius: '8px',
              flexShrink:   0,
              letterSpacing:'0.03em',
              whiteSpace:   'nowrap',
            }}>
              NEW
            </span>
          )}

          <span style={{
            fontSize:  '0.55rem',
            color:     '#8a9a78',
            flexShrink: 0,
            marginLeft: '2px',
          }}>
            {open ? '▲' : '▼'}
          </span>
        </button>
      </div>

      {/* Dropdown list */}
      {open && (
        <div style={{
          position:   'absolute',
          left:       '54px',
          right:      0,
          top:        'calc(100% + 3px)',
          background: '#ffffff',
          border:     '1.5px solid #c0d4a8',
          borderRadius:'6px',
          zIndex:     200,
          boxShadow:  '0 4px 16px rgba(0,0,0,0.13)',
          maxHeight:  '240px',
          overflowY:  'auto',
          minWidth:   '140px',
        }}>
          {options.map(opt => (
            <button
              key={opt.id}
              onClick={() => { onSelect(opt.id); setOpen(false); }}
              disabled={opt.disabled}
              style={{
                width:        '100%',
                display:      'flex',
                flexDirection:'column',
                gap:          '1px',
                padding:      '6px 10px',
                background:   opt.id === value ? '#e8f3dc' : '#ffffff',
                border:       'none',
                borderBottom: '1px solid #f0f5e8',
                cursor:       opt.disabled ? 'default' : 'pointer',
                textAlign:    'left',
                transition:   'background 0.1s',
              }}
              onMouseEnter={e => {
                if (opt.id !== value && !opt.disabled)
                  e.currentTarget.style.background = '#f5faf0';
              }}
              onMouseLeave={e => {
                if (opt.id !== value)
                  e.currentTarget.style.background = opt.id === value ? '#e8f3dc' : '#ffffff';
              }}
            >
              <span style={{
                fontSize:   '0.78rem',
                fontWeight: opt.id === value ? 700 : 500,
                color:      opt.disabled ? '#b0b8a8' : '#1a2010',
                display:    'flex',
                alignItems: 'center',
                gap:        '6px',
                whiteSpace: 'normal',
                wordBreak:  'break-word',
              }}>
                {opt.id === value && (
                  <span style={{ fontSize:'0.55rem', color:'#3d7a22', flexShrink: 0 }}>✓</span>
                )}
                {opt.label}
                {/* Show status dot only for counties that are actively loading */}
                {opt.status && opt.status === 'ready' && (
                  <span style={{ fontSize:'0.6rem', color:'#3d7a22', marginLeft:'auto', flexShrink:0 }}>●</span>
                )}
                {opt.status && (opt.status === 'fetching' || opt.status === 'pipeline') && (
                  <span style={{
                    fontSize:    '0.58rem',
                    color:       '#b8860b',
                    marginLeft:  'auto',
                    fontStyle:   'italic',
                    flexShrink:  0,
                    paddingLeft: '6px',
                  }}>
                    {opt.pct ? `${opt.pct}%` : 'loading…'}
                  </span>
                )}
              </span>
              {opt.hint && (
                <span style={{
                  fontSize:   '0.63rem',
                  color:      '#9aaa88',
                  paddingLeft: opt.id === value ? '14px' : '0',
                }}>
                  {opt.hint}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
function AnalysisSetup({
  apiBaseUrl,
  currentCounty,
  currentCrop,
  activeLayer,
  hasAnalysis,
  newResultReady,
  countyStatuses,   // { countyId: { status, pct } } — passed from App.js
  onCountyChange,
  onCropChange,
  onLayerChange,
}) {
  const [counties, setCounties] = useState([]);
  const [crops,    setCrops]    = useState([]);

  // Fetch county list (metadata only — no pipeline trigger)
  useEffect(() => {
    fetch(`${apiBaseUrl}/counties`)
      .then(r => r.json())
      .then(setCounties)
      .catch(() => {});
    fetch(`${apiBaseUrl}/crops`)
      .then(r => r.json())
      .then(setCrops)
      .catch(() => {});
  }, [apiBaseUrl]);

  // County selected — ONLY loads config/weights/boundary, no pipeline
  const handleCountySelect = async (countyId) => {
    if (countyId === currentCounty) return;
    try {
      // Load lightweight county+crop config (instant — just reads JSON)
      const countyParam = `county=${countyId}&crop=${currentCrop || 'cotton'}`;
      const [countyRes, criteriaRes] = await Promise.all([
        fetch(`${apiBaseUrl}/county?${countyParam}`).then(r => r.json()),
        fetch(`${apiBaseUrl}/criteria?${countyParam}`).then(r => r.json()),
      ]);
      // Merge criteria into countyRes so App.js gets everything in one object
      onCountyChange(countyId, { ...countyRes, criteria: criteriaRes });
    } catch (e) {
      console.warn('County config fetch failed:', e);
    }
  };

  // Crop selected
  const handleCropSelect = async (cropId) => {
    if (cropId === currentCrop) return;
    try {
      const countyParam = currentCounty ? `&county=${currentCounty}` : '';
      const [criteriaRes, countyRes] = await Promise.all([
        fetch(`${apiBaseUrl}/criteria?crop=${cropId}${countyParam}`).then(r => r.json()),
        fetch(`${apiBaseUrl}/county?crop=${cropId}${countyParam}`).then(r => r.json()),
      ]);
      const meta = crops.find(c => c.crop_id === cropId) || { crop_id: cropId };
      onCropChange(cropId, { ...meta, criteria: criteriaRes, ...countyRes });
    } catch {}
  };

  // Layer selected
  const handleLayerSelect = (layerId) => {
    onLayerChange(layerId);
  };

  // Merge server county list with live status from App.js polling
  const countyOptions = counties.map(c => {
    const liveStatus = countyStatuses?.[c.county] || {};
    const status = liveStatus.status || c.status || 'idle';
    const pct    = liveStatus.pct    || c.pct    || 0;
    return {
      id:     c.county,
      label:  c.display_name,
      status,
      pct,
      // Never disable — user can always switch county
      hint:
        status === 'fetching' || status === 'pipeline'
          ? `Fetching data… ${pct}%`
          : status === 'error'
          ? 'Fetch failed — will retry on Run Analysis'
          : null,
    };
  });

  const cropOptions = crops.map(c => ({
    id:    c.crop_id,
    label: c.display_name,
  }));

  const layerOptions = LAYER_OPTIONS.map(l => ({
    ...l,
    disabled: l.id === 'suitability' && !hasAnalysis,
    hint:     l.id === 'suitability' && !hasAnalysis ? 'Run analysis first' : null,
  }));

  // Show progress bar only when the currently-selected county is actively loading
  const currentStatus = countyStatuses?.[currentCounty] || {};
  const isCurrentLoading =
    currentStatus.status === 'fetching' || currentStatus.status === 'pipeline';

  return (
    <div className="panel-section" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div className="section-title">Analysis Setup</div>

      <InlineDropdown
        label="County"
        value={currentCounty}
        options={countyOptions}
        onSelect={handleCountySelect}
        disabled={false}
      />

      <InlineDropdown
        label="Crop"
        value={currentCrop}
        options={cropOptions}
        onSelect={handleCropSelect}
        disabled={false}
      />

      <InlineDropdown
        label="Layer"
        value={activeLayer}
        options={layerOptions}
        onSelect={handleLayerSelect}
        badge={newResultReady && activeLayer !== 'suitability'}
      />

      {/* Progress bar — only for active county data fetch */}
      {isCurrentLoading && (
        <div style={{ marginTop: '2px' }}>
          <div style={{
            display:        'flex',
            justifyContent: 'space-between',
            fontSize:       '0.63rem',
            color:          '#7a8f68',
            marginBottom:   '3px',
          }}>
            <span style={{ fontStyle: 'italic' }}>
              {currentStatus.status === 'fetching'
                ? 'Downloading layers…'
                : 'Running pipeline…'}
            </span>
            <span style={{ fontWeight: 700 }}>{currentStatus.pct || 0}%</span>
          </div>
          <div style={{
            height:       '3px',
            background:   '#dde5d4',
            borderRadius: '2px',
            overflow:     'hidden',
          }}>
            <div style={{
              height:       '100%',
              width:        `${currentStatus.pct || 0}%`,
              background:   '#3d7a22',
              borderRadius: '2px',
              transition:   'width 0.5s ease',
            }} />
          </div>
          <div style={{ fontSize: '0.6rem', color: '#9aaa88', marginTop: '3px', fontStyle: 'italic' }}>
            {currentStatus.message || ''}
          </div>
        </div>
      )}
    </div>
  );
}

export default AnalysisSetup;