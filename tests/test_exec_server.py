"""Tests for the execution server HTTP layer (server/main.py) — token auth."""

import http.client
import http.server
import json
import os
import sys
import threading
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import main as server_main


class TestExecTokenAuth(unittest.TestCase):
    TOKEN = "test-secret-token"

    @classmethod
    def setUpClass(cls):
        cls._server = http.server.HTTPServer(
            ("127.0.0.1", 0), server_main.ExecutionHandler
        )
        cls._port = cls._server.server_address[1]
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def setUp(self):
        self._env = unittest.mock.patch.dict(os.environ, {"EXEC_TOKEN": self.TOKEN})
        self._env.start()
        # Never actually execute anything
        self._exec = unittest.mock.patch.object(
            server_main.handler, "execute",
            return_value={"stdout": "ok", "stderr": "", "exit_code": 0},
        )
        self._exec.start()

    def tearDown(self):
        self._exec.stop()
        self._env.stop()

    def _post(self, headers: dict) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self._port)
        body = json.dumps({"tool": "git", "args": ["status"]})
        conn.request("POST", "/execute", body=body,
                     headers={"Content-Type": "application/json", **headers})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        conn.close()
        return resp.status, data

    def test_valid_token_executes(self):
        status, data = self._post({"X-Exec-Token": self.TOKEN})
        self.assertEqual(status, 200)
        self.assertEqual(data["exit_code"], 0)

    def test_missing_token_rejected(self):
        status, data = self._post({})
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", data["error"])

    def test_wrong_token_rejected(self):
        status, _ = self._post({"X-Exec-Token": "wrong"})
        self.assertEqual(status, 401)

    def test_no_token_configured_rejects_everything(self):
        # Fail closed: server without EXEC_TOKEN must not execute anything
        with unittest.mock.patch.dict(os.environ, {"EXEC_TOKEN": ""}):
            status, _ = self._post({"X-Exec-Token": ""})
        self.assertEqual(status, 401)

    def test_health_needs_no_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self._port)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()


class TestBackendEnvValidation(unittest.TestCase):
    def test_allow_all_needs_nothing(self):
        with unittest.mock.patch.dict(os.environ, {"AUTH_BACKEND": "allow_all"}):
            backend, missing = server_main._missing_backend_env()
        self.assertEqual((backend, missing), ("allow_all", []))

    def test_default_backend_is_allow_all(self):
        env = {k: "" for k in ("AUTH_BACKEND",)}
        with unittest.mock.patch.dict(os.environ, env):
            backend, missing = server_main._missing_backend_env()
        self.assertEqual((backend, missing), ("allow_all", []))

    def test_outhora_reports_missing_secrets(self):
        env = {
            "AUTH_BACKEND": "outhora",
            "OUTHORA_AGENT_ID": "agent_x",
            "OUTHORA_AGENT_SECRET": "",
            "OUTHORA_DEPT_ID": "",
        }
        with unittest.mock.patch.dict(os.environ, env):
            _, missing = server_main._missing_backend_env()
        self.assertEqual(missing, ["OUTHORA_AGENT_SECRET", "OUTHORA_DEPT_ID"])

    def test_webhook_requires_url(self):
        env = {"AUTH_BACKEND": "webhook", "AUTH_WEBHOOK_URL": ""}
        with unittest.mock.patch.dict(os.environ, env):
            _, missing = server_main._missing_backend_env()
        self.assertEqual(missing, ["AUTH_WEBHOOK_URL"])

    def test_dotted_path_backend_required_env_is_validated(self):
        """Custom backends selected by dotted path get startup validation too."""
        env = {
            "AUTH_BACKEND": "server.auth_backends.webhook.WebhookBackend",
            "AUTH_WEBHOOK_URL": "",
        }
        with unittest.mock.patch.dict(os.environ, env):
            _, missing = server_main._missing_backend_env()
        self.assertEqual(missing, ["AUTH_WEBHOOK_URL"])

    def test_unknown_backend_raises_at_startup(self):
        with unittest.mock.patch.dict(os.environ, {"AUTH_BACKEND": "bogus"}):
            with self.assertRaises(ValueError):
                server_main._missing_backend_env()


if __name__ == "__main__":
    unittest.main()
