import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import MapView from './components/MapView';
import WeightControls from './components/WeightControls';
import Statistics from './components/Statistics';
import ReportPanel from './components/ReportPanel';
import AnalysisSetup from './components/AnalysisSetup';
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function App() {
  const [countyInfo,       setCountyInfo]      = useState(null);
  const [activeCounty,     setActiveCounty]    = useState(null);
  const [activeCrop,       setActiveCrop]      = useState(null);
  const [activeLayer,      setActiveLayer]     = useState('suitability');
  const [newResultReady,   setNewResultReady]  = useState(false);
  const [weights,          setWeights]         = useState(null);
  const [criteria,         setCriteria]        = useState([]);
  const [analysisResult,   setAnalysisResult]  = useState(null);
  const [loading,          setLoading]         = useState(false);
  const [applyConstraints, setApplyConstraints]= useState(true);
  const [apiError,         setApiError]        = useState(null);

  const [reportOverlay,    setReportOverlay]   = useState(false);
  const [pdfBlobUrl,       setPdfBlobUrl]      = useState(null);
  const [pdfFilename,      setPdfFilename]     = useState('');
  const [reportDepth,      setReportDepth]     = useState('full');
  const [reportGenerating, setReportGenerating]= useState(false);
  const [reportError,      setReportError]     = useState(null);

  const isMobile       = /iPhone|iPad|Android/i.test(navigator.userAgent);
  const reportPanelRef = useRef(null);

  const loadInitial = async () => {
    try {
      const [countyRes, criteriaRes] = await Promise.all([
        axios.get(`${API_BASE_URL}/county`),
        axios.get(`${API_BASE_URL}/criteria`),
      ]);
      const info = countyRes.data;
      setCountyInfo(info);
      setActiveCounty(info.county);
      setActiveCrop(info.crop_id || 'cotton');
      setWeights({ ...info.weights });
      setCriteria(criteriaRes.data);
      setApiError(null);
    } catch {
      setApiError('Cannot reach the API at ' + API_BASE_URL);
    }
  };

  useEffect(() => { loadInitial(); }, []);

  useEffect(() => {
    const h = e => { if (e.key === 'Escape') setReportOverlay(false); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);

  // County changed from AnalysisSetup
  const handleCountyChange = (countyId, countyConfig) => {
    setCountyInfo(countyConfig);
    setActiveCounty(countyId);
    if (countyConfig.weights) setWeights({ ...countyConfig.weights });
    if (countyConfig.criteria) setCriteria(countyConfig.criteria);
    else {
      axios.get(`${API_BASE_URL}/criteria?crop=${activeCrop||'cotton'}&county=${countyId}`)
        .then(r => setCriteria(r.data)).catch(() => {});
    }
    setAnalysisResult(null);
    setNewResultReady(false);
    setPdfBlobUrl(null);
    setReportOverlay(false);
    // Keep activeLayer if it's a factor layer — useful to see same factor for new county
    // Only reset if on suitability since there's no result for new county
    if (activeLayer === 'suitability') setActiveLayer('elevation');
  };

  // Crop changed from AnalysisSetup
  const handleCropChange = (cropId, cropMeta) => {
    setActiveCrop(cropId);
    if (cropMeta.criteria) setCriteria(cropMeta.criteria);
    if (cropMeta.weights) setWeights({ ...cropMeta.weights });
    setCountyInfo(prev => ({ ...prev, crop: cropMeta.display_name || cropId, crop_id: cropId }));
    setAnalysisResult(null);
    setNewResultReady(false);
    setPdfBlobUrl(null);
    setReportOverlay(false);
  };

  // Layer changed from AnalysisSetup
  const handleLayerChange = (layerId) => {
    setActiveLayer(layerId);
    if (layerId === 'suitability') setNewResultReady(false);
  };

  const handleWeightChange = (criterion, value) => {
    const nw     = { ...weights, [criterion]: value };
    const others = Object.keys(weights).filter(k => k !== criterion);
    const sum    = others.reduce((s, k) => s + weights[k], 0);
    if (sum > 0) {
      const rem = 1.0 - value;
      others.forEach(k => { nw[k] = (weights[k] / sum) * rem; });
    }
    setWeights(nw);
  };

  const resetWeights = () => {
    if (countyInfo?.weights) setWeights({ ...countyInfo.weights });
  };

  const runAnalysis = async () => {
    setLoading(true);
    setNewResultReady(false);
    setPdfBlobUrl(null);
    setReportOverlay(false);
    setReportError(null);
    try {
      const params = new URLSearchParams();
      if (activeCounty) params.set('county', activeCounty);
      if (activeCrop)   params.set('crop',   activeCrop);
      const r = await axios.post(`${API_BASE_URL}/analyze?${params}`, {
        weights: weights, apply_constraints: applyConstraints,
      });
      setAnalysisResult(r.data);

      // Smart layer switch:
      // If user was on a factor layer → keep it, show NEW badge on suitability
      // If user was already on suitability → update to new result (natural)
      if (activeLayer === 'suitability') {
        // stays on suitability, new result loads automatically via URL change
        setNewResultReady(false);
      } else {
        // stays on whatever factor layer they were viewing
        setNewResultReady(true);
      }
    } catch (err) {
      const d = err.response?.data?.detail;
      if (err.response?.status === 503) {
        const msg = typeof d === 'object' ? d.message : d;
        alert(`County data is still loading.\n${msg}\nPlease wait and try again.`);
      } else {
        alert('Error: ' + (typeof d === 'string' ? d : JSON.stringify(d) || err.message));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateReport = async () => {
    if (!analysisResult?.analysis_id) return;
    setReportGenerating(true);
    setReportError(null);
    try {
      const params = new URLSearchParams({ depth: reportDepth });
      if (activeCounty) params.set('county', activeCounty);
      if (activeCrop)   params.set('crop',   activeCrop);
      const r = await fetch(
        `${API_BASE_URL}/report/${analysisResult.analysis_id}?${params}`,
        { method: 'POST' }
      );
      if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d?.detail || `HTTP ${r.status}`); }
      const blob = await r.blob();
      const url  = URL.createObjectURL(blob);
      const fn   = `${activeCounty}_${activeCrop}_${analysisResult.analysis_id}_${reportDepth}.pdf`;
      setPdfBlobUrl(url);
      setPdfFilename(fn);
      setReportOverlay(true);
      setTimeout(() => reportPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    } catch (e) {
      setReportError(e.message || 'Report generation failed');
    } finally {
      setReportGenerating(false);
    }
  };

  const handleDownload = () => {
    if (!pdfBlobUrl) return;
    const a = document.createElement('a'); a.href = pdfBlobUrl; a.download = pdfFilename; a.click();
  };

  if (apiError) return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center',
      height:'100vh', flexDirection:'column', gap:'1rem', background:'#1a1f16', color:'#c07050' }}>
      <div style={{ fontSize:'2rem' }}>⚠</div>
      <div style={{ fontFamily:'Courier New', fontSize:'0.85rem' }}>{apiError}</div>
      <div style={{ fontFamily:'Courier New', fontSize:'0.75rem', color:'#3a4832' }}>
        Start the API: <code>python src/api.py</code>
      </div>
    </div>
  );

  if (!countyInfo || !weights) return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh',
      color:'#3a4832', flexDirection:'column', background:'#1a1f16',
      fontFamily:'Courier New', fontSize:'0.8rem', letterSpacing:'0.08em', textTransform:'uppercase' }}>
      <div>Loading…</div>
    </div>
  );

  const totalWeight  = Object.values(weights).reduce((s, v) => s + v, 0);
  const hasAnalysis  = !!analysisResult?.analysis_id;
  const canRun       = !loading && Math.abs(totalWeight - 1.0) <= 0.01;

  return (
    <div className="App">
      <header className="header">
        <div className="header-left">
          <h1>🌿 Crop Suitability Engine</h1>
          <p>{countyInfo.display_name}, Kenya{activeCrop ? ` — ${countyInfo.crop || activeCrop}` : ''}</p>
        </div>
        <div className="header-badge">MCDA v3.0</div>
      </header>

      <div className="container">
        <div className="left-panel">

          {/* Single consolidated setup panel */}
          <AnalysisSetup
            apiBaseUrl={API_BASE_URL}
            currentCounty={activeCounty}
            currentCrop={activeCrop}
            activeLayer={activeLayer}
            hasAnalysis={hasAnalysis}
            newResultReady={newResultReady}
            onCountyChange={handleCountyChange}
            onCropChange={handleCropChange}
            onLayerChange={handleLayerChange}
          />

          {/* Run Analysis */}
          <div className="panel-section">
            <label className="checkbox-label">
              <input type="checkbox" checked={applyConstraints}
                onChange={e => setApplyConstraints(e.target.checked)} />
              Apply protected area constraints
            </label>
            <button className="analyze-button" onClick={runAnalysis} disabled={!canRun}>
              {loading ? '⏳ Analyzing…' : '▶ Run Analysis'}
            </button>
          </div>

          {/* Weights */}
          <WeightControls
            weights={weights}
            criteria={criteria}
            onWeightChange={handleWeightChange}
            totalWeight={totalWeight}
          />
          <button className="reset-button" onClick={resetWeights}>
            Reset weights to defaults
          </button>

        </div>

        <div className="main-content">
          <MapView
            analysisResult={analysisResult}
            countyInfo={countyInfo}
            apiBaseUrl={API_BASE_URL}
            activeCounty={activeCounty}
            activeLayer={activeLayer}
            onLayerChange={handleLayerChange}
          />

          {reportOverlay && pdfBlobUrl && (
            <div className="report-overlay"
              onClick={e => { if (e.target === e.currentTarget) setReportOverlay(false); }}>
              <div className="report-overlay-card">
                <div className="report-overlay-toolbar">
                  <span className="report-overlay-title">
                    {countyInfo.crop || activeCrop} Suitability Report
                    <span className="report-depth-badge">{reportDepth}</span>
                  </span>
                  <div className="report-overlay-actions">
                    <button className="report-action-btn report-download-btn" onClick={handleDownload}>↓ Download</button>
                    <button className="report-action-btn report-close-btn"
                      onClick={() => setReportOverlay(false)} title="Close (Esc)">✕</button>
                  </div>
                </div>
                {isMobile ? (
                  <div style={{ display:'flex', alignItems:'center', justifyContent:'center',
                    flex:1, flexDirection:'column', gap:'1rem', background:'#f4f4f4' }}>
                    <div style={{ fontSize:'0.85rem', color:'#5a7a42' }}>PDF preview not supported on mobile.</div>
                    <a href={pdfBlobUrl} target="_blank" rel="noreferrer"
                      style={{ padding:'0.8rem 1.5rem', background:'#2d5a1b', color:'white',
                        borderRadius:'6px', textDecoration:'none', fontSize:'0.9rem', fontWeight:600 }}>
                      Open Report</a>
                  </div>
                ) : (
                  <iframe src={pdfBlobUrl} title="Suitability Report"
                    className="report-overlay-iframe" type="application/pdf" />
                )}
              </div>
            </div>
          )}
        </div>

        <div className="right-panel" ref={reportPanelRef}>
          <Statistics result={analysisResult} />
          <ReportPanel
            hasAnalysis={hasAnalysis}
            depth={reportDepth}
            onDepthChange={setReportDepth}
            generating={reportGenerating}
            error={reportError}
            pdfReady={!!pdfBlobUrl}
            onGenerate={handleGenerateReport}
            onView={() => {
              setReportOverlay(true);
              setTimeout(() => reportPanelRef.current?.scrollIntoView({ behavior:'smooth', block:'start' }), 100);
            }}
            onDownload={handleDownload}
          />
        </div>
      </div>

      <footer className="footer">
        <span className="footer-brand">Crop Suitability Engine · {countyInfo.display_name} · MCDA</span>
        {hasAnalysis && (
          <div className="footer-report-controls">
            {reportError && <span className="footer-report-error">⚠ {reportError}</span>}
            {pdfBlobUrl && (
              <>
                <button className="footer-report-btn footer-view-btn" onClick={() => setReportOverlay(true)}>View report</button>
                <button className="footer-report-btn footer-download-btn" onClick={handleDownload}>↓ Download</button>
              </>
            )}
            <select className="footer-depth-select" value={reportDepth}
              onChange={e => setReportDepth(e.target.value)} disabled={reportGenerating}>
              <option value="summary">Summary (2p)</option>
              <option value="full">Full (4p)</option>
            </select>
            <button className="footer-report-btn footer-generate-btn"
              onClick={handleGenerateReport} disabled={reportGenerating}>
              {reportGenerating ? '⏳ Generating…' : '📄 Generate Report'}
            </button>
          </div>
        )}
      </footer>
    </div>
  );
}

export default App;