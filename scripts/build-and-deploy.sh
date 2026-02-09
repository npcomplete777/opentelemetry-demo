#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# build-and-deploy.sh — Build service images and deploy to k3d cluster
#
# Usage:
#   ./scripts/build-and-deploy.sh <service> [service2 ...]
#   ./scripts/build-and-deploy.sh --all-changed    # build only git-changed services
#   ./scripts/build-and-deploy.sh --list           # list available services
#
# Examples:
#   ./scripts/build-and-deploy.sh frontend
#   ./scripts/build-and-deploy.sh frontend recommendation
#   ./scripts/build-and-deploy.sh --all-changed
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
K3D_CLUSTER="${K3D_CLUSTER:-k3d-gitops-demo}"
K8S_NAMESPACE="${K8S_NAMESPACE:-otel-demo}"
IMAGE_PREFIX="${IMAGE_PREFIX:-ghcr.io/npcomplete777/opentelemetry-demo}"

# Service -> Dockerfile mapping
declare -A DOCKERFILES=(
  [ad]="src/ad/Dockerfile"
  [cart]="src/cart/src/Dockerfile"
  [checkout]="src/checkout/Dockerfile"
  [currency]="src/currency/Dockerfile"
  [email]="src/email/Dockerfile"
  [frontend]="src/frontend/Dockerfile"
  [frontend-proxy]="src/frontend-proxy/Dockerfile"
  [fraud-detection]="src/fraud-detection/Dockerfile"
  [image-provider]="src/image-provider/Dockerfile"
  [kafka]="src/kafka/Dockerfile"
  [llm]="src/llm/Dockerfile"
  [load-generator]="src/load-generator/Dockerfile"
  [payment]="src/payment/Dockerfile"
  [product-catalog]="src/product-catalog/Dockerfile"
  [product-reviews]="src/product-reviews/Dockerfile"
  [quote]="src/quote/Dockerfile"
  [recommendation]="src/recommendation/Dockerfile"
  [shipping]="src/shipping/Dockerfile"
  [accounting]="src/accounting/Dockerfile"
)

# Service -> K8s deployment name mapping (most are 1:1)
declare -A DEPLOY_NAMES=(
  [ad]="ad"
  [cart]="cart"
  [checkout]="checkout"
  [currency]="currency"
  [email]="email"
  [frontend]="frontend"
  [frontend-proxy]="frontend-proxy"
  [fraud-detection]="fraud-detection"
  [image-provider]="image-provider"
  [kafka]="kafka"
  [llm]="llm"
  [load-generator]="load-generator"
  [payment]="payment"
  [product-catalog]="product-catalog"
  [product-reviews]="product-reviews"
  [quote]="quote"
  [recommendation]="recommendation"
  [shipping]="shipping"
  [accounting]="accounting"
)

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLUE}[build]${NC} $*"; }
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }

list_services() {
  echo "Available services:"
  for svc in $(echo "${!DOCKERFILES[@]}" | tr ' ' '\n' | sort); do
    echo "  $svc  (${DOCKERFILES[$svc]})"
  done
}

detect_changed() {
  local changed=()
  local files
  files=$(cd "$REPO_ROOT" && git diff --name-only HEAD~1 HEAD 2>/dev/null || git diff --name-only HEAD)

  for svc in "${!DOCKERFILES[@]}"; do
    local src_dir="src/${svc}"
    if echo "$files" | grep -q "^${src_dir}/"; then
      changed+=("$svc")
    fi
  done

  if [[ ${#changed[@]} -eq 0 ]]; then
    warn "No changed services detected in git diff"
    exit 0
  fi

  echo "${changed[@]}"
}

build_service() {
  local service="$1"
  local dockerfile="${DOCKERFILES[$service]}"
  local sha_short
  sha_short=$(cd "$REPO_ROOT" && git rev-parse --short HEAD)
  local image_tag="${IMAGE_PREFIX}/${service}:${sha_short}"
  local image_latest="${IMAGE_PREFIX}/${service}:latest"

  if [[ ! -f "$REPO_ROOT/$dockerfile" ]]; then
    err "Dockerfile not found: $dockerfile"
    return 1
  fi

  log "Building $service ($dockerfile)"
  log "  Image: $image_tag"

  docker build \
    -t "$image_tag" \
    -t "$image_latest" \
    -f "$REPO_ROOT/$dockerfile" \
    "$REPO_ROOT" \
    --label "org.opencontainers.image.revision=$(cd "$REPO_ROOT" && git rev-parse HEAD)" \
    --label "org.opencontainers.image.source=https://github.com/npcomplete777/opentelemetry-demo"

  ok "Built $image_tag"

  # Import into k3d
  log "Importing into k3d cluster: $K3D_CLUSTER"
  k3d image import "$image_tag" "$image_latest" -c "${K3D_CLUSTER#k3d-}" 2>/dev/null || \
  k3d image import "$image_tag" "$image_latest" -c "$K3D_CLUSTER" 2>/dev/null || {
    # Try without k3d- prefix
    local cluster_name="${K3D_CLUSTER#k3d-}"
    warn "Retrying import with cluster name: $cluster_name"
    k3d image import "$image_tag" "$image_latest" -c "$cluster_name"
  }
  ok "Imported into k3d"

  # Patch the deployment to use the new image and restart
  local deploy_name="${DEPLOY_NAMES[$service]}"
  log "Patching deployment/$deploy_name to use $image_tag"

  kubectl set image "deployment/$deploy_name" \
    "$deploy_name=$image_tag" \
    -n "$K8S_NAMESPACE" 2>/dev/null && \
  ok "Deployment patched" || {
    warn "kubectl set image failed, trying rollout restart"
    kubectl rollout restart "deployment/$deploy_name" -n "$K8S_NAMESPACE"
  }

  # Wait for rollout
  log "Waiting for rollout..."
  kubectl rollout status "deployment/$deploy_name" -n "$K8S_NAMESPACE" --timeout=120s && \
  ok "$service deployed and healthy" || \
  warn "$service rollout timed out — check: kubectl get pods -n $K8S_NAMESPACE -l opentelemetry.io/name=$deploy_name"

  echo ""
}

# =============================================================================
# Main
# =============================================================================

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 <service> [service2 ...] | --all-changed | --list"
  echo ""
  list_services
  exit 1
fi

case "$1" in
  --list|-l)
    list_services
    exit 0
    ;;
  --all-changed|-a)
    SERVICES=($(detect_changed))
    ;;
  *)
    SERVICES=("$@")
    ;;
esac

# Validate all services before building
for svc in "${SERVICES[@]}"; do
  if [[ -z "${DOCKERFILES[$svc]:-}" ]]; then
    err "Unknown service: $svc"
    echo ""
    list_services
    exit 1
  fi
done

log "Services to build: ${SERVICES[*]}"
log "Cluster: $K3D_CLUSTER"
log "Namespace: $K8S_NAMESPACE"
echo ""

BUILT=0
FAILED=0
for svc in "${SERVICES[@]}"; do
  if build_service "$svc"; then
    ((BUILT++))
  else
    ((FAILED++))
    err "Failed to build $svc"
  fi
done

echo ""
echo "=============================="
ok "Built: $BUILT  Failed: $FAILED"
echo "=============================="

[[ $FAILED -eq 0 ]] || exit 1
