import React from 'react';
import { MapContainer, TileLayer, Rectangle, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

// Bungoma bounding box
const BUNGOMA_BOUNDS = [[0.2, 34.3], [1.3, 35.0]];
const BUNGOMA_CENTER = [0.75, 34.65];

function SuitabilityOverlay({ result }) {
  const map = useMap();
  
  React.useEffect(() => {
    if (result) {
      // Fit to Bungoma bounds when result loads
      map.fitBounds(BUNGOMA_BOUNDS);
    }
  }, [result, map]);

  if (!result) return null;

  // Color coding based on mean suitability
  const getMeanColor = (mean) => {
    if (mean >= 70) return '#2e7d32';
    if (mean >= 50) return '#66bb6a';
    if (mean >= 30) return '#ffa726';
    return '#ef5350';
  };

  const color = getMeanColor(result.statistics.mean);
  const opacity = 0.3;

  return (
    <Rectangle
      bounds={BUNGOMA_BOUNDS}
      pathOptions={{
        color: color,
        weight: 3,
        fillColor: color,
        fillOpacity: opacity
      }}
    />
  );
}

function MapView({ analysisResult }) {
  return (
    <div className="map-container">
      {!analysisResult ? (
        <div className="map-placeholder">
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🗺️</div>
            <p>Adjust weights and click "Run Analysis"</p>
            <p style={{ fontSize: '0.9rem', color: '#bbb', marginTop: '0.5rem' }}>
              Results will be displayed on the map
            </p>
          </div>
        </div>
      ) : (
        <MapContainer
          center={BUNGOMA_CENTER}
          zoom={10}
          style={{ height: '100%', width: '100%' }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          
          <SuitabilityOverlay result={analysisResult} />
          
          {/* Legend */}
          <div className="leaflet-bottom leaflet-right">
            <div className="leaflet-control" style={{
              background: 'white',
              padding: '1rem',
              borderRadius: '8px',
              boxShadow: '0 2px 8px rgba(0,0,0,0.2)'
            }}>
              <div style={{ fontWeight: 'bold', marginBottom: '0.5rem', fontSize: '0.9rem' }}>
                Suitability
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', fontSize: '0.85rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <div style={{ width: '20px', height: '12px', background: '#2e7d32', borderRadius: '2px' }} />
                  <span>Highly (≥70)</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <div style={{ width: '20px', height: '12px', background: '#66bb6a', borderRadius: '2px' }} />
                  <span>Moderate (50-70)</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <div style={{ width: '20px', height: '12px', background: '#ffa726', borderRadius: '2px' }} />
                  <span>Marginal (30-50)</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <div style={{ width: '20px', height: '12px', background: '#ef5350', borderRadius: '2px' }} />
                  <span>Not Suitable (&lt;30)</span>
                </div>
              </div>
              
              {analysisResult && (
                <div style={{ 
                  marginTop: '0.75rem', 
                  paddingTop: '0.75rem', 
                  borderTop: '1px solid #ddd',
                  fontSize: '0.85rem'
                }}>
                  <strong>Mean Score:</strong> {analysisResult.statistics.mean.toFixed(1)}
                </div>
              )}
            </div>
          </div>
        </MapContainer>
      )}
    </div>
  );
}

export default MapView;
