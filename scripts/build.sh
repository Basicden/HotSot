#!/bin/bash
# HotSot — Build all Docker images
set -e

SERVICES=(
    order-service kitchen-service shelf-service eta-service
    notification-service realtime-service api-gateway ml-service
    arrival-service compensation-service
)

REGISTRY="${DOCKER_REGISTRY:-ghcr.io/basicden/hotsot}"
TAG="${DOCKER_TAG:-latest}"

echo "=============================="
echo " HotSot Docker Build"
echo " Registry: $REGISTRY"
echo " Tag:      $TAG"
echo "=============================="

for svc in "${SERVICES[@]}"; do
    echo ""
    echo ">>> Building $svc ..."
    docker build \
        -f "services/$svc/Dockerfile" \
        -t "$REGISTRY/$svc:$TAG" \
        -t "$REGISTRY/$svc:$(git rev-parse --short HEAD 2>/dev/null || echo 'dev')" \
        "services/$svc/"
    echo "<<< Built $svc ✓"
done

echo ""
echo "=============================="
echo " All 10 services built!"
echo "=============================="
