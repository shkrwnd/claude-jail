#!/usr/bin/env python3
"""Execution Server — runs on the host outside the container.

The container sends tool execution requests; this server handles
authorization, credential fetching, and subprocess execution. The container
only receives stdout/stderr/exit_code.

Listens on TCP 127.0.0.1:8377 (loopback only — not reachable from the
network). The container connects to host.docker.internal:8377, which Docker
forwards to the host loopback. Unix sockets are not used: Docker Desktop
(macOS/Windows) cannot reliably share them between host and container.

Usage:
    python3 server/main.py [--port 8377] [--env deploy/server.env]
"""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import os
import signal
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import server.handler as handler  # noqa: E402

_DEFAULT_PORT = 8377
_DEFAULT_ENV = os.path.join(_ROOT, "deploy", "server.env")
_DEFAULTS_ENV = os.path.join(_ROOT, "deploy", "server.defaults.env")
_TOKEN_FILE = os.path.join(_ROOT, "deploy", "exec.token")

# Env vars each auth backend requires (no defaults exist for these — they are
# secrets and must be set in deploy/server.env).
_REQUIRED_ENV = {
    "outhora": ("OUTHORA_AGENT_ID", "OUTHORA_AGENT_SECRET", "OUTHORA_DEPT_ID"),
    "webhook": ("AUTH_WEBHOOK_URL",),
    "allow_all": (),
}


class ExecutionHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silent: handler.py logs each request with the actual command,
        # decision, and exit code — the raw HTTP line adds no information.
        pass

    def do_POST(self):
        if self.path != "/execute":
            self._respond(404, {"error": f"Unknown path: {self.path}"})
            return

        # Shared-secret auth: only callers that know EXEC_TOKEN (the container,
        # via deploy/exec.token) may execute — not arbitrary local processes.
        expected = os.environ.get("EXEC_TOKEN", "")
        provided = self.headers.get("X-Exec-Token", "")
        if not expected or not hmac.compare_digest(provided, expected):
            self._respond(401, {"error": "unauthorized: missing or invalid X-Exec-Token"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError as exc:
            self._respond(400, {"error": f"Invalid JSON: {exc}"})
            return

        result = handler.execute(body)
        self._respond(200, result)

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _load_env_file(path: str) -> None:
    """Load a dotenv-style file and set env vars (does not override existing)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Don't override env vars already set (CLI or shell take precedence)
                if key and not os.environ.get(key):
                    os.environ[key] = value
    except FileNotFoundError:
        print(f"[server] WARNING: Env file not found at {path}.", flush=True)
        print("[server] Run 'sh deploy/create-configs.sh' to create missing config files.", flush=True)
        return

    print(f"[server] Loaded env from {path}", flush=True)


def _missing_backend_env() -> tuple[str, list[str]]:
    """Return (backend, missing required env vars) for the configured backend."""
    backend = os.environ.get("AUTH_BACKEND") or "allow_all"
    required = _REQUIRED_ENV.get(backend, ())
    return backend, [key for key in required if not os.environ.get(key)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Execution Server")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT,
                        help=f"TCP port on 127.0.0.1 (default: {_DEFAULT_PORT})")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--env", default=_DEFAULT_ENV, help="Path to server.env file")
    args = parser.parse_args()

    # Precedence (first set wins): real env / CLI > server.env > defaults
    _load_env_file(args.env)
    _load_env_file(_DEFAULTS_ENV)
    _load_env_file(_TOKEN_FILE)

    if not os.environ.get("EXEC_TOKEN"):
        print("[server] ERROR: No EXEC_TOKEN configured. Generate one with:", flush=True)
        print("[server]   sh deploy/create-configs.sh", flush=True)
        sys.exit(1)

    backend, missing = _missing_backend_env()
    if missing:
        print(f"[server] ERROR: AUTH_BACKEND={backend} requires "
              f"{', '.join(missing)} — set them in deploy/server.env.", flush=True)
        sys.exit(1)
    print(f"[server] Auth backend: {backend}", flush=True)

    server = http.server.HTTPServer((args.host, args.port), ExecutionHandler)
    print(f"[server] Listening on {args.host}:{args.port}", flush=True)

    def _shutdown(sig, frame):
        print("\n[server] Shutting down...", flush=True)
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("[server] Ready. Waiting for requests from the container.", flush=True)
    print("[server] Press Ctrl+C to stop.", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
