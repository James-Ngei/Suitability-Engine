import React, { useState, useEffect } from 'react';
import './App.css';
import MapView from './components/MapView';
import WeightControls from './components/WeightControls';
import Statistics from './components/Statistics';
import ReportPanel from './components/ReportPanel';
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function App() {
  const [countyInfo,       setCountyInfo]       = useState(null);
  const [weights,          setWeights]          = useState(null);
  const [criteria,         setCriteria]         = useState([]);
  const [analysisResult,   setAnalysisResult]   = useState(null);
  const [loading,          setLoading]          = useState(false);
  const [applyConstraints, setApplyConstraints] = useState(true);
  const [apiError,         setApiError]         = useState(null);

  // Report state — lifted here so footer controls and map overlay share it
  const [reportOverlay,    setReportOverlay]    = useState(false);
  const [pdfBlobUrl,       setPdfBlobUrl]       = useState(null);
  const [pdfFilename,      setPdfFilename]      = useState('');
  const [reportDepth,      setReportDepth]      = useState('full');
  const [reportGenerating, setReportGenerating] = useState(false);
  const [reportError,      setReportError]      = useState(null);

  useEffect(() => {
    Promise.all([
      axios.get(`${API_BASE_URL}/county`),
      axios.get(`${API_BASE_URL}/criteria`),
    ])
      .then(([countyRes, criteriaRes]) => {
        const info = countyRes.data;
        setCountyInfo(info);
        setWeights(info.weights);
        setCriteria(criteriaRes.data);
        setApiError(null);
      })
      .catch(err => {
        console.error('Failed to load county config:', err);
        setApiError('Cannot reach the API at ' + API_BASE_URL);
      });
  }, []);

  // Dismiss overlay on Escape
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') setReportOverlay(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const handleWeightChange = (criterion, value) => {
    const newWeights = { ...weights, [criterion]: value };
    const others     = Object.keys(weights).filter(k => k !== criterion);
    const otherTotal = others.reduce((s, k) => s + weights[k], 0);
    const remaining  = 1.0 - value;
    if (otherTotal > 0) {
      others.forEach(k => { newWeights[k] = (weights[k] / otherTotal) * remaining; });
    }
    setWeights(newWeights);
  };

  const resetWeights = () => {
    if (countyInfo) setWeights({ ...countyInfo.weights });
  };

  const runAnalysis = async () => {
    setLoading(true);
    setPdfBlobUrl(null);
    setReportOverlay(false);
    setReportError(null);
    try {
      const response = await axios.post(`${API_BASE_URL}/analyze`, {
        weights:           weights,
        apply_constraints: applyConstraints,
      });
      setAnalysisResult(response.data);
    } catch (error) {
      console.error('Analysis error:', error);
      alert('Error: ' + (error.response?.data?.detail || error.message));
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateReport = async () => {
    if (!analysisResult?.analysis_id) return;
    setReportGenerating(true);
    setReportError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/report/${analysisResult.analysis_id}?depth=${reportDepth}`,
        { method: 'POST' }
      );
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail?.detail || `HTTP ${response.status}`);
      }
      const blob     = await response.blob();
      const blobUrl  = URL.createObjectURL(blob);
      const county   = (analysisResult.county || 'report').replace(/\s+/g, '_').toLowerCase();
      const filename = `${county}_suitability_${analysisResult.analysis_id}_${reportDepth}.pdf`;
      setPdfBlobUrl(blobUrl);
      setPdfFilename(filename);
      setReportOverlay(true);
    } catch (err) {
      console.error('Report error:', err);
      setReportError(err.message || 'Report generation failed');
    } finally {
      setReportGenerating(false);
    }
  };

  const handleDownload = () => {
    if (!pdfBlobUrl) return;
    const a = document.createElement('a');
    a.href = pdfBlobUrl;
    a.download = pdfFilename;
    a.click();
  };

  if (apiError) {
    return (
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'center',
        height:'100vh', flexDirection:'column', gap:'1rem',
        background:'#1a1f16', color:'#c07050',
      }}>
        <div style={{ fontSize:'2rem' }}>⚠</div>
        <div style={{ fontFamily:'Courier New', fontSize:'0.85rem' }}>{apiError}</div>
        <div style={{ fontFamily:'Courier New', fontSize:'0.75rem', color:'#3a4832' }}>
          Start the API: <code>python src/api.py</code>
        </div>
      </div>
    );
  }

  if (!countyInfo || !weights) {
    return (
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'center',
        height:'100vh', color:'#3a4832', flexDirection:'column', gap:'0.5rem',
        background:'#1a1f16', fontFamily:'Courier New', fontSize:'0.8rem',
        letterSpacing:'0.08em', textTransform:'uppercase',
      }}>
        <div>Loading configuration...</div>
      </div>
    );
  }

  const totalWeight = Object.values(weights).reduce((s, v) => s + v, 0);
  const hasAnalysis = !!analysisResult?.analysis_id;

  return (
    <div className="App">
      <header className="header">
        <div className="header-left">
          <h1>🌿 Crop Suitability Engine</h1>
          <p>{countyInfo.display_name}, {countyInfo.country} — {countyInfo.crop} Analysis</p>
        </div>
        <div className="header-badge">MCDA v2.1</div>
      </header>

      <div className="container">
        <div className="left-panel">
          <WeightControls
            weights={weights}
            criteria={criteria}
            onWeightChange={handleWeightChange}
            totalWeight={totalWeight}
          />
          <div className="panel-section">
            <div className="section-title">Run Analysis</div>
            <label className="checkbox-label">
              <input type="checkbox" checked={applyConstraints}
                onChange={e => setApplyConstraints(e.target.checked)} />
              Apply protected area constraints
            </label>
            <button className="analyze-button" onClick={runAnalysis}
              disabled={loading || Math.abs(totalWeight - 1.0) > 0.01}>
              {loading ? '⏳ Analyzing...' : '▶ Run Analysis'}
            </button>
            <button className="reset-button" onClick={resetWeights}>Reset weights</button>
          </div>
        </div>

        <div className="main-content">
          <MapView
            analysisResult={analysisResult}
            countyInfo={countyInfo}
            apiBaseUrl={API_BASE_URL}
          />

          {/* PDF overlay — full map area, click backdrop to close */}
          {reportOverlay && pdfBlobUrl && (
            <div className="report-overlay"
              onClick={e => { if (e.target === e.currentTarget) setReportOverlay(false); }}>
              <div className="report-overlay-card">
                <div className="report-overlay-toolbar">
                  <span className="report-overlay-title">
                    {countyInfo.crop} Suitability Report
                    <span className="report-depth-badge">{reportDepth}</span>
                  </span>
                  <div className="report-overlay-actions">
                    <button className="report-action-btn report-download-btn"
                      onClick={handleDownload}>↓ Download</button>
                    <button className="report-action-btn report-close-btn"
                      onClick={() => setReportOverlay(false)}
                      title="Close (Esc)">✕</button>
                  </div>
                </div>
                <iframe src={pdfBlobUrl} title="Suitability Report"
                  className="report-overlay-iframe" type="application/pdf" />
              </div>
            </div>
          )}
        </div>

        <div className="right-panel">
          <Statistics result={analysisResult} />
          <ReportPanel
            hasAnalysis={hasAnalysis}
            depth={reportDepth}
            onDepthChange={setReportDepth}
            generating={reportGenerating}
            error={reportError}
            pdfReady={!!pdfBlobUrl}
            onGenerate={handleGenerateReport}
            onView={() => setReportOverlay(true)}
            onDownload={handleDownload}
          />
        </div>
      </div>

      <footer className="footer">
        <span className="footer-brand">
          Crop Suitability Engine &nbsp;·&nbsp; {countyInfo.display_name} &nbsp;·&nbsp; Multi-Criteria Decision Analysis
        </span>

        {hasAnalysis && (
          <div className="footer-report-controls">
            {reportError && (
              <span className="footer-report-error">⚠ {reportError}</span>
            )}
            {pdfBlobUrl && (
              <>
                <button className="footer-report-btn footer-view-btn"
                  onClick={() => setReportOverlay(true)}>
                  View report
                </button>
                <button className="footer-report-btn footer-download-btn"
                  onClick={handleDownload}>
                  ↓ Download
                </button>
              </>
            )}
            <select className="footer-depth-select" value={reportDepth}
              onChange={e => setReportDepth(e.target.value)} disabled={reportGenerating}>
              <option value="summary">Summary (2p)</option>
              <option value="full">Full (4p)</option>
            </select>
            <button className="footer-report-btn footer-generate-btn"
              onClick={handleGenerateReport} disabled={reportGenerating}>
              {reportGenerating ? '⏳ Generating...' : '📄 Generate Report'}
            </button>
          </div>
        )}
      </footer>
    </div>
  );
}

export default App;