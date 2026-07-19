#!/usr/bin/env bash
# Run all tests inside a Docker container.
# Usage: ./tests/run-tests.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Building test container ==="
docker build -t claude-jail-test -f "${SCRIPT_DIR}/Dockerfile.test" "$PROJECT_DIR"

echo ""
echo "=== Running Python tests ==="
docker run --rm \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    claude-jail-test \
    pytest tests/ -v

echo ""
echo "=== All tests passed ==="
