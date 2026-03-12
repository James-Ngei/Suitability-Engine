import React, { useState, useEffect } from 'react';
import './App.css';
import MapView from './components/MapView';
import WeightControls from './components/WeightControls';
import Statistics from './components/Statistics';
import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000';

function App() {
  const [countyInfo,      setCountyInfo]      = useState(null);
  const [weights,         setWeights]         = useState(null);   // null until API loads
  const [criteria,        setCriteria]        = useState([]);
  const [analysisResult,  setAnalysisResult]  = useState(null);
  const [loading,         setLoading]         = useState(false);
  const [applyConstraints, setApplyConstraints] = useState(true);
  const [apiError,        setApiError]        = useState(null);

  // Load county info and criteria on mount — drives everything else
  useEffect(() => {
    Promise.all([
      axios.get(`${API_BASE_URL}/county`),
      axios.get(`${API_BASE_URL}/criteria`),
    ])
      .then(([countyRes, criteriaRes]) => {
        const info = countyRes.data;
        setCountyInfo(info);
        // Initialise weights from whatever the config says
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
    // Auto-normalise remaining weights proportionally
    const others      = Object.keys(weights).filter(k => k !== criterion);
    const otherTotal  = others.reduce((s, k) => s + weights[k], 0);
    const remaining   = 1.0 - value;
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

  // ── Render ───────────────────────────────────────────────────────────────────
  if (apiError) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', flexDirection: 'column', gap: '1rem', color: '#c62828'
      }}>
        <div style={{ fontSize: '2rem' }}>⚠️</div>
        <div style={{ fontWeight: 600 }}>{apiError}</div>
        <div style={{ fontSize: '0.9rem', color: '#666' }}>
          Start the API with: <code>python src/api.py</code>
        </div>
      </div>
    );
  }

  if (!countyInfo || !weights) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', color: '#666', flexDirection: 'column', gap: '0.5rem'
      }}>
        <div style={{ fontSize: '1.5rem' }}>⏳</div>
        <div>Loading county configuration...</div>
      </div>
    );
  }

  const totalWeight = Object.values(weights).reduce((s, v) => s + v, 0);

  return (
    <div className="App">
      <header className="header">
        <h1>🌾 {countyInfo.crop} Suitability Analysis</h1>
        <p>{countyInfo.display_name}, {countyInfo.country} — Multi-Criteria Decision Support System</p>
      </header>

      <div className="container">
        <div className="sidebar">
          {/* Weight controls — only rendered once weights are loaded */}
          <WeightControls
            weights={weights}
            criteria={criteria}
            onWeightChange={handleWeightChange}
            onReset={resetWeights}
            totalWeight={totalWeight}
          />

          <div className="controls-section">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={applyConstraints}
                onChange={e => setApplyConstraints(e.target.checked)}
              />
              Apply Protected Area Constraints
            </label>

            <button
              className="analyze-button"
              onClick={runAnalysis}
              disabled={loading || Math.abs(totalWeight - 1.0) > 0.01}
            >
              {loading ? 'Analyzing...' : '▶ Run Analysis'}
            </button>

            <button className="reset-button" onClick={resetWeights}>
              Reset to Defaults
            </button>
          </div>

          {analysisResult && <Statistics result={analysisResult} />}
        </div>

        <div className="main-content">
          <MapView analysisResult={analysisResult} countyInfo={countyInfo} />
        </div>
      </div>

      <footer className="footer">
        <p>
          Multi-Criteria Suitability Analysis Engine &nbsp;|&nbsp;
          {countyInfo.display_name} &nbsp;|&nbsp; Built with React + FastAPI
        </p>
      </footer>
    </div>
  );
}

export default App;