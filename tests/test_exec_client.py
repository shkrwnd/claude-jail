"""Tests for the container-side execution protocol client (wrappers/exec_client.py)."""

import json
import os
import socket
import sys
import threading
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "wrappers"))

import exec_client
from exec_client import ExecutionResult


# ---------------------------------------------------------------------------
# Minimal TCP test server
# ---------------------------------------------------------------------------

class MockExecServer:
    """Minimal localhost TCP HTTP server standing in for the host execution server."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))  # OS-assigned free port
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.port}"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except OSError:
                break

    last_headers: dict = {}

    def _handle(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk

        # Parse Content-Length to read body
        headers, _, rest = data.partition(b"\r\n\r\n")
        content_length = 0
        self.last_headers = {}
        for line in headers.split(b"\r\n"):
            name, sep, value = line.partition(b":")
            if sep:
                self.last_headers[name.decode().lower()] = value.strip().decode()
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())
        body_bytes = rest
        while len(body_bytes) < content_length:
            body_bytes += conn.recv(4096)

        request = json.loads(body_bytes) if body_bytes else {}
        response = self._dispatch(request)
        response_body = json.dumps(response).encode()

        conn.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
            b"\r\n" + response_body
        )
        conn.close()

    def _dispatch(self, request: dict) -> dict:
        tool = request.get("tool", "")
        args = request.get("args", [])
        command = " ".join([tool] + args)
        if "destroy" in command or "delete" in command:
            return {"stdout": "", "stderr": "DENIED: destructive action blocked", "exit_code": 1}
        return {"stdout": f"mock output for: {command}\n", "stderr": "", "exit_code": 0}

    def stop(self) -> None:
        self._sock.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExecClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server = MockExecServer()

    @classmethod
    def tearDownClass(cls):
        cls._server.stop()

    def test_approved_command_returns_output(self):
        result = exec_client.execute("git", ["status"], server=self._server.endpoint)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("mock output", result.stdout)

    def test_denied_command_returns_nonzero(self):
        result = exec_client.execute("kubectl", ["delete", "pod", "mypod"], server=self._server.endpoint)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("DENIED", result.stderr)

    def test_result_fields_populated(self):
        result = exec_client.execute(
            "aws", ["s3", "ls"], reason="listing buckets", server=self._server.endpoint,
        )
        self.assertIsInstance(result, ExecutionResult)
        self.assertIsInstance(result.exit_code, int)
        self.assertIsInstance(result.stdout, str)
        self.assertIsInstance(result.stderr, str)

    def test_server_from_env(self):
        with unittest.mock.patch.dict(os.environ, {"EXEC_SERVER": self._server.endpoint}):
            result = exec_client.execute("git", ["status"])
        self.assertEqual(result.exit_code, 0)

    def test_exec_token_header_sent(self):
        with unittest.mock.patch.dict(os.environ, {"EXEC_TOKEN": "sekrit"}):
            exec_client.execute("git", ["status"], server=self._server.endpoint)
        self.assertEqual(self._server.last_headers.get("x-exec-token"), "sekrit")

    def test_server_not_listening_returns_error(self):
        # Port 1 on localhost is essentially guaranteed to refuse connections
        result = exec_client.execute("aws", ["s3", "ls"], server="127.0.0.1:1")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not listening", result.stderr)

    def test_stdlib_only(self):
        """The container-side client must not depend on host-side code."""
        import ast
        path = os.path.join(os.path.dirname(__file__), "..", "wrappers", "exec_client.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for mod in modules:
                top = mod.split(".")[0]
                self.assertNotIn(top, ("server", "sdk"), f"container code imports host module {mod!r}")


if __name__ == "__main__":
    unittest.main()
