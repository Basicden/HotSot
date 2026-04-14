#!/bin/bash
# HotSot — Push images to registry + deploy to Kubernetes
set -e

SERVICES=(
    order-service kitchen-service shelf-service eta-service
    notification-service realtime-service api-gateway ml-service
    arrival-service compensation-service
)

REGISTRY="${DOCKER_REGISTRY:-ghcr.io/basicden/hotsot}"
TAG="${DOCKER_TAG:-latest}"
NAMESPACE="${K8S_NAMESPACE:-hotsot}"

echo "=============================="
echo " HotSot Deploy"
echo " Registry:   $REGISTRY"
echo " Tag:        $TAG"
echo " Namespace:  $NAMESPACE"
echo "=============================="

# Push images
for svc in "${SERVICES[@]}"; do
    echo ">>> Pushing $svc ..."
    docker push "$REGISTRY/$svc:$TAG" 2>/dev/null || echo "  (push skipped — image may not exist locally)"
done

# Deploy to Kubernetes
echo ""
echo ">>> Applying Kubernetes manifests ..."
kubectl apply -f infra/kubernetes/configmap.yaml -n "$NAMESPACE" 2>/dev/null || true
kubectl apply -f infra/kubernetes/hpa.yaml -n "$NAMESPACE" 2>/dev/null || true

for svc in "${SERVICES[@]}"; do
    manifest="infra/kubernetes/${svc}.yaml"
    if [ -f "$manifest" ]; then
        echo "  Deploying $svc ..."
        kubectl apply -f "$manifest" -n "$NAMESPACE" 2>/dev/null || echo "  (skipped — $manifest not found or kubectl unavailable)"
    fi
done

# API Gateway
kubectl apply -f infra/kubernetes/api-gateway.yaml -n "$NAMESPACE" 2>/dev/null || true

echo ""
echo "=============================="
echo " Deploy complete!"
echo " Check status: kubectl get pods -n $NAMESPACE"
echo "=============================="
