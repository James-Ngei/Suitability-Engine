import React, { useState, useEffect, useRef } from 'react';

/**
 * AnalysisSetup
 * -------------
 * Replaces three separate panels (CountySelector, CropSelector, map LayerToggle)
 * with one compact panel containing three inline label+dropdown rows.
 *
 * Layout:
 *   County  [Kitui County        ▼]
 *   Crop    [Cotton              ▼]
 *   Layer   [Suitability result  ▼]
 *
 * Props:
 *   apiBaseUrl        string
 *   currentCounty     string
 *   currentCrop       string
 *   activeLayer       string
 *   hasAnalysis       bool
 *   newResultReady    bool     — true briefly after analysis completes (shows badge)
 *   onCountyChange    fn(countyId, countyConfig)
 *   onCropChange      fn(cropId, cropMeta)
 *   onLayerChange     fn(layerName)
 */

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
        gap:         '0',
        marginBottom: '0',
      }}>
        {/* Label */}
        <span style={{
          fontSize:      '0.68rem',
          fontWeight:    600,
          color:         '#7a8f68',
          width:         '46px',
          flexShrink:    0,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}>
          {label}
        </span>

        {/* Trigger */}
        <button
          onClick={() => !disabled && setOpen(o => !o)}
          disabled={disabled}
          style={{
            flex:          1,
            display:       'flex',
            alignItems:    'center',
            justifyContent:'space-between',
            padding:       '4px 8px',
            background:    open ? '#f0f7e8' : '#ffffff',
            border:        `1px solid ${open ? '#3d7a22' : '#c8d8b0'}`,
            borderRadius:  '5px',
            cursor:        disabled ? 'not-allowed' : 'pointer',
            opacity:       disabled ? 0.5 : 1,
            transition:    'all 0.12s',
            gap:           '6px',
          }}
        >
          <span style={{
            fontSize:   '0.78rem',
            fontWeight: 600,
            color:      '#1a2010',
            flex:       1,
            textAlign:  'left',
            overflow:   'hidden',
            textOverflow:'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {current?.label || '—'}
          </span>

          {/* New result badge */}
          {badge && (
            <span style={{
              fontSize:    '0.55rem',
              fontWeight:  700,
              background:  '#3d7a22',
              color:       '#ffffff',
              padding:     '1px 5px',
              borderRadius:'8px',
              flexShrink:  0,
              letterSpacing:'0.03em',
            }}>
              NEW
            </span>
          )}

          <span style={{ fontSize: '0.55rem', color: '#8a9a78', flexShrink: 0 }}>
            {open ? '▲' : '▼'}
          </span>
        </button>
      </div>

      {/* Dropdown */}
      {open && (
        <div style={{
          position:   'absolute',
          top:        'calc(100% + 3px)',
          left:       '46px',
          right:      0,
          background: '#ffffff',
          border:     '1.5px solid #c0d4a8',
          borderRadius:'6px',
          zIndex:     200,
          boxShadow:  '0 4px 16px rgba(0,0,0,0.13)',
          maxHeight:  '240px',
          overflowY:  'auto',
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
              onMouseEnter={e => { if (opt.id !== value && !opt.disabled) e.currentTarget.style.background = '#f5faf0'; }}
              onMouseLeave={e => { if (opt.id !== value) e.currentTarget.style.background = opt.id === value ? '#e8f3dc' : '#ffffff'; }}
            >
              <span style={{
                fontSize:   '0.78rem',
                fontWeight: opt.id === value ? 700 : 500,
                color:      opt.disabled ? '#b0b8a8' : '#1a2010',
                display:    'flex',
                alignItems: 'center',
                gap:        '6px',
              }}>
                {opt.id === value && (
                  <span style={{ fontSize:'0.55rem', color:'#3d7a22' }}>✓</span>
                )}
                {opt.label}
                {opt.status && opt.status !== 'ready' && opt.status !== 'idle' && (
                  <span style={{
                    fontSize:'0.58rem', color:'#b8860b',
                    marginLeft:'auto', fontStyle:'italic',
                  }}>
                    {opt.status === 'fetching' || opt.status === 'pipeline'
                      ? `${opt.pct||0}%` : opt.status}
                  </span>
                )}
              </span>
              {opt.hint && (
                <span style={{ fontSize:'0.63rem', color:'#9aaa88', paddingLeft: opt.id === value ? '14px' : '0' }}>
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
  onCountyChange,
  onCropChange,
  onLayerChange,
}) {
  const [counties,      setCounties]      = useState([]);
  const [crops,         setCrops]         = useState([]);
  const [countyLoading, setCountyLoading] = useState(null); // county id being loaded
  const pollRef = useRef(null);

  // Fetch county + crop lists
  useEffect(() => {
    fetch(`${apiBaseUrl}/counties`).then(r => r.json()).then(data => {
      setCounties(data);
    }).catch(() => {});
    fetch(`${apiBaseUrl}/crops`).then(r => r.json()).then(setCrops).catch(() => {});
  }, [apiBaseUrl]);

  // Poll county status while loading
  const pollCounty = (countyId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r    = await fetch(`${apiBaseUrl}/status/${countyId}`);
        const data = await r.json();
        // Update counties list with new status
        setCounties(prev => prev.map(c =>
          c.county === countyId ? { ...c, status: data.status, pct: data.pct } : c
        ));
        if (data.status === 'ready') {
          clearInterval(pollRef.current);
          setCountyLoading(null);
          const cfgR = await fetch(`${apiBaseUrl}/county?county=${countyId}`);
          const cfg  = await cfgR.json();
          onCountyChange(countyId, cfg);
        } else if (data.status === 'error') {
          clearInterval(pollRef.current);
          setCountyLoading(null);
        }
      } catch {}
    }, 2500);
  };

  // County selected
  const handleCountySelect = async (countyId) => {
    if (countyId === currentCounty) return;
    const entry = counties.find(c => c.county === countyId);
    if (!entry) return;

    if (entry.loaded || entry.status === 'ready') {
      const r   = await fetch(`${apiBaseUrl}/county?county=${countyId}`).then(r => r.json());
      onCountyChange(countyId, r);
      return;
    }

    // Trigger load
    setCountyLoading(countyId);
    setCounties(prev => prev.map(c =>
      c.county === countyId ? { ...c, status: 'fetching', pct: 1 } : c
    ));
    try {
      await fetch(`${apiBaseUrl}/admin/load-county?county=${countyId}`, { method: 'POST' });
      pollCounty(countyId);
    } catch {
      setCountyLoading(null);
    }
  };

  // Crop selected
  const handleCropSelect = async (cropId) => {
    if (cropId === currentCrop) return;
    try {
      const countyParam = currentCounty ? `&county=${currentCounty}` : '';
      const [criteriaR, countyR] = await Promise.all([
        fetch(`${apiBaseUrl}/criteria?crop=${cropId}${countyParam}`).then(r => r.json()),
        fetch(`${apiBaseUrl}/county?crop=${cropId}${countyParam}`).then(r => r.json()),
      ]);
      const meta = crops.find(c => c.crop_id === cropId) || { crop_id: cropId };
      onCropChange(cropId, { ...meta, criteria: criteriaR, ...countyR });
    } catch {}
  };

  // Layer selected
  const handleLayerSelect = (layerId) => {
    onLayerChange(layerId);
  };

  // Build county options
  const countyOptions = counties.map(c => ({
    id:       c.county,
    label:    c.display_name,
    status:   c.status,
    pct:      c.pct,
    disabled: c.county === countyLoading && c.status !== 'ready',
    hint:     c.status === 'fetching' || c.status === 'pipeline'
              ? `Fetching data… ${c.pct||0}%`
              : c.status === 'idle'
              ? 'Click to fetch data'
              : c.status === 'error'
              ? 'Fetch failed — click to retry'
              : null,
  }));

  // Build crop options — just display_name, no scientific name
  const cropOptions = crops.map(c => ({
    id:    c.crop_id,
    label: c.display_name,
  }));

  // Build layer options — suitability only available after analysis
  const layerOptions = LAYER_OPTIONS.map(l => ({
    ...l,
    disabled: l.id === 'suitability' && !hasAnalysis,
    hint:     l.id === 'suitability' && !hasAnalysis ? 'Run analysis first' : null,
  }));

  // Loading progress for county being fetched
  const loadingEntry = countyLoading
    ? counties.find(c => c.county === countyLoading)
    : null;

  return (
    <div className="panel-section" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div className="section-title">Analysis Setup</div>

      <InlineDropdown
        label="County"
        value={currentCounty}
        options={countyOptions}
        onSelect={handleCountySelect}
        disabled={!!countyLoading}
      />

      <InlineDropdown
        label="Crop"
        value={currentCrop}
        options={cropOptions}
        onSelect={handleCropSelect}
        disabled={!!countyLoading}
      />

      <InlineDropdown
        label="Layer"
        value={activeLayer}
        options={layerOptions}
        onSelect={handleLayerSelect}
        badge={newResultReady && activeLayer !== 'suitability'}
      />

      {/* County fetch progress */}
      {loadingEntry && (loadingEntry.status === 'fetching' || loadingEntry.status === 'pipeline') && (
        <div style={{ marginTop: '2px' }}>
          <div style={{
            display:        'flex',
            justifyContent: 'space-between',
            fontSize:       '0.63rem',
            color:          '#7a8f68',
            marginBottom:   '3px',
          }}>
            <span style={{ fontStyle: 'italic' }}>
              {loadingEntry.status === 'fetching' ? 'Fetching from Planetary Computer…' : 'Running pipeline…'}
            </span>
            <span style={{ fontWeight: 700 }}>{loadingEntry.pct || 0}%</span>
          </div>
          <div style={{ height: '3px', background: '#dde5d4', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{
              height:     '100%',
              width:      `${loadingEntry.pct || 0}%`,
              background: '#3d7a22',
              borderRadius:'2px',
              transition: 'width 0.5s ease',
            }} />
          </div>
        </div>
      )}
    </div>
  );
}

export default AnalysisSetup;