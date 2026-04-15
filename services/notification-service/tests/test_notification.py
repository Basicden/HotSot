from app.core.dispatcher import NotificationDispatcher

def test_trai_compliance_check():
    # Transactional messages should pass
    assert NotificationDispatcher._is_transactional("Order Ready", "Your order is ready for pickup")
    assert not NotificationDispatcher._is_transactional("Sale!", "Get 50% off today")
