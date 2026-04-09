import React from 'react';
import { MapContainer, TileLayer, ImageOverlay, GeoJSON, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

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

function SuitabilityOverlay({ result, apiBaseUrl }) {
  if (!result?.analysis_id || !result?.raster_bounds) return null;
  return (
    <ImageOverlay
      url={`${apiBaseUrl}/map-image/${result.analysis_id}?t=${Date.now()}`}
      bounds={result.raster_bounds}
      opacity={0.78}
      zIndex={10}
    />
  );
}

function BoundaryOverlay({ geojson }) {
  if (!geojson) return null;
  return (
    <GeoJSON
      key={JSON.stringify(geojson)}
      data={geojson}
      style={{ color: '#1a5c0a', weight: 2, fillOpacity: 0, dashArray: '5 4' }}
    />
  );
}

function Legend({ result, countyInfo, boundaryLoaded }) {
  return (
    <div style={{
      position: 'absolute', bottom: '28px', right: '10px',
      zIndex: 1000,
      background: 'rgba(255,255,255,0.95)',
      border: '1px solid #c8d8b8',
      padding: '0.7rem 0.85rem',
      borderRadius: '8px',
      minWidth: '175px',
      boxShadow: '0 2px 10px rgba(0,0,0,0.12)',
      fontFamily: 'inherit',
    }}>
      <div style={{
        fontSize: '0.62rem',
        fontWeight: 700,
        textTransform: 'uppercase',
        letterSpacing: '0.09em',
        color: '#5a7a42',
        marginBottom: '0.5rem',
        paddingBottom: '0.3rem',
        borderBottom: '1px solid #dde5d4',
      }}>
        {countyInfo ? `${countyInfo.crop} Suitability` : 'Suitability Index'}
      </div>

      {[
        { color: '#2d7a1b', label: 'Highly suitable',  note: '≥ 70' },
        { color: '#74b83e', label: 'Moderate',          note: '50–70' },
        { color: '#e0a020', label: 'Marginal',          note: '30–50' },
        { color: '#d04030', label: 'Not suitable',      note: '< 30' },
        { color: '#e8ede0', label: 'Excluded',          note: 'Protected', border: '1px solid #c8d8b8' },
      ].map(({ color, label, note, border }) => (
        <div key={label} style={{
          display: 'flex', alignItems: 'center', gap: '0.45rem',
          fontSize: '0.72rem', marginBottom: '0.22rem',
        }}>
          <div style={{
            width: '11px', height: '11px', flexShrink: 0,
            background: color, border: border || 'none', borderRadius: '2px',
          }} />
          <span style={{ color: '#2a3a1a', flex: 1 }}>{label}</span>
          <span style={{ color: '#7a8f68', fontSize: '0.68rem' }}>{note}</span>
        </div>
      ))}

      {boundaryLoaded && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.45rem',
          fontSize: '0.7rem', marginTop: '0.35rem',
          paddingTop: '0.35rem', borderTop: '1px solid #dde5d4',
        }}>
          <div style={{
            width: '11px', height: '0',
            borderTop: '2px dashed #1a5c0a', flexShrink: 0,
          }} />
          <span style={{ color: '#5a7a42' }}>
            {countyInfo ? countyInfo.display_name : 'County'} boundary
          </span>
        </div>
      )}

      {result && (
        <div style={{
          marginTop: '0.45rem', paddingTop: '0.4rem',
          borderTop: '1px solid #dde5d4',
          fontSize: '0.7rem', color: '#5a7a42',
        }}>
          Mean score:{' '}
          <span style={{ fontWeight: 700, color: '#2d5a1b' }}>
            {result.statistics.mean.toFixed(1)}
          </span>
        </div>
      )}
    </div>
  );
}

function MapView({ analysisResult, countyInfo, apiBaseUrl }) {
  const [boundaryGeoJSON, setBoundaryGeoJSON] = React.useState(null);
  const [boundaryError,   setBoundaryError]   = React.useState(false);

  const center = countyInfo?.map_center ?? [-0.5, 37.5];
  const zoom   = countyInfo?.map_zoom   ?? 8;

  React.useEffect(() => {
    if (!apiBaseUrl) return;
    fetch(`${apiBaseUrl}/boundary-geojson`)
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(data => setBoundaryGeoJSON(data))
      .catch(err => {
        console.warn('Could not load boundary:', err);
        setBoundaryError(true);
      });
  }, [apiBaseUrl]);

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
        <SuitabilityOverlay result={analysisResult} apiBaseUrl={apiBaseUrl} />
        <BoundaryOverlay geojson={boundaryGeoJSON} />
      </MapContainer>

      {!analysisResult && (
        <div className="map-prompt">
          <div className="icon">🗺️</div>
          <p>Adjust weights and click<br /><strong>Run Analysis</strong></p>
        </div>
      )}

      {boundaryError && (
        <div style={{
          position: 'absolute', top: '10px', left: '50%',
          transform: 'translateX(-50%)',
          background: '#fff8f0', border: '1px solid #e8b080',
          borderRadius: '6px', padding: '0.35rem 0.7rem',
          fontSize: '0.73rem', zIndex: 1000, color: '#8a4010',
        }}>
          ⚠ Boundary unavailable
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