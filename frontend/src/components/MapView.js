import React from 'react';
import { MapContainer, TileLayer, ImageOverlay, GeoJSON, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const BUNGOMA_CENTER = [0.75, 34.65];
const API_BASE = 'http://localhost:8000';

// ── Fit map to boundary once GeoJSON loads ─────────────────────────────────
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

// ── Suitability PNG overlay ────────────────────────────────────────────────
// Uses raster_bounds returned by the API — NOT hardcoded values.
// This ensures the PNG is placed at exactly the right coordinates.
function SuitabilityOverlay({ result }) {
  if (!result || !result.analysis_id || !result.raster_bounds) return null;

  const imageUrl = `${API_BASE}/map-image/${result.analysis_id}?t=${Date.now()}`;

  return (
    <ImageOverlay
      url={imageUrl}
      bounds={result.raster_bounds}   // [[south, west], [north, east]] from API
      opacity={0.8}
      zIndex={10}
    />
  );
}

// ── Bungoma boundary outline ───────────────────────────────────────────────
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

// ── Legend ─────────────────────────────────────────────────────────────────
function Legend({ analysisResult, boundaryLoaded }) {
  return (
    <div style={{
      position: 'absolute', bottom: '30px', right: '10px',
      zIndex: 1000, background: 'white', padding: '1rem',
      borderRadius: '8px', boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
      minWidth: '175px'
    }}>
      <div style={{ fontWeight: 'bold', marginBottom: '0.5rem', fontSize: '0.9rem' }}>
        Suitability
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
          fontSize: '0.82rem', marginBottom: '0.25rem'
        }}>
          <div style={{
            width: '20px', height: '12px', background: color,
            border: border || 'none', borderRadius: '2px', flexShrink: 0
          }} />
          <span>{label}</span>
        </div>
      ))}

      {boundaryLoaded && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          fontSize: '0.82rem', marginTop: '0.4rem'
        }}>
          <div style={{
            width: '20px', height: '0px',
            borderTop: '2.5px solid #1a237e', flexShrink: 0
          }} />
          <span>Bungoma boundary</span>
        </div>
      )}

      {analysisResult && (
        <div style={{
          marginTop: '0.75rem', paddingTop: '0.75rem',
          borderTop: '1px solid #ddd', fontSize: '0.85rem'
        }}>
          <strong>Mean Score:</strong> {analysisResult.statistics.mean.toFixed(1)}
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────
function MapView({ analysisResult }) {
  const [boundaryGeoJSON, setBoundaryGeoJSON] = React.useState(null);
  const [boundaryError, setBoundaryError]     = React.useState(false);

  // Fetch boundary once on mount
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
    <div className="map-container" style={{ position: 'relative' }}>
      <MapContainer
        center={BUNGOMA_CENTER}
        zoom={10}
        style={{ height: '100%', width: '100%' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {/* Fit viewport to actual county boundary */}
        <FitToBoundary geojson={boundaryGeoJSON} />

        {/* Suitability heatmap using exact bounds from API */}
        <SuitabilityOverlay result={analysisResult} />

        {/* County boundary outline */}
        <BoundaryOverlay geojson={boundaryGeoJSON} />
      </MapContainer>

      {/* Prompt before first analysis */}
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

      {/* Warning if boundary failed to load */}
      {boundaryError && (
        <div style={{
          position: 'absolute', top: '10px', left: '50%',
          transform: 'translateX(-50%)',
          background: '#fff3cd', border: '1px solid #ffc107',
          borderRadius: '6px', padding: '0.4rem 0.8rem',
          fontSize: '0.8rem', zIndex: 1000, color: '#856404'
        }}>
          ⚠️ Boundary outline unavailable — check /boundary-geojson endpoint
        </div>
      )}

      <Legend
        analysisResult={analysisResult}
        boundaryLoaded={!!boundaryGeoJSON}
      />
    </div>
  );
}

export default MapView;