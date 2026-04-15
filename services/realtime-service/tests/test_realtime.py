from app.core.connection_manager import ConnectionManager
def test_manager():
    m = ConnectionManager()
    assert m.vendor_count == 0
