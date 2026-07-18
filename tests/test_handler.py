"""Tests for host-side request handling (server/handler.py) — path mapping."""

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


if __name__ == "__main__":
    unittest.main()
