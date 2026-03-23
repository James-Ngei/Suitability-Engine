import React, { useState, useEffect } from 'react';
import './App.css';
import MapView from './components/MapView';
import WeightControls from './components/WeightControls';
import Statistics from './components/Statistics';
import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000';

function App() {
  const [countyInfo,       setCountyInfo]       = useState(null);
  const [weights,          setWeights]          = useState(null);
  const [criteria,         setCriteria]         = useState([]);
  const [analysisResult,   setAnalysisResult]   = useState(null);
  const [loading,          setLoading]          = useState(false);
  const [applyConstraints, setApplyConstraints] = useState(true);
  const [apiError,         setApiError]         = useState(null);

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
        console.error('Failed to load county config from API:', err);
        setApiError('Cannot reach the API at ' + API_BASE_URL);
      });
  }, []);

  const handleWeightChange = (criterion, value) => {
    const newWeights = { ...weights, [criterion]: value };
    const others     = Object.keys(weights).filter(k => k !== criterion);
    const otherTotal = others.reduce((s, k) => s + weights[k], 0);
    const remaining  = 1.0 - value;
    if (otherTotal > 0) {
      others.forEach(k => {
        newWeights[k] = (weights[k] / otherTotal) * remaining;
      });
    }
    setWeights(newWeights);
  };

  const resetWeights = () => {
    if (countyInfo) setWeights({ ...countyInfo.weights });
  };

  const runAnalysis = async () => {
    setLoading(true);
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

  // ── Error / Loading states ───────────────────────────────────────────────
  if (apiError) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', flexDirection: 'column', gap: '1rem',
        background: '#1a1f16', color: '#c07050',
      }}>
        <div style={{ fontSize: '2rem' }}>⚠</div>
        <div style={{ fontFamily: 'Courier New', fontSize: '0.85rem' }}>{apiError}</div>
        <div style={{ fontFamily: 'Courier New', fontSize: '0.75rem', color: '#3a4832' }}>
          Start the API: <code>python src/api.py</code>
        </div>
      </div>
    );
  }

  if (!countyInfo || !weights) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', color: '#3a4832', flexDirection: 'column', gap: '0.5rem',
        background: '#1a1f16', fontFamily: 'Courier New', fontSize: '0.8rem',
        letterSpacing: '0.08em', textTransform: 'uppercase',
      }}>
        <div>Loading configuration...</div>
      </div>
    );
  }

  const totalWeight = Object.values(weights).reduce((s, v) => s + v, 0);

  return (
    <div className="App">
      {/* ── Header ── */}
      <header className="header">
        <div className="header-left">
          <h1>🌿 Crop Suitability Engine</h1>
          <p>{countyInfo.display_name}, {countyInfo.country} — {countyInfo.crop} Analysis</p>
        </div>
        <div className="header-badge">MCDA v2.0</div>
      </header>

      {/* ── 3-column layout ── */}
      <div className="container">

        {/* ── LEFT: Weights + Controls ── */}
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
              <input
                type="checkbox"
                checked={applyConstraints}
                onChange={e => setApplyConstraints(e.target.checked)}
              />
              Apply protected area constraints
            </label>
            <button
              className="analyze-button"
              onClick={runAnalysis}
              disabled={loading || Math.abs(totalWeight - 1.0) > 0.01}
            >
              {loading ? '⏳ Analyzing...' : '▶ Run Analysis'}
            </button>
            <button className="reset-button" onClick={resetWeights}>
              Reset weights
            </button>
          </div>
        </div>

        {/* ── CENTER: Map ── */}
        <div className="main-content">
          <MapView analysisResult={analysisResult} countyInfo={countyInfo} />
        </div>

        {/* ── RIGHT: Results ── */}
        <div className="right-panel">
          <Statistics result={analysisResult} />
        </div>

      </div>

      {/* ── Footer ── */}
      <footer className="footer">
        Crop Suitability Engine &nbsp;·&nbsp; {countyInfo.display_name} &nbsp;·&nbsp; Multi-Criteria Decision Analysis
      </footer>
    </div>
  );
}

export default App;