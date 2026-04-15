"""HotSot ETA Service — Tests."""

from app.core.predictor import ETAPredictor


def test_risk_levels():
    assert ETAPredictor._determine_risk(0, 0, False) == "LOW"
    assert ETAPredictor._determine_risk(90, 40, True) == "CRITICAL"
    assert ETAPredictor._determine_risk(70, 20, False) == "MEDIUM"


def test_item_complexity():
    items = [{"name": "biryani"}, {"name": "samosa"}]
    complexity = ETAPredictor._calculate_item_complexity(items)
    assert complexity > 0  # biryani adds complexity

    simple = [{"name": "water"}]
    assert ETAPredictor._calculate_item_complexity(simple) == 0
