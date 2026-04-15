"""
HotSot Order Service — Production-Grade Order Lifecycle Management.

V2 Microservice with Saga Pattern + Choreography for the HotSot
cloud kitchen platform at 10,000+ vendor scale.

Features:
- 16-state order lifecycle (CREATED → PICKED)
- Saga pattern with choreography for distributed transactions
- Kafka event streaming with manual commit and DLQ
- Razorpay payment integration with escrow model
- Multi-tenant isolation (tenant_id on every model)
- OpenTelemetry observability
- Production-grade error handling and compensation
"""
