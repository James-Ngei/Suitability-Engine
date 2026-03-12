import React from 'react';
import { MapContainer, TileLayer, ImageOverlay, GeoJSON, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const API_BASE = 'http://localhost:8000';

// ── Fit map to boundary ────────────────────────────────────────────────────────
function FitToBoundary({ geojson }) {
  const map = useMap();
  React.useEffect(() => {
    if (!geojson) return;
    try {
      const L = require('leaflet');
      const layer = L.geoJSON(geojson);
      map.fitBounds(layer.getBounds(), { padding: [20, 20] });
    } catch (e) {
      console.warn('fitBounds failed:', e);
    }
  }, [geojson, map]);
  return null;
}

// ── Suitability PNG overlay ────────────────────────────────────────────────────
function SuitabilityOverlay({ result }) {
  if (!result?.analysis_id || !result?.raster_bounds) return null;
  return (
    <ImageOverlay
      url={`${API_BASE}/map-image/${result.analysis_id}?t=${Date.now()}`}
      bounds={result.raster_bounds}
      opacity={0.8}
      zIndex={10}
    />
  );
}

// ── County boundary outline ────────────────────────────────────────────────────
function BoundaryOverlay({ geojson }) {
  if (!geojson) return null;
  return (
    <GeoJSON
      key={JSON.stringify(geojson)}
      data={geojson}
      style={{ color: '#1a237e', weight: 2.5, fillOpacity: 0 }}
    />
  );
}

// ── Legend ─────────────────────────────────────────────────────────────────────
function Legend({ result, countyInfo, boundaryLoaded }) {
  return (
    <div style={{
      position: 'absolute', bottom: '30px', right: '10px',
      zIndex: 1000, background: 'white', padding: '0.85rem 1rem',
      borderRadius: '8px', boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
      minWidth: '190px', maxWidth: '210px'
    }}>
      <div style={{ fontWeight: 700, marginBottom: '0.5rem', fontSize: '0.88rem', color: '#333' }}>
        {countyInfo ? `${countyInfo.crop} Suitability` : 'Suitability'}
      </div>

      {[
        { color: '#2e7d32', label: 'Highly suitable (≥70)' },
        { color: '#66bb6a', label: 'Moderate (50–70)' },
        { color: '#ffa726', label: 'Marginal (30–50)' },
        { color: '#ef5350', label: 'Not suitable (<30)' },
        { color: 'transparent', label: 'Excluded / No data', border: '1px solid #ccc' },
      ].map(({ color, label, border }) => (
        <div key={label} style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          fontSize: '0.8rem', marginBottom: '0.22rem'
        }}>
          <div style={{
            width: '18px', height: '11px', background: color,
            border: border || 'none', borderRadius: '2px', flexShrink: 0
          }} />
          <span style={{ color: '#444' }}>{label}</span>
        </div>
      ))}

      {boundaryLoaded && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          fontSize: '0.8rem', marginTop: '0.3rem'
        }}>
          <div style={{
            width: '18px', height: '0',
            borderTop: '2.5px solid #1a237e', flexShrink: 0
          }} />
          <span style={{ color: '#444' }}>
            {countyInfo ? `${countyInfo.display_name} boundary` : 'County boundary'}
          </span>
        </div>
      )}

      {result && (
        <div style={{
          marginTop: '0.65rem', paddingTop: '0.65rem',
          borderTop: '1px solid #eee', fontSize: '0.83rem', color: '#333'
        }}>
          <strong>Mean Score:</strong> {result.statistics.mean.toFixed(1)}
        </div>
      )}
    </div>
  );
}

// ── Main MapView ───────────────────────────────────────────────────────────────
function MapView({ analysisResult, countyInfo }) {
  const [boundaryGeoJSON, setBoundaryGeoJSON] = React.useState(null);
  const [boundaryError,   setBoundaryError]   = React.useState(false);

  // Default center from countyInfo or fallback to Kenya centre
  const center = countyInfo?.map_center ?? [-0.5, 37.5];
  const zoom   = countyInfo?.map_zoom   ?? 8;

  React.useEffect(() => {
    fetch(`${API_BASE}/boundary-geojson`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => setBoundaryGeoJSON(data))
      .catch(err => {
        console.warn('Could not load boundary:', err);
        setBoundaryError(true);
      });
  }, []);

  return (
    <div className="map-container">
      <MapContainer
        center={center}
        zoom={zoom}
        style={{ height: '100%', width: '100%' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        <FitToBoundary geojson={boundaryGeoJSON} />
        <SuitabilityOverlay result={analysisResult} />
        <BoundaryOverlay geojson={boundaryGeoJSON} />
      </MapContainer>

      {!analysisResult && (
        <div style={{
          position: 'absolute', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          background: 'rgba(255,255,255,0.88)',
          padding: '1.2rem 1.8rem', borderRadius: '10px',
          textAlign: 'center', pointerEvents: 'none',
          zIndex: 500, boxShadow: '0 2px 10px rgba(0,0,0,0.15)'
        }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '0.5rem' }}>🗺️</div>
          <p style={{ margin: 0, color: '#444' }}>
            Adjust weights and click <strong>Run Analysis</strong>
          </p>
        </div>
      )}

      {boundaryError && (
        <div style={{
          position: 'absolute', top: '10px', left: '50%',
          transform: 'translateX(-50%)',
          background: '#fff3cd', border: '1px solid #ffc107',
          borderRadius: '6px', padding: '0.4rem 0.8rem',
          fontSize: '0.8rem', zIndex: 1000, color: '#856404'
        }}>
          ⚠️ Boundary unavailable — check /boundary-geojson
        </div>
      )}

      <Legend
        result={analysisResult}
        countyInfo={countyInfo}
        boundaryLoaded={!!boundaryGeoJSON}
      />
    </div>
  );
}

export default MapView;