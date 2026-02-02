"""
API Test Client
Test the suitability analysis API endpoints
"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_health():
    """Test health check"""
    print("=== Testing Health Check ===")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    print()

def test_criteria():
    """Test criteria endpoint"""
    print("=== Testing Criteria Endpoint ===")
    response = requests.get(f"{BASE_URL}/criteria")
    print(f"Status: {response.status_code}")
    data = response.json()
    for criterion in data:
        print(f"  {criterion['name']}: {criterion['description']}")
        print(f"    Optimal: {criterion['optimal_range']}, Weight: {criterion['current_weight']}")
    print()

def test_default_weights():
    """Test default weights"""
    print("=== Testing Default Weights ===")
    response = requests.get(f"{BASE_URL}/default-weights")
    print(f"Status: {response.status_code}")
    print(json.dumps(response.json(), indent=2))
    print()

def test_analysis_default():
    """Test analysis with default weights"""
    print("=== Testing Analysis (Default Weights) ===")
    
    payload = {
        "weights": {
            "rainfall": 0.25,
            "elevation": 0.20,
            "temperature": 0.20,
            "soil": 0.20,
            "slope": 0.15
        },
        "apply_constraints": True
    }
    
    response = requests.post(f"{BASE_URL}/analyze", json=payload)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        result = response.json()
        print("\nStatistics:")
        for key, value in result['statistics'].items():
            print(f"  {key}: {value:.2f}")
        
        print("\nClassification:")
        for key, value in result['classification'].items():
            print(f"  {key}: {value:.1f}%")
        
        print(f"\nTimestamp: {result['timestamp']}")
    else:
        print(f"Error: {response.text}")
    print()

def test_analysis_custom():
    """Test analysis with custom weights (emphasis on rainfall)"""
    print("=== Testing Analysis (Rainfall-Heavy) ===")
    
    payload = {
        "weights": {
            "rainfall": 0.40,  # Increased
            "elevation": 0.15,
            "temperature": 0.15,
            "soil": 0.15,
            "slope": 0.15
        },
        "apply_constraints": True
    }
    
    response = requests.post(f"{BASE_URL}/analyze", json=payload)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        result = response.json()
        print("\nStatistics:")
        print(f"  Mean suitability: {result['statistics']['mean']:.2f}")
        print(f"  Highly suitable area: {result['classification']['highly_suitable_pct']:.1f}%")
    else:
        print(f"Error: {response.text}")
    print()

def test_invalid_weights():
    """Test validation (weights don't sum to 1)"""
    print("=== Testing Validation (Invalid Weights) ===")
    
    payload = {
        "weights": {
            "rainfall": 0.30,
            "elevation": 0.20,
            "temperature": 0.20,
            "soil": 0.20,
            "slope": 0.20  # Sum = 1.1
        },
        "apply_constraints": True
    }
    
    response = requests.post(f"{BASE_URL}/analyze", json=payload)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text}")
    print()

def run_all_tests():
    """Run all API tests"""
    print("=" * 70)
    print("SUITABILITY ANALYSIS API - TEST SUITE")
    print("=" * 70)
    print()
    
    try:
        test_health()
        test_criteria()
        test_default_weights()
        test_analysis_default()
        test_analysis_custom()
        test_invalid_weights()
        
        print("=" * 70)
        print("ALL TESTS COMPLETED")
        print("=" * 70)
        
    except requests.exceptions.ConnectionError:
        print("❌ Error: Cannot connect to API")
        print("Make sure the API is running: python src/api.py")

if __name__ == "__main__":
    run_all_tests()