"""Tests for host-side request handling (server/handler.py)."""

import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import handler


class TestRepoPathMapping(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self._tmpdir, "sub"))
        self._env = unittest.mock.patch.dict(
            os.environ, {"WORKSPACE_DIR": self._tmpdir, "WORKSPACE_MAP": ""}
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_workspace_root_maps_to_workspace_dir(self):
        host, err = handler._map_repo_to_host("/workspace")
        self.assertIsNone(err)
        self.assertEqual(host, self._tmpdir)

    def test_workspace_subpath_maps(self):
        host, err = handler._map_repo_to_host("/workspace/sub")
        self.assertIsNone(err)
        self.assertEqual(host, os.path.join(self._tmpdir, "sub"))

    def test_empty_repo_means_no_workdir(self):
        host, err = handler._map_repo_to_host("")
        self.assertIsNone(err)
        self.assertIsNone(host)

    def test_unmapped_path_is_an_error(self):
        host, err = handler._map_repo_to_host("/etc")
        self.assertIsNone(host)
        self.assertIn("outside the mapped workspace", err["stderr"])

    def test_nonexistent_target_is_an_error(self):
        host, err = handler._map_repo_to_host("/workspace/missing")
        self.assertIsNone(host)
        self.assertIn("does not exist on the host", err["stderr"])

    def test_workspace_map_longest_prefix_wins(self):
        extra = tempfile.mkdtemp()
        with unittest.mock.patch.dict(
            os.environ, {"WORKSPACE_MAP": f"/workspace/sub={extra}"}
        ):
            host, err = handler._map_repo_to_host("/workspace/sub")
        self.assertIsNone(err)
        self.assertEqual(host, extra)

    def test_parse_override_volumes(self):
        override = os.path.join(self._tmpdir, "override.yml")
        with open(override, "w") as f:
            f.write(
                "services:\n"
                "  claude:\n"
                "    volumes:\n"
                "      - /Users/me/project:/workspace/project:rw\n"
                "      - /Users/me/docs:/workspace/docs:ro\n"
                "      - /Users/me/plain:/workspace/plain\n"
                "      - not-a-volume-line\n"
            )
        mapping = handler._parse_override_volumes(override)
        self.assertEqual(mapping, {
            "/workspace/project": "/Users/me/project",
            "/workspace/docs": "/Users/me/docs",
            "/workspace/plain": "/Users/me/plain",
        })

    def test_parse_override_volumes_missing_file(self):
        self.assertEqual(handler._parse_override_volumes("/nonexistent.yml"), {})

    def test_prefix_does_not_match_lookalike_paths(self):
        # /workspaceevil must not match the /workspace prefix
        host, err = handler._map_repo_to_host("/workspaceevil")
        self.assertIsNone(host)
        self.assertIn("outside the mapped workspace", err["stderr"])


class _StubBackend:
    def __init__(self, decision):
        self._decision = decision

    def authorize(self, **kwargs):
        return self._decision

    def execution_env(self, tool, decision):
        return dict(os.environ)


class TestDecisionHandling(unittest.TestCase):
    """Pending vs denied decisions must be distinguishable in the container."""

    def _execute(self, decision, request=None):
        with unittest.mock.patch.object(
            handler, "get_auth_backend", return_value=_StubBackend(decision)
        ):
            return handler.execute(request or {"tool": "git", "args": ["status"]})

    def test_pending_reports_approval_waiting(self):
        from server.auth_backends.base import AuthDecision
        result = self._execute(AuthDecision(
            status="pending", reason="still awaiting approval", request_id="req-42"
        ))
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("PENDING APPROVAL", result["stderr"])
        self.assertIn("req-42", result["stderr"])
        self.assertNotIn("DENIED", result["stderr"])

    def test_denied_reports_reason(self):
        from server.auth_backends.base import AuthDecision
        result = self._execute(AuthDecision(status="denied", reason="not allowed"))
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("DENIED: not allowed", result["stderr"])

    def test_approved_executes(self):
        from server.auth_backends.base import AuthDecision
        result = self._execute(
            AuthDecision(status="approved"),
            {"tool": "echo", "args": ["hello"]},
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), "hello")


class TestExecutionTimeout(unittest.TestCase):
    def test_hung_command_is_killed(self):
        from server.auth_backends.base import AuthDecision
        with unittest.mock.patch.object(
            handler, "get_auth_backend",
            return_value=_StubBackend(AuthDecision(status="approved")),
        ), unittest.mock.patch.dict(os.environ, {"EXEC_TIMEOUT": "1"}):
            result = handler.execute({"tool": "sleep", "args": ["10"]})
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("timed out after 1s", result["stderr"])

    def test_invalid_timeout_falls_back_to_default(self):
        with unittest.mock.patch.dict(os.environ, {"EXEC_TIMEOUT": "banana"}):
            self.assertEqual(handler._exec_timeout(), handler._DEFAULT_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
