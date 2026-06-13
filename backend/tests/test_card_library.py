"""Tests for the Card Library (牌库) system.

Covers:
- Blueprint CRUD (save from card, import, update, delete, get, list, search).
- Desensitization (path stripping, asset_id stripping, secret blocking).
- Cover image validation (magic bytes, size, SVG rejection).
- Instantiation validation:
  - Required inputs must be bound.
  - Required parameters must be provided.
  - Skill/MCP availability check.
  - Runtime requirements enforcement.
  - Project existence check.
  - Project lock usage.
- BlueprintOutputSchema.artifact_class uses ArtifactClass literal.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.models.card_blueprint import (
    BlueprintOutputSchema,
    BlueprintRuntimeRequirement,
    BlueprintRuntimeRequirements,
    CardBlueprint,
    CardBlueprintDraft,
    CardBlueprintIndexEntry,
    InstantiateRequest,
)
from app.models.cards import Card, CardAssetRef
from app.models.executor import ExecutorContext, RuntimeBindings
from app.models.graph import Asset
from app.models.output_contracts import CardOutputSpec
from app.services.card_library_service import CardLibraryService
from app.services.project_service import ProjectService


class _Base(unittest.TestCase):
    def setUp(self):
        self.data_root = Path(tempfile.mkdtemp())
        self.settings = Settings(data_root=self.data_root)
        get_settings.cache_clear()

    def tearDown(self):
        get_settings.cache_clear()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def _project_service(self):
        with patch("app.services.project_service.get_settings", return_value=self.settings):
            return ProjectService()

    def _service(self, project_service=None):
        ps = project_service or self._project_service()
        return CardLibraryService(ps, settings=self.settings)

    def _add_asset(
        self,
        project_id: str,
        asset_id: str,
        path: str = "results/data.csv",
        status: str = "valid",
    ) -> None:
        ps = self._project_service()
        store = ps.graph_store(project_id)
        assets = store.load_assets()
        assets.append(
            Asset(
                asset_id=asset_id,
                asset_type="data",
                title="Test Asset",
                status=status,
                path=path,
                summary="test asset",
            )
        )
        store.save_assets(assets)

    def _create_project(self, project_id: str = "test-project"):
        ps = self._project_service()
        ps.create_project(
            project_id=project_id,
            name="Test Project",
            current_goal="testing",
        )
        return ps


# ======================================================================
# Blueprint CRUD
# ======================================================================

class TestBlueprintCRUD(_Base):
    def test_save_from_card(self):
        ps = self._create_project("proj1")
        svc = self._service(ps)

        # Create a card in the project
        store = ps.graph_store("proj1")
        card = Card(
            card_id="card-001",
            card_type="module",
            title="Test Card",
            status="proposed",
            summary="/home/user/data analysis",
            inputs=[CardAssetRef(label="input data", asset_id="sha256:" + "a" * 64)],
            outputs=[
                CardOutputSpec(role="result", label="Result", artifact_class="figure"),
            ],
        )
        store.save_cards([card])

        result = svc.save_from_card("proj1", "card-001")
        self.assertTrue(result.blueprint_id)
        self.assertIn("未进行 AI 泛化检查", result.warnings[0])

        # Verify blueprint saved
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertEqual(bp["title"], "Test Card")
        # Desensitization: /home path stripped
        self.assertNotIn("/home/user", bp["summary"])

    def test_save_from_card_infers_formats(self):
        ps = self._create_project("proj1")
        svc = self._service(ps)
        store = ps.graph_store("proj1")
        asset_id = "sha256:" + "a" * 64
        store.save_assets([
            Asset(
                asset_id=asset_id,
                asset_type="data",
                title="Input",
                status="valid",
                path="results/counts.h5ad",
                summary="input",
            ),
        ])
        card = Card(
            card_id="card-002",
            card_type="module",
            title="Format Inference",
            status="proposed",
            summary="test",
            inputs=[CardAssetRef(label="input data", asset_id=asset_id)],
            outputs=[CardOutputSpec(role="result", label="Result", artifact_class="figure")],
        )
        store.save_cards([card])

        result = svc.save_from_card("proj1", "card-002")
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertEqual(bp["inputs_schema"][0]["accepted_formats"], ["h5ad"])

    def test_import_blueprint(self):
        svc = self._service()
        bp_data = {
            "blueprint_id": "will-be-regenerated",
            "title": "Imported BP",
            "summary": "A test blueprint",
            "skills": ["skill_a"],
            "mcp_servers": ["mcp_b"],
            "inputs_schema": [{"slot": "data", "label": "Input Data", "required": True}],
            "outputs_schema": [
                {"role": "plot", "label": "Plot", "artifact_class": "figure"},
            ],
            "parameters": [
                {"name": "threshold", "type": "float", "required": True},
            ],
            "runtime_requirements": {
                "python": {"env_hint": "scanpy", "packages": ["scanpy"]},
                "r": "__system__",
            },
        }
        result = svc.save_from_import(bp_data)
        self.assertTrue(result.blueprint_id)

        bp = svc.get_blueprint(result.blueprint_id)
        self.assertEqual(bp["title"], "Imported BP")
        self.assertEqual(bp["parameters"][0]["name"], "threshold")

    def test_list_blueprints(self):
        svc = self._service()
        svc.save_from_import({"blueprint_id": "x", "title": "BP 1"})
        svc.save_from_import({"blueprint_id": "y", "title": "BP 2"})
        entries = svc.list_blueprints()
        self.assertEqual(len(entries), 2)

    def test_search_blueprints(self):
        svc = self._service()
        svc.save_from_import({"blueprint_id": "x", "title": "RNA-seq Analysis", "domain": "genomics"})
        svc.save_from_import({"blueprint_id": "y", "title": "Cell Culture", "domain": "biology"})
        results = svc.search_blueprints(query="rna")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "RNA-seq Analysis")

    def test_update_blueprint(self):
        svc = self._service()
        result = svc.save_from_import({"blueprint_id": "x", "title": "Old Title"})
        from app.models.card_blueprint import UpdateBlueprintRequest
        updated = svc.update_blueprint(result.blueprint_id, UpdateBlueprintRequest(title="New Title"))
        self.assertEqual(updated["title"], "New Title")

    def test_delete_blueprint(self):
        svc = self._service()
        result = svc.save_from_import({"blueprint_id": "x", "title": "BP"})
        svc.delete_blueprint(result.blueprint_id)
        with self.assertRaises(ValueError):
            svc.get_blueprint(result.blueprint_id)

    def test_export_blueprint(self):
        svc = self._service()
        result = svc.save_from_import({"blueprint_id": "x", "title": "BP", "summary": "test"})
        exported = svc.export_blueprint(result.blueprint_id)
        self.assertEqual(exported["title"], "BP")


# ======================================================================
# Desensitization
# ======================================================================

class TestDesensitization(_Base):
    def test_home_path_stripped(self):
        """Desensitization happens in save_from_card, not save_from_import."""
        ps = self._create_project("proj1")
        svc = self._service(ps)
        store = ps.graph_store("proj1")
        card = Card(
            card_id="c1", card_type="module", title="Card", status="proposed",
            summary="path /home/alice/data removed",
        )
        store.save_cards([card])
        result = svc.save_from_card("proj1", "c1")
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertNotIn("/home/alice", bp["summary"])

    def test_users_path_stripped(self):
        ps = self._create_project("proj1")
        svc = self._service(ps)
        store = ps.graph_store("proj1")
        card = Card(
            card_id="c1", card_type="module", title="Card", status="proposed",
            summary="see /Users/bob/Desktop for details",
        )
        store.save_cards([card])
        result = svc.save_from_card("proj1", "c1")
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertNotIn("/Users/bob", bp["summary"])

    def test_windows_path_stripped(self):
        ps = self._create_project("proj1")
        svc = self._service(ps)
        store = ps.graph_store("proj1")
        card = Card(
            card_id="c1", card_type="module", title="Card", status="proposed",
            summary="C:\\Users\\carol\\data here",
        )
        store.save_cards([card])
        result = svc.save_from_card("proj1", "c1")
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertNotIn("C:\\Users\\carol", bp["summary"])

    def test_asset_id_stripped_from_card(self):
        ps = self._create_project("proj1")
        svc = self._service(ps)
        store = ps.graph_store("proj1")
        card = Card(
            card_id="c1",
            card_type="module",
            title="Card",
            status="proposed",
            summary="data sha256:" + "f" * 64 + " processed",
        )
        store.save_cards([card])
        result = svc.save_from_card("proj1", "c1")
        bp = svc.get_blueprint(result.blueprint_id)
        self.assertNotIn("sha256:", bp["summary"])


# ======================================================================
# Cover image validation
# ======================================================================

class TestCoverImage(_Base):
    def _save_blueprint(self, svc):
        result = svc.save_from_import({"blueprint_id": "x", "title": "BP"})
        return result.blueprint_id

    def test_save_png_cover(self):
        svc = self._service()
        bp_id = self._save_blueprint(svc)
        # Minimal valid PNG: 8-byte signature + IHDR chunk
        png_sig = b"\x89PNG\r\n\x1a\n"
        # Minimal IHDR chunk: length(4) + type(4) + data(13) + crc(4)
        ihdr = b"\x00\x00\x00\rIHDR" + b"\x00" * 13 + b"\x00" * 4
        content = png_sig + ihdr
        result = svc.save_cover(bp_id, content, "cover.png")
        self.assertTrue(result["ok"])

    def test_reject_svg_cover(self):
        svc = self._service()
        bp_id = self._save_blueprint(svc)
        with self.assertRaises(ValueError) as ctx:
            svc.save_cover(bp_id, b"<svg></svg>", "cover.svg")
        self.assertIn("SVG", str(ctx.exception))

    def test_reject_too_large(self):
        svc = self._service()
        bp_id = self._save_blueprint(svc)
        big = b"\x89PNG" + b"\x00" * (2 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError) as ctx:
            svc.save_cover(bp_id, big, "cover.png")
        self.assertIn("too large", str(ctx.exception).lower())

    def test_reject_invalid_magic_bytes(self):
        svc = self._service()
        bp_id = self._save_blueprint(svc)
        with self.assertRaises(ValueError) as ctx:
            svc.save_cover(bp_id, b"not-an-image", "cover.png")
        self.assertIn("Invalid image format", str(ctx.exception))


# ======================================================================
# Instantiation validation (Findings #1, #3, #4)
# ======================================================================

class TestInstantiationValidation(_Base):
    def _setup_project_with_blueprint(self, bp_overrides=None):
        """Create a project and import a blueprint. Returns (service, project_service, bp_id)."""
        ps = self._create_project("proj1")
        svc = self._service(ps)
        bp_data = {
            "blueprint_id": "test-bp",
            "title": "Test BP",
            "inputs_schema": [
                {"slot": "data", "label": "Input Data", "required": True},
            ],
            "outputs_schema": [
                {"role": "result", "label": "Result", "artifact_class": "table"},
            ],
            "parameters": [
                {"name": "threshold", "type": "float", "required": True},
            ],
            "skills": [],
            "mcp_servers": [],
            "runtime_requirements": {
                "python": {"env_hint": "", "packages": []},
                "r": "__system__",
            },
        }
        if bp_overrides:
            bp_data.update(bp_overrides)
        result = svc.save_from_import(bp_data)
        return svc, ps, result.blueprint_id

    def test_missing_required_parameter(self):
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "asset-1"},
            parameter_values={},  # Missing 'threshold'
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("threshold" in b for b in result.blockers))

    def test_missing_required_input(self):
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={},  # 'data' not bound
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("data" in b for b in result.blockers))

    def test_project_not_found(self):
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "nonexistent-project", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("not found" in b.lower() for b in result.blockers))

    def test_runtime_requirement_enforced(self):
        bp_overrides = {
            "runtime_requirements": {
                "python": {"env_hint": "scanpy", "packages": ["scanpy"]},
                "r": "__system__",
            },
        }
        svc, _, bp_id = self._setup_project_with_blueprint(bp_overrides)
        self._add_asset("proj1", "a1")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
            python_runtime=None,  # No runtime selected
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("python runtime" in b.lower() for b in result.blockers))

    def test_successful_instantiation(self):
        svc, ps, bp_id = self._setup_project_with_blueprint()
        self._add_asset("proj1", "asset-001", path="results/data.csv", status="valid")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "asset-001"},
            parameter_values={"threshold": "0.05"},
        ))
        self.assertNotEqual(result.card_id, "")
        self.assertEqual(result.blockers, [])

        # Verify card was added to project
        cards = ps.graph_store("proj1").load_cards()
        self.assertTrue(any(c.card_id == result.card_id for c in cards))

    def test_parameter_with_path_blocked(self):
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "/home/user/secret"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("file path" in b for b in result.blockers))

    def test_parameter_with_secret_blocked(self):
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "my_api_key=ABC123"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("sensitive" in b.lower() for b in result.blockers))

    def test_skill_not_available_blocked(self):
        """Skill referenced by blueprint must exist in the library registry."""
        svc, ps, bp_id = self._setup_project_with_blueprint({
            "skills": ["nonexistent_skill"],
        })
        self._add_asset("proj1", "a1")
        # Mock the library registry service to report skill not found
        mock_registry = MagicMock()
        mock_registry.get_entry.side_effect = ValueError("skill library item not found: nonexistent_skill")
        svc.library_registry_service = mock_registry

        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("nonexistent_skill" in b for b in result.blockers))

    def test_mcp_not_available_blocked(self):
        """MCP server referenced by blueprint must exist in the library registry."""
        svc, ps, bp_id = self._setup_project_with_blueprint({
            "mcp_servers": ["nonexistent_mcp"],
        })
        self._add_asset("proj1", "a1")
        mock_registry = MagicMock()
        mock_registry.get_entry.side_effect = ValueError("mcp library item not found: nonexistent_mcp")
        svc.library_registry_service = mock_registry

        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("nonexistent_mcp" in b for b in result.blockers))

    def test_unknown_input_asset_blocked(self):
        """Binding a non-existent input asset must block instantiation."""
        svc, _, bp_id = self._setup_project_with_blueprint()
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "does-not-exist"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("does-not-exist" in b for b in result.blockers))

    def test_input_asset_unusable_status_blocked(self):
        """Binding a rejected/missing/archived asset must block instantiation."""
        svc, _, bp_id = self._setup_project_with_blueprint()
        self._add_asset("proj1", "rejected-asset", status="rejected")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "rejected-asset"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("rejected-asset" in b for b in result.blockers))

    def test_input_asset_format_mismatch_blocked(self):
        """Binding an asset whose format is not in accepted_formats must block."""
        svc, _, bp_id = self._setup_project_with_blueprint({
            "inputs_schema": [
                {"slot": "data", "label": "Input Data", "required": True, "accepted_formats": ["tsv"]},
            ],
        })
        self._add_asset("proj1", "csv-asset", path="results/data.csv")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "csv-asset"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("csv" in b.lower() and "tsv" in b.lower() for b in result.blockers))

    def test_extensionless_asset_blocked_when_formats_required(self):
        """An asset with no extension must be blocked if accepted_formats is specified."""
        svc, _, bp_id = self._setup_project_with_blueprint({
            "inputs_schema": [
                {"slot": "data", "label": "Input Data", "required": True, "accepted_formats": ["csv"]},
            ],
        })
        self._add_asset("proj1", "extless-asset", path="results/data")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "extless-asset"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("no inferable format" in b.lower() for b in result.blockers))

    def test_disabled_skill_blocked(self):
        """A skill that exists but is disabled must block instantiation."""
        svc, _, bp_id = self._setup_project_with_blueprint({
            "skills": ["disabled_skill"],
        })
        self._add_asset("proj1", "a1")
        mock_registry = MagicMock()
        mock_registry.get_entry.return_value = {"item": {"enabled": False}}
        svc.library_registry_service = mock_registry

        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("disabled" in b.lower() for b in result.blockers))

    def test_disabled_mcp_blocked(self):
        """An MCP server that exists but is disabled must block instantiation."""
        svc, _, bp_id = self._setup_project_with_blueprint({
            "mcp_servers": ["disabled_mcp"],
        })
        self._add_asset("proj1", "a1")
        mock_registry = MagicMock()
        mock_registry.get_entry.return_value = {"item": {"enabled": False}}
        svc.library_registry_service = mock_registry

        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("disabled" in b.lower() for b in result.blockers))

    def test_env_hint_does_not_block_without_packages(self):
        """env_hint alone is a soft hint and must not require a runtime."""
        svc, ps, bp_id = self._setup_project_with_blueprint({
            "runtime_requirements": {
                "python": {"env_hint": "scanpy", "packages": []},
                "r": "__system__",
            },
        })
        self._add_asset("proj1", "a1")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
            python_runtime=None,
        ))
        self.assertNotEqual(result.card_id, "")
        self.assertEqual(result.blockers, [])

    def test_invalid_runtime_with_packages_blocked(self):
        """A selected runtime that cannot be resolved must block instantiation."""
        svc, _, bp_id = self._setup_project_with_blueprint({
            "runtime_requirements": {
                "python": {"env_hint": "", "packages": ["scanpy"]},
                "r": "__system__",
            },
        })
        self._add_asset("proj1", "a1")
        result = svc.instantiate(bp_id, "proj1", InstantiateRequest(
            input_bindings={"data": "a1"},
            parameter_values={"threshold": "0.5"},
            python_runtime="totally-fake-env",
        ))
        self.assertEqual(result.card_id, "")
        self.assertTrue(any("runtime" in b.lower() for b in result.blockers))


# ======================================================================
# BlueprintOutputSchema.artifact_class validation (Finding #5)
# ======================================================================

class TestArtifactClassValidation(unittest.TestCase):
    def test_legal_artifact_classes(self):
        for cls in ("figure", "table", "document", "model", "archive", "binary"):
            bp = BlueprintOutputSchema(role="r", label="l", artifact_class=cls)
            self.assertEqual(bp.artifact_class, cls)

    def test_illegal_artifact_class_rejected(self):
        with self.assertRaises(ValidationError):
            BlueprintOutputSchema(role="r", label="l", artifact_class="bogus")

    def test_format_normalization(self):
        bp = BlueprintOutputSchema(
            role="r", label="l",
            artifact_class="figure",
            accepted_formats=["SVG", ".png", "PDF"],
            preferred_format="SVG",
        )
        self.assertEqual(bp.accepted_formats, ["svg", "png", "pdf"])
        self.assertEqual(bp.preferred_format, "svg")

    def test_preferred_format_must_match_accepted(self):
        with self.assertRaises(ValidationError):
            BlueprintOutputSchema(
                role="r", label="l",
                artifact_class="figure",
                accepted_formats=["png"],
                preferred_format="svg",
            )


# ======================================================================
# Project draft flow
# ======================================================================

class TestProjectDraftFlow(_Base):
    def _create_card_with_runtime(self, project_id: str, card_id: str, title: str):
        ps = self._project_service()
        store = ps.graph_store(project_id)
        card = Card(
            card_id=card_id,
            card_type="module",
            title=title,
            status="proposed",
            summary="A reusable analysis card",
            outputs=[
                CardOutputSpec(role="result", label="Result", artifact_class="figure"),
            ],
            executor_context=ExecutorContext(
                instruction_blocks=["Run analysis"],
                runtime_bindings=RuntimeBindings(
                    conda_env="scanpy",
                    r_env="__system__",
                ),
            ),
        )
        store.save_cards([card])
        return card

    def test_create_project_draft(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        result = svc.create_project_draft("proj-draft", "card-001")
        self.assertTrue(result.draft_id)
        self.assertIn("规则审查已执行", result.warnings[0])

        draft_path = ps.project_path("proj-draft") / "card-library-drafts" / "drafts" / result.draft_id
        self.assertTrue(draft_path.exists())
        self.assertTrue((draft_path / "blueprint.json").exists())

    def test_get_project_draft(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        draft = svc.get_project_draft("proj-draft", created.draft_id)
        self.assertEqual(draft["draft_id"], created.draft_id)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["blueprint"]["title"], "Clean Card")

    def test_list_project_drafts(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        entries = svc.list_project_drafts("proj-draft")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["draft_id"], created.draft_id)
        self.assertEqual(entries[0]["status"], "draft")
        self.assertEqual(entries[0]["title"], "Clean Card")

    def test_review_project_draft_clean(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        result = svc.review_project_draft("proj-draft", created.draft_id)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["review"]["verdict"], "pass")

    def test_review_project_draft_project_name_in_title(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Test Project Analysis")

        created = svc.create_project_draft("proj-draft", "card-001")
        result = svc.review_project_draft("proj-draft", created.draft_id)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["review"]["verdict"], "fail")

    def test_review_project_draft_absolute_path_in_instructions(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        # Inject an absolute path after extraction scrubbed it
        draft = svc.get_project_draft("proj-draft", created.draft_id)
        draft["blueprint"]["instruction_blocks"] = ["Load data from /home/user/data.csv"]
        from app.services.utils import atomic_write_json
        draft_dir = ps.project_path("proj-draft") / "card-library-drafts" / "drafts" / created.draft_id
        atomic_write_json(draft_dir / "blueprint.json", draft)

        result = svc.review_project_draft("proj-draft", created.draft_id)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["review"]["verdict"], "fail")

    def test_publish_project_draft_approved(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        svc.review_project_draft("proj-draft", created.draft_id)
        result = svc.publish_project_draft("proj-draft", created.draft_id)

        self.assertTrue(result.global_blueprint_id)
        draft = svc.get_project_draft("proj-draft", created.draft_id)
        self.assertEqual(draft["status"], "published")
        self.assertEqual(draft["global_blueprint_id"], result.global_blueprint_id)

        # Verify global blueprint exists
        bp = svc.get_blueprint(result.global_blueprint_id)
        self.assertEqual(bp["title"], "Clean Card")

    def test_publish_project_draft_not_approved(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        with self.assertRaises(ValueError) as ctx:
            svc.publish_project_draft("proj-draft", created.draft_id)
        self.assertIn("approved", str(ctx.exception).lower())

    def test_delete_project_draft(self):
        ps = self._create_project("proj-draft")
        svc = self._service(ps)
        self._create_card_with_runtime("proj-draft", "card-001", "Clean Card")

        created = svc.create_project_draft("proj-draft", "card-001")
        result = svc.delete_project_draft("proj-draft", created.draft_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["draft_id"], created.draft_id)

        with self.assertRaises(ValueError):
            svc.get_project_draft("proj-draft", created.draft_id)


if __name__ == "__main__":
    unittest.main()
