"""HotSot ETA Service — Unit Tests."""
from app.core.predictor import predict_eta, _calculate_confidence, _assess_risk

def test_rule_based_prediction():
    result = predict_eta(kitchen_load=5, queue_length=3, item_complexity=2, time_of_day=12)
    assert result.eta_seconds >= 60
    assert 0 < result.confidence <= 1.0
    assert result.risk_level in ["LOW", "MEDIUM", "HIGH"]
    assert len(result.confidence_interval) == 2

def test_peak_hour_multiplier():
    normal = predict_eta(kitchen_load=5, queue_length=3, is_peak_hour=False)
    peak = predict_eta(kitchen_load=5, queue_length=3, is_peak_hour=True)
    assert peak.eta_seconds >= normal.eta_seconds

def test_festival_multiplier():
    normal = predict_eta(kitchen_load=5, queue_length=3, is_festival=False)
    festival = predict_eta(kitchen_load=5, queue_length=3, is_festival=True)
    assert festival.eta_seconds >= normal.eta_seconds

def test_monsoon_multiplier():
    normal = predict_eta(kitchen_load=5, queue_length=3, is_monsoon=False)
    monsoon = predict_eta(kitchen_load=5, queue_length=3, is_monsoon=True)
    assert monsoon.eta_seconds >= normal.eta_seconds

def test_confidence_decreases_with_load():
    low_load = _calculate_confidence(2, 1)
    high_load = _calculate_confidence(20, 10)
    assert low_load > high_load

def test_risk_assessment():
    assert _assess_risk(300, 0.9) == "LOW"
    assert _assess_risk(300, 0.7) == "MEDIUM"
    assert _assess_risk(900, 0.5) == "HIGH"

def test_minimum_eta():
    result = predict_eta(kitchen_load=0, queue_length=0, item_complexity=0, staff_count=10)
    assert result.eta_seconds >= 60

def test_model_version():
    result = predict_eta()
    assert result.model_version in ["v1_lgbm", "v1_rule_based"]
