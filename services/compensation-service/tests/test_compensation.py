from app.core.engine import CompensationEngine

def test_shelf_expired_compensation():
    engine = CompensationEngine()
    result = engine.calculate_compensation("SHELF_EXPIRED", 500.0)
    assert result["compensation_amount"] == 500.0
    assert result["auto_approve"] == True

def test_delay_compensation():
    engine = CompensationEngine()
    result = engine.calculate_compensation("DELAY_15MIN", 500.0)
    assert result["compensation_amount"] == 100.0  # 20%
