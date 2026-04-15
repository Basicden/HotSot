"""
HotSot Order Service — Core Module.

Production-grade order lifecycle management with:
- V2 16-state state machine
- Saga pattern with choreography
- Kafka event handlers
- Multi-tenant database models
"""

from app.core.state_machine import state_machine, InvalidTransitionError, GuardViolationError
from app.core.saga import order_saga, OrderSaga, SagaInstance, ORDER_SAGA_STEPS
