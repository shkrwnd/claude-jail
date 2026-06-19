#!/usr/bin/env bash
# Run all Outhora tests inside a Docker container.
# Usage: ./tests/run-tests.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Building test container ==="
docker build -t outhora-test -f "${SCRIPT_DIR}/Dockerfile.test" "$PROJECT_DIR"

echo ""
echo "=== Running Python SDK tests ==="
docker run --rm \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    outhora-test \
    pytest tests/test_sdk.py -v

echo ""
echo "=== Running wrapper integration tests ==="
docker run --rm \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    outhora-test \
    bash tests/test_wrappers.sh

echo ""
echo "=== All tests passed ==="
