"""Tests for the host-side Outhora client — aligned with ACP contract."""

import json
import os
import sys
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.outhora.client import ActionDenied, ApprovalRequired, OuthoraClient
from server.outhora.credentials import build_execution_env
from server.outhora.models import ActionResponse, ActionStatus, CredentialResponse


class TestModels(unittest.TestCase):
    def test_action_response_approved(self):
        resp = ActionResponse.from_dict({"status": "approved", "approval_token": "tok-abc123"})
        self.assertEqual(resp.status, ActionStatus.APPROVED)
        self.assertEqual(resp.approval_token, "tok-abc123")

    def test_action_response_rejected(self):
        resp = ActionResponse.from_dict({"status": "rejected", "reason": "destructive action blocked"})
        self.assertEqual(resp.status, ActionStatus.REJECTED)
        self.assertEqual(resp.reason, "destructive action blocked")

    def test_action_response_pending(self):
        resp = ActionResponse.from_dict({"status": "pending", "request_id": "req-xyz", "approver": "manager@example.com"})
        self.assertEqual(resp.status, ActionStatus.PENDING)
        self.assertEqual(resp.request_id, "req-xyz")
        self.assertEqual(resp.approver, "manager@example.com")

    def test_credential_response(self):
        creds = CredentialResponse.from_dict({"access_key": "AKIA...", "secret_key": "secret", "session_token": "token", "expires_at": "2026-01-01T00:00:00Z", "gh_token": "ghp_xxx"})
        self.assertEqual(creds.access_key, "AKIA...")
        self.assertEqual(creds.extra["gh_token"], "ghp_xxx")


class TestCredentialInjection(unittest.TestCase):
    def test_build_aws_env(self):
        creds = CredentialResponse(access_key="AKIA_TEST", secret_key="SECRET_TEST", session_token="TOKEN_TEST", expires_at="2026-01-01T00:00:00Z")
        env = build_execution_env("aws", creds)
        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "AKIA_TEST")
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "SECRET_TEST")
        self.assertEqual(env["AWS_SESSION_TOKEN"], "TOKEN_TEST")

    def test_build_gh_env(self):
        creds = CredentialResponse(access_key="ghp_token", secret_key="", session_token="", expires_at="2026-01-01T00:00:00Z", extra={"gh_token": "ghp_real_token"})
        env = build_execution_env("gh", creds)
        self.assertEqual(env["GH_TOKEN"], "ghp_real_token")

    def test_build_psql_env(self):
        creds = CredentialResponse(access_key="", secret_key="", session_token="", expires_at="2026-01-01T00:00:00Z", extra={"pgpassword": "dbpass", "pguser": "dbuser", "pghost": "db.example.com", "pgdatabase": "mydb"})
        env = build_execution_env("psql", creds)
        self.assertEqual(env["PGPASSWORD"], "dbpass")
        self.assertEqual(env["PGUSER"], "dbuser")


class MockACPHandler(BaseHTTPRequestHandler):
    """Mock ACP server matching the acp_submit_action / acp_get_action_status contract."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/v1/health":
            self._respond(200, {"status": "ok"})
        elif self.path.startswith("/v1/actions/"):
            request_id = self.path.split("/")[-1]
            self._respond(200, {"status": "approved", "request_id": request_id, "approval_token": f"tok-{request_id}"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/v1/actions":
            command = body.get("context", {}).get("command", "")
            if "destroy" in command or "delete" in command:
                self._respond(200, {"status": "rejected", "reason": "destructive action blocked"})
            elif "apply" in command or "merge" in command:
                self._respond(200, {"status": "pending", "request_id": "req-test-123", "approver": "manager@example.com"})
            else:
                self._respond(200, {"status": "approved", "approval_token": "tok-test-456"})
        elif self.path == "/v1/credentials":
            self._respond(200, {"access_key": "AKIA_TEMP", "secret_key": "SECRET_TEMP", "session_token": "TOKEN_TEMP", "expires_at": "2026-01-01T00:00:00Z"})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class TestClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockACPHandler)
        cls.port = cls.server.server_address[1]
        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _client(self):
        return OuthoraClient(api_url=self.api_url, api_key="test-key", dept_id="test-dept", user_id="test-user", session_id="test-session")

    def test_health_check(self):
        self.assertTrue(self._client().health_check())

    def test_submit_approved(self):
        resp = self._client().submit_action("git_push", {"command": "git push origin main"})
        self.assertEqual(resp.status, ActionStatus.APPROVED)
        self.assertEqual(resp.approval_token, "tok-test-456")

    def test_submit_rejected(self):
        resp = self._client().submit_action("terraform_destroy", {"command": "terraform destroy"})
        self.assertEqual(resp.status, ActionStatus.REJECTED)
        self.assertIn("destructive", resp.reason)

    def test_submit_pending(self):
        resp = self._client().submit_action("terraform_apply", {"command": "terraform apply"})
        self.assertEqual(resp.status, ActionStatus.PENDING)
        self.assertEqual(resp.request_id, "req-test-123")
        self.assertEqual(resp.approver, "manager@example.com")

    def test_get_action_status(self):
        resp = self._client().get_action_status("req-test-123")
        self.assertEqual(resp.status, ActionStatus.APPROVED)
        self.assertEqual(resp.approval_token, "tok-req-test-123")

    def test_get_temporary_credentials(self):
        creds = self._client().get_temporary_credentials("aws", "tok-test-456")
        self.assertEqual(creds.access_key, "AKIA_TEMP")
        self.assertEqual(creds.session_token, "TOKEN_TEMP")

    def test_execute_authorized_approved(self):
        resp = self._client().execute_authorized("git_push", {"command": "git push origin main"})
        self.assertEqual(resp.status, ActionStatus.APPROVED)
        self.assertNotEqual(resp.approval_token, "")

    def test_execute_authorized_rejected(self):
        with self.assertRaises(ActionDenied):
            self._client().execute_authorized("terraform_destroy", {"command": "terraform destroy"})

    def test_execute_authorized_pending(self):
        with self.assertRaises(ApprovalRequired) as ctx:
            self._client().execute_authorized("terraform_apply", {"command": "terraform apply"})
        self.assertEqual(ctx.exception.request_id, "req-test-123")


if __name__ == "__main__":
    unittest.main()
