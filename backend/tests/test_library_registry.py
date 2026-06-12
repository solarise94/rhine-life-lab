"""Tests for capability install/register safety and performance.

Covers:
- ID validation and path containment for skill/MCP install.
- MCP register does not leave directories behind on invalid input.
- Single-entry registry mutation instead of synchronous whole-library refresh.
- MCP local-path install structural validation.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.config import Settings, get_settings
from app.services.app_config_service import AppConfigService
from app.services.library_registry_service import LibraryRegistryService
from app.services.project_service import ProjectService


class TestLibraryRegistryInstallAndRegister(unittest.TestCase):
    def setUp(self):
        self._original_data_root = get_settings().data_root
        self.data_root = Path(tempfile.mkdtemp())
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()
        get_settings().data_root = self._original_data_root

    def _service(self):
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            project_service = ProjectService()
        app_config_service = AppConfigService(settings=self.settings)
        return LibraryRegistryService(
            project_service=project_service,
            app_config_service=app_config_service,
            settings=self.settings,
        )

    def _make_skill_dir(self, parent: Path, name: str, frontmatter_name: str | None = None) -> Path:
        skill_dir = parent / name
        skill_dir.mkdir(parents=True)
        content = "---\n"
        if frontmatter_name:
            content += f'name: "{frontmatter_name}"\n'
        content += "---\n\n# Test skill\n"
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return skill_dir

    def _make_mcp_dir(self, parent: Path, name: str, manifest: str = "server.json") -> Path:
        mcp_dir = parent / name
        mcp_dir.mkdir(parents=True)
        (mcp_dir / manifest).write_text(
            '{"name": "Test MCP", "type": "http", "url": "http://127.0.0.1:1/sse"}',
            encoding="utf-8",
        )
        return mcp_dir

    def test_install_skill_rejects_traversal_target_id(self):
        service = self._service()
        skill_dir = self._make_skill_dir(self.data_root, "good-skill")
        with self.assertRaises(ValueError) as ctx:
            service.install_skill_from_directory(skill_dir, target_id="../evil")
        self.assertIn("illegal", str(ctx.exception).lower())

    def test_install_skill_uses_explicit_target_id_and_frontmatter_name(self):
        service = self._service()
        skill_dir = self._make_skill_dir(
            self.data_root, "source-name", frontmatter_name="Pretty Name"
        )
        result = service.install_skill_from_directory(skill_dir, target_id="my-id")
        self.assertEqual(result["installed_id"], "my-id")
        self.assertEqual(result["installed_name"], "Pretty Name")

        entries = service.list_entries("skill")["items"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "my-id")
        self.assertEqual(entries[0]["name"], "Pretty Name")

    def test_install_skill_does_not_force_refresh_whole_library(self):
        service = self._service()
        skill_dir = self._make_skill_dir(self.data_root, "source-name")
        with patch.object(service, "refresh_entries") as mock_refresh:
            service.install_skill_from_directory(skill_dir, target_id="my-id")
        mock_refresh.assert_not_called()

    def test_register_mcp_rejects_traversal_server_id(self):
        service = self._service()
        with self.assertRaises(ValueError) as ctx:
            service.register_mcp_server(
                server_id="../../evil",
                name="Evil",
                transport="http",
                url="http://127.0.0.1:1/sse",
            )
        self.assertIn("illegal", str(ctx.exception).lower())

    def test_register_mcp_rejects_invalid_server_id_characters(self):
        service = self._service()
        for bad_id in ["foo/bar", "foo..", ".foo", "foo bar"]:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    service.register_mcp_server(
                        server_id=bad_id,
                        name="Bad",
                        transport="http",
                        url="http://127.0.0.1:1/sse",
                    )

    def test_register_mcp_invalid_stdio_does_not_leave_directory(self):
        service = self._service()
        with self.assertRaises(ValueError):
            service.register_mcp_server(
                server_id="my-mcp",
                name="My MCP",
                transport="stdio",
                command="",
            )
        cap_root = self.data_root / "_system" / "capabilities" / "mcp"
        self.assertFalse((cap_root / "my-mcp").exists())

    def test_register_mcp_persists_name_and_updates_registry(self):
        service = self._service()
        result = service.register_mcp_server(
            server_id="my-mcp",
            name="My Display Name",
            transport="http",
            url="http://127.0.0.1:1/sse",
        )
        self.assertEqual(result["installed_id"], "my-mcp")
        self.assertEqual(result["installed_name"], "My Display Name")

        entries = service.list_entries("mcp")["items"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "my-mcp")
        self.assertEqual(entries[0]["name"], "My Display Name")

        server_json = self.data_root / "_system" / "capabilities" / "mcp" / "my-mcp" / "server.json"
        self.assertTrue(server_json.exists())

    def test_register_mcp_does_not_force_refresh_whole_library(self):
        service = self._service()
        with patch.object(service, "refresh_entries") as mock_refresh:
            service.register_mcp_server(
                server_id="my-mcp",
                name="My MCP",
                transport="http",
                url="http://127.0.0.1:1/sse",
            )
        mock_refresh.assert_not_called()

    def test_install_mcp_from_directory_rejects_missing_manifest(self):
        service = self._service()
        source = self.data_root / "empty-mcp"
        source.mkdir()
        with self.assertRaises(ValueError) as ctx:
            service.install_mcp_from_directory(source)
        self.assertIn("server.json", str(ctx.exception))

    def test_install_mcp_from_directory_installs_with_explicit_id(self):
        service = self._service()
        source = self._make_mcp_dir(self.data_root, "source-mcp")
        result = service.install_mcp_from_directory(source, target_id="installed-mcp")
        self.assertEqual(result["installed_id"], "installed-mcp")

        entries = service.list_entries("mcp")["items"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "installed-mcp")

    def test_install_mcp_from_directory_mcp_json_is_scanned_and_resolvable(self):
        service = self._service()
        source = self.data_root / "mcp-json-source"
        source.mkdir()
        (source / "mcp.json").write_text(
            '{"name": "MCP JSON Server", "type": "http", "url": "http://127.0.0.1:2/sse"}',
            encoding="utf-8",
        )
        result = service.install_mcp_from_directory(source, target_id="mcp-json")
        self.assertEqual(result["installed_name"], "MCP JSON Server")

        # A later full refresh must still discover the entry.
        service.refresh_entries("mcp", force=True)
        entry = service.get_entry("mcp", "mcp-json")["item"]
        self.assertEqual(entry["name"], "MCP JSON Server")

        # resolve_mcp_bindings must be able to render its config.
        with patch.object(service.project_service, "get_project_snapshot", return_value={"python_runtimes": []}):
            bindings = service.resolve_mcp_bindings("test-project", ["mcp-json"])
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["config"]["mcpServers"]["mcp-json"]["url"], "http://127.0.0.1:2/sse")

    def test_register_mcp_atomic_keeps_old_on_write_failure(self):
        service = self._service()
        service.register_mcp_server(
            server_id="my-mcp",
            name="Old",
            transport="http",
            url="http://127.0.0.1:1/sse",
        )
        old_server_json = self.data_root / "_system" / "capabilities" / "mcp" / "my-mcp" / "server.json"
        self.assertTrue(old_server_json.exists())

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                service.register_mcp_server(
                    server_id="my-mcp",
                    name="New",
                    transport="http",
                    url="http://127.0.0.1:2/sse",
                    overwrite=True,
                )

        # Old installation must still be intact.
        self.assertTrue(old_server_json.exists())
        self.assertIn("Old", old_server_json.read_text(encoding="utf-8"))

    def test_install_skill_atomic_keeps_old_on_copy_failure(self):
        service = self._service()
        skill_dir = self._make_skill_dir(self.data_root, "source")
        service.install_skill_from_directory(skill_dir, target_id="my-skill")
        old_skill_md = self.data_root / "_system" / "capabilities" / "skills" / "my-skill" / "SKILL.md"
        self.assertTrue(old_skill_md.exists())

        new_skill_dir = self._make_skill_dir(self.data_root, "new-source")
        with patch.object(shutil, "copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                service.install_skill_from_directory(new_skill_dir, target_id="my-skill", overwrite=True)

        self.assertTrue(old_skill_md.exists())


if __name__ == "__main__":
    unittest.main()
