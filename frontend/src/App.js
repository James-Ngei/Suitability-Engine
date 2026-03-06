import React, { useState, useEffect } from 'react';
import './App.css';
import MapView from './components/MapView';
import WeightControls from './components/WeightControls';
import Statistics from './components/Statistics';
import axios from 'axios';

const API_BASE_URL = 'http://localhost:8000';

function App() {
  const [weights, setWeights] = useState({
    rainfall: 0.25,
    elevation: 0.20,
    temperature: 0.20,
    soil: 0.20,
    slope: 0.15
  });

  const [analysisResult, setAnalysisResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [criteria, setCriteria] = useState([]);
  const [applyConstraints, setApplyConstraints] = useState(true);

  // Load criteria on mount
  useEffect(() => {
    loadCriteria();
  }, []);

  const loadCriteria = async () => {
    try {
      const response = await axios.get(`${API_BASE_URL}/criteria`);
      setCriteria(response.data);
    } catch (error) {
      console.error('Error loading criteria:', error);
    }
  };

  const runAnalysis = async () => {
    setLoading(true);
    
    try {
      const response = await axios.post(`${API_BASE_URL}/analyze`, {
        weights: weights,
        apply_constraints: applyConstraints
      });
      
      setAnalysisResult(response.data);
    } catch (error) {
      console.error('Error running analysis:', error);
      alert('Error: ' + (error.response?.data?.detail || error.message));
    } finally {
      setLoading(false);
    }
  };

  const handleWeightChange = (criterion, value) => {
    const newWeights = { ...weights, [criterion]: value };
    
    // Auto-normalize other weights
    const changedWeight = value;
    const otherCriteria = Object.keys(weights).filter(k => k !== criterion);
    const otherTotal = otherCriteria.reduce((sum, k) => sum + weights[k], 0);
    
    if (otherTotal > 0) {
      const remainingWeight = 1.0 - changedWeight;
      otherCriteria.forEach(k => {
        newWeights[k] = (weights[k] / otherTotal) * remainingWeight;
      });
    }
    
    setWeights(newWeights);
  };

  const resetWeights = () => {
    setWeights({
      rainfall: 0.25,
      elevation: 0.20,
      temperature: 0.20,
      soil: 0.20,
      slope: 0.15
    });
  };

  return (
    <div className="App">
      <header className="header">
        <h1>🌾 Cotton Suitability Analysis</h1>
        <p>Bungoma County, Kenya - Multi-Criteria Decision Support System</p>
      </header>

      <div className="container">
        <div className="sidebar">
          <WeightControls
            weights={weights}
            criteria={criteria}
            onWeightChange={handleWeightChange}
            onReset={resetWeights}
          />

          <div className="controls-section">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={applyConstraints}
                onChange={(e) => setApplyConstraints(e.target.checked)}
              />
              Apply Protected Area Constraints
            </label>

            <button 
              className="analyze-button"
              onClick={runAnalysis}
              disabled={loading}
            >
              {loading ? 'Analyzing...' : '▶ Run Analysis'}
            </button>

            <button 
              className="reset-button"
              onClick={resetWeights}
            >
              Reset to Defaults
            </button>
          </div>

          {analysisResult && (
            <Statistics result={analysisResult} />
          )}
        </div>

        <div className="main-content">
          <MapView analysisResult={analysisResult} />
        </div>
      </div>

      <footer className="footer">
        <p>Multi-Criteria Suitability Analysis Engine | Built with React + FastAPI</p>
      </footer>
    </div>
  );
}

export default App;
