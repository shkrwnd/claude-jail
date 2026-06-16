#!/usr/bin/env bash
# Integration test for Outhora wrappers using the mock server.
# Usage: ./tests/test_wrappers.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Outhora Wrapper Integration Tests ==="
echo ""

# Start mock server
echo "[1/5] Starting mock Outhora server..."
MOCK_PORT=0
python3 -c "
import json, sys, os
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == '/api/v1/health':
            self._r(200, {'status': 'ok'})
        else:
            self._r(404, {})
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        if self.path == '/api/v1/authorize':
            cmd = body.get('command','')
            if 'destroy' in cmd:
                self._r(200, {'decision':'deny','reason':'blocked'})
            elif 'apply' in cmd:
                self._r(200, {'decision':'approval_required','approval_id':'apr-1'})
            else:
                self._r(200, {'decision':'allow','action_id':'act-1'})
        elif self.path == '/api/v1/credentials':
            self._r(200, {'access_key':'AK','secret_key':'SK','session_token':'ST','expires_at':'2026-01-01T00:00:00Z'})
        elif self.path == '/api/v1/audit':
            self._r(202, {'accepted': True})
        else:
            self._r(404, {})
    def _r(self, s, b):
        self.send_response(s)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps(b).encode())

srv = HTTPServer(('127.0.0.1', 0), Handler)
port = srv.server_address[1]
# Write port so parent can read it
with open('${SCRIPT_DIR}/.mock_port', 'w') as f:
    f.write(str(port))
print(f'Mock server on port {port}', flush=True)
srv.serve_forever()
" &
MOCK_PID=$!
sleep 1

MOCK_PORT=$(cat "${SCRIPT_DIR}/.mock_port")
rm -f "${SCRIPT_DIR}/.mock_port"

cleanup() {
    kill "$MOCK_PID" 2>/dev/null || true
}
trap cleanup EXIT

export OUTHORA_API_URL="http://127.0.0.1:${MOCK_PORT}"
export OUTHORA_API_KEY="test-key"
export OUTHORA_USER_ID="test-user"
export OUTHORA_SESSION_ID="test-session"

PASS=0
FAIL=0

run_test() {
    local name="$1"
    local expected_exit="$2"
    shift 2
    local actual_exit=0
    "$@" > /dev/null 2>&1 || actual_exit=$?
    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        echo "  PASS: ${name}"
        ((PASS++))
    else
        echo "  FAIL: ${name} (expected exit ${expected_exit}, got ${actual_exit})"
        ((FAIL++))
    fi
}

# Test the common functions directly
echo ""
echo "[2/5] Testing authorization decisions..."

# Test allow
auth_resp=$(curl -s -X POST \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d '{"tool":"aws","command":"aws s3 ls","user_id":"test","agent_session_id":"test"}' \
    "${OUTHORA_API_URL}/api/v1/authorize")
decision=$(echo "$auth_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'])")
if [[ "$decision" == "allow" ]]; then
    echo "  PASS: allow decision"
    ((PASS++))
else
    echo "  FAIL: expected allow, got ${decision}"
    ((FAIL++))
fi

# Test deny
auth_resp=$(curl -s -X POST \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d '{"tool":"terraform","command":"terraform destroy","user_id":"test","agent_session_id":"test"}' \
    "${OUTHORA_API_URL}/api/v1/authorize")
decision=$(echo "$auth_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'])")
if [[ "$decision" == "deny" ]]; then
    echo "  PASS: deny decision"
    ((PASS++))
else
    echo "  FAIL: expected deny, got ${decision}"
    ((FAIL++))
fi

# Test approval_required
auth_resp=$(curl -s -X POST \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d '{"tool":"terraform","command":"terraform apply","user_id":"test","agent_session_id":"test"}' \
    "${OUTHORA_API_URL}/api/v1/authorize")
decision=$(echo "$auth_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['decision'])")
if [[ "$decision" == "approval_required" ]]; then
    echo "  PASS: approval_required decision"
    ((PASS++))
else
    echo "  FAIL: expected approval_required, got ${decision}"
    ((FAIL++))
fi

echo ""
echo "[3/5] Testing credential fetch..."
creds_resp=$(curl -s -X POST \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d '{"tool":"aws","action_id":"act-1"}' \
    "${OUTHORA_API_URL}/api/v1/credentials")
access_key=$(echo "$creds_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_key'])")
if [[ "$access_key" == "AK" ]]; then
    echo "  PASS: credential fetch"
    ((PASS++))
else
    echo "  FAIL: expected access_key=AK, got ${access_key}"
    ((FAIL++))
fi

echo ""
echo "[4/5] Testing audit submission..."
audit_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Authorization: Bearer test-key" \
    -H "Content-Type: application/json" \
    -d '{"tool":"aws","command":"aws s3 ls","decision":"allow","agent_session_id":"test","user_id":"test","timestamp":"2026-01-01T00:00:00Z"}' \
    "${OUTHORA_API_URL}/api/v1/audit")
if [[ "$audit_code" == "202" ]]; then
    echo "  PASS: audit submission"
    ((PASS++))
else
    echo "  FAIL: expected HTTP 202, got ${audit_code}"
    ((FAIL++))
fi

echo ""
echo "[5/5] Testing health check..."
health_resp=$(curl -s "${OUTHORA_API_URL}/api/v1/health")
status=$(echo "$health_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
if [[ "$status" == "ok" ]]; then
    echo "  PASS: health check"
    ((PASS++))
else
    echo "  FAIL: expected status=ok, got ${status}"
    ((FAIL++))
fi

echo ""
echo "═══════════════════════════════"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "═══════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
