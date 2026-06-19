#!/usr/bin/env bash
# Common functions for Outhora CLI wrappers.
# Sourced by individual tool wrappers — not executed directly.

set -euo pipefail

: "${OUTHORA_API_URL:=https://api.outhora.com}"
: "${OUTHORA_AGENT_ID:?OUTHORA_AGENT_ID must be set}"
: "${OUTHORA_AGENT_SECRET:?OUTHORA_AGENT_SECRET must be set}"
: "${OUTHORA_DEPT_ID:?OUTHORA_DEPT_ID must be set}"
: "${OUTHORA_USER_ID:=${USER:-unknown}}"
: "${OUTHORA_SESSION_ID:=}"

# ── Token Exchange ────────────────────────────────────────────────────────

outhora_get_token() {
    local response http_code body

    response=$(curl -s -w "\n%{http_code}" \
        --max-time 30 \
        -H "Content-Type: application/json" \
        -H "User-Agent: outhora-wrapper/1.0" \
        -X POST \
        -d "{\"agent_identifier\": \"${OUTHORA_AGENT_ID}\", \"agent_secret\": \"${OUTHORA_AGENT_SECRET}\", \"dept_id\": \"${OUTHORA_DEPT_ID}\"}" \
        "${OUTHORA_API_URL}/v1/agent-auth"
    )

    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "ERROR: Outhora agent auth failed (HTTP ${http_code}): ${body}" >&2
        exit 1
    fi

    echo "$body" | python3 -c "import json,sys; print(json.load(sys.stdin)['access_token'])"
}

# Fetch token once per wrapper invocation
OUTHORA_TOKEN=$(outhora_get_token)

# ── Authorization ────────────────────────────────────────────────────────

outhora_authorize() {
    local tool="$1"
    local command="$2"
    local reason="${3:-}"

    # Derive action_type as {tool}_{first_subcommand}
    local subcommand
    subcommand=$(echo "$command" | tr ' ' '\n' | grep -v '^-' | sed -n '2p')
    local action_type="${tool}${subcommand:+_${subcommand}}"

    # Use OUTHORA_REASON env var if set (Claude sets this to explain intent)
    local intent="${reason:-${OUTHORA_REASON:-}}"

    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
    'action_type': '${action_type}',
    'context': {
        'tool': '${tool}',
        'command': sys.argv[1],
        'reason': sys.argv[2],
        'agent_id': '${OUTHORA_AGENT_ID}',
        'dept_id': '${OUTHORA_DEPT_ID}',
        'user_id': '${OUTHORA_USER_ID}',
        'session_id': '${OUTHORA_SESSION_ID}',
        'repo': '$(pwd)',
        'branch': '$(/usr/bin/git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")',
    }
}))
" "$command" "${intent}")

    [[ "${OUTHORA_DEBUG:-0}" == "1" ]] && echo "[outhora] Request body: ${payload}" >&2 || true

    local response http_code body
    response=$(curl -s -w "\n%{http_code}" \
        --max-time 30 \
        -H "Authorization: Bearer ${OUTHORA_TOKEN}" \
        -H "Content-Type: application/json" \
        -H "User-Agent: outhora-wrapper/1.0" \
        -X POST \
        -d "$payload" \
        "${OUTHORA_API_URL}/v1/actions"
    )

    http_code=$(echo "$response" | tail -1)
    body=$(echo "$response" | sed '$d')

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        echo "ERROR: Outhora authorization failed (HTTP ${http_code}): ${body}" >&2
        exit 1
    fi

    echo "$body"
}

# ── Decision handling ────────────────────────────────────────────────────

outhora_get_status() {
    local response="$1"
    echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])"
}

outhora_get_request_id() {
    local response="$1"
    echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin).get('request_id',''))"
}

outhora_get_reason() {
    local response="$1"
    echo "$response" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('decision_reason', d.get('reason','')))"
}

outhora_get_approver() {
    local response="$1"
    echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin).get('approver',''))"
}

outhora_get_approval_token() {
    local response="$1"
    echo "$response" | python3 -c "import json,sys; print(json.load(sys.stdin).get('approval_token',''))"
}

outhora_handle_decision() {
    local action_response="$1"
    local status
    status=$(outhora_get_status "$action_response")

    case "$status" in
        approved)
            return 0
            ;;
        denied|rejected)
            local reason
            reason=$(outhora_get_reason "$action_response")
            echo "DENIED by Outhora: ${reason:-policy violation}" >&2
            exit 1
            ;;
        pending)
            local request_id approver reason
            request_id=$(outhora_get_request_id "$action_response")
            approver=$(outhora_get_approver "$action_response")
            reason=$(outhora_get_reason "$action_response")
            echo "" >&2
            echo "╔══════════════════════════════════════════════════════╗" >&2
            echo "║  Approval required in Outhora                        ║" >&2
            echo "╚══════════════════════════════════════════════════════╝" >&2
            echo "" >&2
            echo "  Request ID: ${request_id}" >&2
            echo "  Approver:   ${approver}" >&2
            echo "  Reason:     ${reason}" >&2
            echo "  Review at:  ${OUTHORA_API_URL}/approvals/${request_id}" >&2
            echo "" >&2
            echo "  Waiting for approval (Ctrl+C to cancel)..." >&2
            echo "" >&2

            # Poll until approved or rejected
            local poll_interval=5
            local poll_response poll_status
            while true; do
                sleep "$poll_interval"
                poll_response=$(curl -s \
                    --max-time 10 \
                    -H "Authorization: Bearer ${OUTHORA_TOKEN}" \
                    -H "User-Agent: outhora-wrapper/1.0" \
                    "${OUTHORA_API_URL}/v1/actions/${request_id}"
                )
                poll_status=$(echo "$poll_response" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','pending'))")

                case "$poll_status" in
                    approved)
                        echo "  ✓ Approved. Executing..." >&2
                        # Update action_response so caller can get approval_token
                        action_response="$poll_response"
                        return 0
                        ;;
                    rejected|denied)
                        local poll_reason
                        poll_reason=$(outhora_get_reason "$poll_response")
                        echo "DENIED by Outhora: ${poll_reason:-rejected by approver}" >&2
                        exit 1
                        ;;
                    failed)
                        local poll_reason
                        poll_reason=$(outhora_get_reason "$poll_response")
                        echo "ERROR: Outhora action failed: ${poll_reason:-unknown error}" >&2
                        echo "  Full response: ${poll_response}" >&2
                        exit 1
                        ;;
                    pending|escalated)
                        echo "  Still waiting..." >&2
                        ;;
                    *)
                        echo "ERROR: Unexpected status while polling: ${poll_status}" >&2
                        echo "  Response: ${poll_response}" >&2
                        exit 1
                        ;;
                esac
            done
            ;;
        *)
            echo "ERROR: Unknown status: ${status}" >&2
            exit 1
            ;;
    esac
}

# ── Resolve real binary ─────────────────────────────────────────────────

outhora_find_real_binary() {
    local tool="$1"
    local wrapper_dir
    wrapper_dir=$(dirname "$(readlink -f "${BASH_SOURCE[1]}" 2>/dev/null || echo "${BASH_SOURCE[1]}")")

    local real_binary=""
    local IFS=":"
    for dir in $PATH; do
        if [[ "$dir" != "$wrapper_dir" && -x "$dir/$tool" ]]; then
            real_binary="$dir/$tool"
            break
        fi
    done

    if [[ -z "$real_binary" ]]; then
        echo "ERROR: Could not find real '${tool}' binary in PATH" >&2
        exit 1
    fi

    echo "$real_binary"
}
