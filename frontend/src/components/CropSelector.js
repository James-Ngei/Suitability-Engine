import React, { useState, useEffect, useRef } from 'react';

/**
 * CropSelector
 * ------------
 * Dropdown to pick the active crop. Fetches list from GET /crops.
 * Selecting a crop reloads weights + criteria from GET /criteria?crop=X
 *
 * Props:
 *   apiBaseUrl     — string
 *   currentCrop    — string (crop_id)
 *   currentCounty  — string (county id, for criteria fetch)
 *   onCropChange   — fn(cropId, cropMeta) where cropMeta = { crop_id, display_name, ... }
 */
function CropSelector({ apiBaseUrl, currentCrop, currentCounty, onCropChange }) {
  const [crops,    setCrops]    = useState([]);
  const [expanded, setExpanded] = useState(false);
  const wrapRef = useRef(null);

  // Close on outside click
  useEffect(() => {
    const h = e => {
      if (wrapRef.current && !wrapRef.current.contains(e.target))
        setExpanded(false);
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  useEffect(() => {
    fetch(`${apiBaseUrl}/crops`)
      .then(r => r.json())
      .then(data => setCrops(data))
      .catch(() => {});
  }, [apiBaseUrl]);

  const handleSelect = async (cropId) => {
    if (cropId === currentCrop) { setExpanded(false); return; }
    try {
      const countyParam = currentCounty ? `&county=${currentCounty}` : '';
      const [criteriaRes] = await Promise.all([
        fetch(`${apiBaseUrl}/criteria?crop=${cropId}${countyParam}`).then(r => r.json()),
      ]);
      const meta = crops.find(c => c.crop_id === cropId) || { crop_id: cropId };
      onCropChange(cropId, { ...meta, criteria: criteriaRes });
    } catch (e) {
      console.warn('Crop switch failed:', e);
    }
    setExpanded(false);
  };

  const current = crops.find(c => c.crop_id === currentCrop);

  return (
    <div className="panel-section" ref={wrapRef}>
      <div className="section-title">Crop</div>

      <button
        className="county-selector-btn"
        onClick={() => setExpanded(e => !e)}
      >
        <span className="county-selector-name">
          {current?.display_name || currentCrop || 'Select crop'}
        </span>
        <span className="county-selector-crop" style={{ fontStyle: 'normal', fontSize: '0.65rem' }}>
          {current?.scientific_name || ''}
        </span>
        <span className="county-selector-arrow">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="county-dropdown">
          {crops.length === 0 && (
            <div className="county-option-empty">No crops available</div>
          )}
          {crops.map(c => (
            <button
              key={c.crop_id}
              className={`county-option ${c.crop_id === currentCrop ? 'active' : ''}`}
              onClick={() => handleSelect(c.crop_id)}
            >
              <div className="county-option-row">
                <span className="county-option-name">{c.display_name}</span>
                <span className="county-option-crop"
                  style={{ fontStyle: 'italic', fontSize: '0.62rem' }}>
                  {c.scientific_name}
                </span>
              </div>
              {c.description && (
                <div className="county-option-hint" style={{ paddingLeft: 0, marginTop: '0.1rem' }}>
                  {c.description}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default CropSelector;