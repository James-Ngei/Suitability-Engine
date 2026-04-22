import React, { useState, useEffect, useRef } from 'react';

/**
 * CountySelector
 * --------------
 * Shows all available counties with their load status.
 * Selecting a county that isn't loaded yet triggers POST /admin/load-county
 * and polls /status/{county} until ready.
 *
 * Props:
 *   apiBaseUrl       — string
 *   currentCounty    — string (county id)
 *   onCountyChange   — fn(countyId, countyConfig)
 */
function CountySelector({ apiBaseUrl, currentCounty, onCountyChange }) {
  const [counties,  setCounties]  = useState([]);
  const [expanded,  setExpanded]  = useState(false);
  const [loading,   setLoading]   = useState(false);
  const [loadingId, setLoadingId] = useState(null);
  const [progress,  setProgress]  = useState({});   // { countyId: { pct, message, status } }
  const pollRef   = useRef(null);
  const wrapRef   = useRef(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = e => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setExpanded(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Fetch county list
  const fetchCounties = () => {
    fetch(`${apiBaseUrl}/counties`)
      .then(r => r.json())
      .then(data => {
        setCounties(data);
        // Seed progress state from current server status
        const p = {};
        data.forEach(c => { p[c.county] = { status: c.status, pct: c.pct || 0 }; });
        setProgress(prev => ({ ...prev, ...p }));
      })
      .catch(() => {});
  };

  useEffect(() => {
    fetchCounties();
    const interval = setInterval(fetchCounties, 8000);
    return () => clearInterval(interval);
  }, [apiBaseUrl]);

  // Poll a specific county's status until ready or error
  const pollStatus = (countyId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const r    = await fetch(`${apiBaseUrl}/status/${countyId}`);
        const data = await r.json();
        setProgress(prev => ({
          ...prev,
          [countyId]: { status: data.status, pct: data.pct || 0, message: data.message },
        }));
        if (data.status === 'ready') {
          clearInterval(pollRef.current);
          setLoadingId(null);
          // Fetch the county config and notify parent
          const cfgRes = await fetch(`${apiBaseUrl}/county?county=${countyId}`);
          const cfg    = await cfgRes.json();
          onCountyChange(countyId, cfg);
          setExpanded(false);
          fetchCounties();
        } else if (data.status === 'error') {
          clearInterval(pollRef.current);
          setLoadingId(null);
        }
      } catch {}
    }, 2500);
  };

  const handleSelect = async (countyId) => {
    if (countyId === currentCounty) {
      setExpanded(false);
      return;
    }

    const entry = counties.find(c => c.county === countyId);
    if (!entry) return;

    if (entry.loaded || entry.status === 'ready') {
      // Already loaded — just switch
      const cfgRes = await fetch(`${apiBaseUrl}/county?county=${countyId}`);
      const cfg    = await cfgRes.json();
      onCountyChange(countyId, cfg);
      setExpanded(false);
      return;
    }

    // Need to fetch — trigger background task and poll
    setLoadingId(countyId);
    setProgress(prev => ({ ...prev, [countyId]: { status: 'fetching', pct: 1, message: 'Starting...' } }));

    try {
      await fetch(`${apiBaseUrl}/admin/load-county?county=${countyId}`, { method: 'POST' });
      pollStatus(countyId);
    } catch (e) {
      setLoadingId(null);
      setProgress(prev => ({ ...prev, [countyId]: { status: 'error', pct: 0, message: String(e) } }));
    }
  };

  const currentEntry = counties.find(c => c.county === currentCounty);
  const prog = progress[loadingId] || {};

  const statusColor = (status) => {
    if (status === 'ready')                  return '#3d7a22';
    if (status === 'fetching' || status === 'pipeline') return '#b8860b';
    if (status === 'error')                  return '#c05840';
    return '#8a9a78';
  };

  const statusDot = (status) => {
    if (status === 'ready')    return '●';
    if (status === 'fetching' || status === 'pipeline') return '◌';
    if (status === 'error')    return '✕';
    return '○';
  };

  return (
    <div className="panel-section" ref={wrapRef}>
      <div className="section-title">County</div>

      {/* Current county selector button */}
      <button
        className="county-selector-btn"
        onClick={() => setExpanded(e => !e)}
        disabled={!!loadingId}
      >
        <span className="county-selector-name">
          {currentEntry?.display_name || currentCounty || 'Select county'}
        </span>
        <span className="county-selector-crop">
          {currentEntry?.crop || ''}
        </span>
        <span className="county-selector-arrow">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Loading progress bar for a county being fetched */}
      {loadingId && (
        <div className="county-fetch-progress">
          <div className="county-fetch-label">
            <span>Preparing {counties.find(c => c.county === loadingId)?.display_name || loadingId}...</span>
            <span>{prog.pct || 0}%</span>
          </div>
          <div className="county-fetch-bar-track">
            <div
              className="county-fetch-bar-fill"
              style={{ width: `${prog.pct || 0}%` }}
            />
          </div>
          <div className="county-fetch-message">{prog.message || ''}</div>
        </div>
      )}

      {/* Dropdown list */}
      {expanded && (
        <div className="county-dropdown">
          {counties.length === 0 && (
            <div className="county-option-empty">No counties available</div>
          )}
          {counties.map(c => {
            const p       = progress[c.county] || {};
            const isActive = c.county === currentCounty;
            const isBusy   = c.county === loadingId;
            const st       = p.status || c.status || 'idle';
            return (
              <button
                key={c.county}
                className={`county-option ${isActive ? 'active' : ''} ${isBusy ? 'busy' : ''}`}
                onClick={() => handleSelect(c.county)}
                disabled={isBusy}
              >
                <div className="county-option-row">
                  <span
                    className="county-option-dot"
                    style={{ color: statusColor(st) }}
                    title={st}
                  >
                    {statusDot(st)}
                  </span>
                  <span className="county-option-name">{c.display_name}</span>
                  <span className="county-option-crop">{c.crop}</span>
                </div>
                {(st === 'fetching' || st === 'pipeline') && (
                  <div className="county-option-progress">
                    <div className="county-option-bar" style={{ width: `${p.pct || 0}%` }} />
                  </div>
                )}
                {st === 'idle' && (
                  <div className="county-option-hint">Click to fetch data</div>
                )}
                {st === 'error' && (
                  <div className="county-option-hint error">Fetch failed — click to retry</div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default CountySelector;