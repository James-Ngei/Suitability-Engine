import React, { useState, useEffect } from 'react';
import { MapContainer, TileLayer, ImageOverlay, GeoJSON, useMap } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

const CMAP_GRADIENTS = {
  terrain:   'linear-gradient(to right, #1a6b3c, #4a9e6b, #a8c87a, #e8d878, #c8a050, #a07040, #888, #fff)',
  YlGnBu:    'linear-gradient(to right, #ffffd9, #c7e9b4, #7fcdbb, #41b6c4, #1d91c0, #225ea8, #0c2c84)',
  RdYlBu_r:  'linear-gradient(to right, #313695, #4575b4, #74add1, #e0f3f8, #ffffbf, #fdae61, #f46d43, #a50026)',
  YlOrBr:    'linear-gradient(to right, #ffffe5, #fff7bc, #fee391, #fec44f, #fe9929, #cc4c02, #8c2d04)',
  copper_r:  'linear-gradient(to right, #c87820, #a05818, #784010, #502808, #281400, #000)',
};

const LAYER_META = {
  suitability: { label: 'Suitability',  unit: '/100',   low: 'Low',   high: 'High',  gradient: null },
  elevation:   { label: 'Elevation',    unit: 'm',      low: 'Low',   high: 'High',  gradient: CMAP_GRADIENTS.terrain },
  rainfall:    { label: 'Rainfall',     unit: 'mm/yr',  low: 'Dry',   high: 'Wet',   gradient: CMAP_GRADIENTS.YlGnBu },
  temperature: { label: 'Temperature',  unit: '°C',     low: 'Cool',  high: 'Hot',   gradient: CMAP_GRADIENTS.RdYlBu_r },
  soil:        { label: 'Soil Clay',    unit: 'g/kg',   low: 'Sandy', high: 'Clay',  gradient: CMAP_GRADIENTS.YlOrBr },
  slope:       { label: 'Slope',        unit: '°',      low: 'Flat',  high: 'Steep', gradient: CMAP_GRADIENTS.copper_r },
};

function FitToBoundary({ geojson }) {
  const map = useMap();
  useEffect(() => {
    if (!geojson) return;
    try { const L = require('leaflet'); map.fitBounds(L.geoJSON(geojson).getBounds(), { padding:[20,20] }); }
    catch {}
  }, [geojson, map]);
  return null;
}

function Legend({ activeLayer, result, countyInfo, boundaryLoaded }) {
  const meta = LAYER_META[activeLayer];
  return (
    <div style={{
      position:'absolute', bottom:'28px', right:'10px', zIndex:1000,
      background:'rgba(255,255,255,0.96)', border:'1px solid #c8d8b8',
      padding:'0.65rem 0.85rem', borderRadius:'8px', minWidth:'165px',
      boxShadow:'0 2px 10px rgba(0,0,0,0.12)', fontFamily:'inherit',
    }}>
      <div style={{
        fontSize:'0.62rem', fontWeight:700, textTransform:'uppercase',
        letterSpacing:'0.09em', color:'#5a7a42',
        marginBottom:'0.5rem', paddingBottom:'0.3rem', borderBottom:'1px solid #dde5d4',
      }}>
        {countyInfo?.crop && activeLayer === 'suitability'
          ? `${countyInfo.crop} Suitability` : meta?.label}
      </div>

      {activeLayer === 'suitability' ? (
        <>
          {[
            { color:'#2d7a1b', label:'Highly suitable', note:'≥ 70' },
            { color:'#74b83e', label:'Moderate',         note:'50–70' },
            { color:'#e0a020', label:'Marginal',         note:'30–50' },
            { color:'#d04030', label:'Not suitable',     note:'< 30' },
            { color:'#e8ede0', label:'Excluded',         note:'Protected', border:'1px solid #c8d8b8' },
          ].map(({ color, label, note, border }) => (
            <div key={label} style={{ display:'flex', alignItems:'center', gap:'0.45rem', fontSize:'0.72rem', marginBottom:'0.2rem' }}>
              <div style={{ width:'11px', height:'11px', flexShrink:0, background:color, border:border||'none', borderRadius:'2px' }} />
              <span style={{ color:'#2a3a1a', flex:1 }}>{label}</span>
              <span style={{ color:'#7a8f68', fontSize:'0.68rem' }}>{note}</span>
            </div>
          ))}
          {result && (
            <div style={{ marginTop:'0.4rem', paddingTop:'0.35rem', borderTop:'1px solid #dde5d4', fontSize:'0.7rem', color:'#5a7a42' }}>
              Mean: <strong style={{ color:'#2d5a1b' }}>{result.statistics.mean.toFixed(1)}</strong>
            </div>
          )}
        </>
      ) : meta && (
        <>
          <div style={{ height:'10px', borderRadius:'3px', background:meta.gradient||'#ccc', margin:'0 0 5px' }} />
          <div style={{ display:'flex', justifyContent:'space-between', fontSize:'0.62rem', color:'#7a8f68', marginBottom:'3px' }}>
            <span>{meta.low}</span><span>{meta.high}</span>
          </div>
          <div style={{ fontSize:'0.62rem', color:'#a0b088', textAlign:'center' }}>
            Normalised 0–100 · {meta.unit}
          </div>
        </>
      )}

      {boundaryLoaded && (
        <div style={{ display:'flex', alignItems:'center', gap:'0.45rem', fontSize:'0.7rem',
          marginTop:'0.4rem', paddingTop:'0.35rem', borderTop:'1px solid #dde5d4' }}>
          <div style={{ width:'11px', height:0, borderTop:'2px dashed #1a5c0a', flexShrink:0 }} />
          <span style={{ color:'#5a7a42' }}>{countyInfo?.display_name || 'County'}</span>
        </div>
      )}
    </div>
  );
}

function MapView({ analysisResult, countyInfo, apiBaseUrl, activeCounty, activeLayer }) {
  const [boundaryGeoJSON, setBoundaryGeoJSON] = useState(null);
  const [boundaryError,   setBoundaryError]   = useState(false);
  const [overlayUrl,      setOverlayUrl]       = useState(null);
  const [overlayBounds,   setOverlayBounds]    = useState(null);
  const [layerLoading,    setLayerLoading]     = useState(false);

  const center     = countyInfo?.map_center ?? [-0.5, 37.5];
  const zoom       = countyInfo?.map_zoom   ?? 8;
  const hasAnalysis = !!analysisResult?.analysis_id;

  useEffect(() => {
    if (!apiBaseUrl || !activeCounty) return;
    setBoundaryGeoJSON(null); setBoundaryError(false);
    fetch(`${apiBaseUrl}/boundary-geojson?county=${activeCounty}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(setBoundaryGeoJSON).catch(() => setBoundaryError(true));
  }, [apiBaseUrl, activeCounty]);

  useEffect(() => {
    if (!activeCounty) { setOverlayUrl(null); return; }

    if (activeLayer === 'suitability') {
      if (!hasAnalysis) { setOverlayUrl(null); setOverlayBounds(null); return; }
      setOverlayUrl(`${apiBaseUrl}/map-image/${analysisResult.analysis_id}?t=${Date.now()}&county=${activeCounty}`);
      setOverlayBounds(analysisResult.raster_bounds);
      return;
    }

    if (!analysisResult?.raster_bounds) { setOverlayUrl(null); return; }

    setLayerLoading(true);
    const url = `${apiBaseUrl}/layer-image/${activeCounty}/${activeLayer}?t=${Date.now()}`;
    const img = new window.Image();
    img.onload  = () => { setOverlayUrl(url); setOverlayBounds(analysisResult.raster_bounds); setLayerLoading(false); };
    img.onerror = () => { setOverlayUrl(null); setLayerLoading(false); };
    img.src = url;
  }, [activeLayer, activeCounty, analysisResult?.analysis_id]);

  return (
    <div className="map-container">
      <MapContainer center={center} zoom={zoom} style={{ height:'100%', width:'100%' }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitToBoundary geojson={boundaryGeoJSON} />
        {overlayUrl && overlayBounds && (
          <ImageOverlay url={overlayUrl} bounds={overlayBounds} opacity={0.82} zIndex={10} />
        )}
        {boundaryGeoJSON && (
          <GeoJSON
            key={JSON.stringify(boundaryGeoJSON).slice(0,60)}
            data={boundaryGeoJSON}
            style={{ color:'#1a5c0a', weight:2, fillOpacity:0, dashArray:'5 4' }}
          />
        )}
      </MapContainer>

      {layerLoading && (
        <div style={{ position:'absolute', top:'12px', left:'50%', transform:'translateX(-50%)',
          background:'rgba(255,255,255,0.95)', border:'1px solid #c8d8b8', borderRadius:'6px',
          padding:'0.3rem 0.8rem', fontSize:'0.72rem', color:'#5a7a42', zIndex:1000 }}>
          Loading {LAYER_META[activeLayer]?.label}…
        </div>
      )}

      {!hasAnalysis && activeLayer === 'suitability' && (
        <div className="map-prompt">
          <div className="icon">🗺️</div>
          <p>Adjust weights and click<br /><strong>Run Analysis</strong></p>
        </div>
      )}

      {activeLayer !== 'suitability' && !analysisResult?.raster_bounds && !layerLoading && (
        <div style={{ position:'absolute', top:'50%', left:'50%', transform:'translate(-50%,-50%)',
          background:'rgba(255,255,255,0.93)', border:'1px solid #c8d8b8',
          padding:'0.75rem 1.1rem', borderRadius:'8px', textAlign:'center',
          zIndex:500, fontSize:'0.78rem', color:'#5a7a42', pointerEvents:'none' }}>
          Run an analysis first to unlock factor layers
        </div>
      )}

      {boundaryError && (
        <div style={{ position:'absolute', top:'10px', left:'50%', transform:'translateX(-50%)',
          background:'#fff8f0', border:'1px solid #e8b080', borderRadius:'6px',
          padding:'0.35rem 0.7rem', fontSize:'0.73rem', zIndex:1000, color:'#8a4010' }}>
          ⚠ Boundary unavailable
        </div>
      )}

      <Legend
        activeLayer={activeLayer}
        result={hasAnalysis ? analysisResult : null}
        countyInfo={countyInfo}
        boundaryLoaded={!!boundaryGeoJSON}
      />
    </div>
  );
}

export default MapView;