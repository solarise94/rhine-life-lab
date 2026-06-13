from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings, get_settings
from app.models.card_blueprint import (
    BlueprintInputSchema,
    BlueprintOutputSchema,
    BlueprintParameter,
    BlueprintProvenance,
    BlueprintRuntimeRequirement,
    BlueprintRuntimeRequirements,
    CardBlueprint,
    CardBlueprintIndex,
    CardBlueprintIndexEntry,
    InstantiateRequest,
    InstantiateResult,
    SaveResult,
    UpdateBlueprintRequest,
)
from app.models.cards import Card, CardAssetRef
from app.models.executor import ExecutorContext, RuntimeBindings
from app.models.graph import Asset, GraphState
from app.models.output_contracts import CardOutputSpec, normalize_output_format
from app.services.asset_materialization_service import AssetMaterializationService
from app.services.project_service import ProjectService
from app.services.runtime_dependency_resolver_service import RuntimeDependencyResolverService
from app.services.utils import atomic_write_json, read_json, utc_now

try:
    from app.services.library_registry_service import LibraryRegistryService
except ImportError:
    LibraryRegistryService = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Desensitization patterns
# ---------------------------------------------------------------------------

_PATH_PATTERNS = [
    re.compile(r"/home/[^\s,;)\]}\"]+"),
    re.compile(r"/Users/[^\s,;)\]}\"]+"),
    re.compile(r"C:\\[^\s,;)\]}\"]+"),
]

_ASSET_ID_PATTERN = re.compile(r"\bsha256:[a-f0-9]{64}\b", re.I)

_SECRET_PATTERN = re.compile(r"(key|token|password|secret|credential)", re.I)

# Asset statuses that are safe to bind as card inputs.
_VALID_INPUT_STATUSES = {"valid", "candidate"}


def _scrub_text(text: str) -> str:
    """Remove absolute paths and asset IDs from a text string."""
    result = text
    for pat in _PATH_PATTERNS:
        result = pat.sub("", result)
    result = _ASSET_ID_PATTERN.sub("", result)
    return result.strip()


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] if slug else "blueprint"


class CardLibraryService:
    """Manages the system-level card library (blueprint deck)."""

    def __init__(
        self,
        project_service: ProjectService,
        settings: Settings | None = None,
        library_registry_service: LibraryRegistryService | None = None,
        runtime_dependency_resolver_service: RuntimeDependencyResolverService | None = None,
    ) -> None:
        self.project_service = project_service
        self.settings = settings or get_settings()
        self.library_registry_service = library_registry_service
        self.runtime_dependency_resolver_service = (
            runtime_dependency_resolver_service or RuntimeDependencyResolverService()
        )
        self._root = Path(self.settings.data_root) / "_system" / "card-library"
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_dirs(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / "blueprints").mkdir(parents=True, exist_ok=True)

    def _index_path(self) -> Path:
        return self._root / "index.json"

    def _blueprint_dir(self, blueprint_id: str) -> Path:
        return self._root / "blueprints" / blueprint_id

    def _read_index(self) -> dict[str, Any]:
        return read_json(self._index_path(), {"schema_version": "card_library_index.v1", "entries": []})

    def _write_index(self, index_data: dict[str, Any]) -> None:
        atomic_write_json(self._index_path(), index_data)

    def _generate_blueprint_id(self, title: str) -> str:
        slug = _slugify(title)
        existing = {e.get("blueprint_id", "") for e in self._read_index().get("entries", [])}

        for _attempt in range(10):
            suffix = uuid4().hex[:4]
            candidate = f"{slug}-{suffix}"
            if candidate not in existing:
                return candidate
        # Fallback with longer suffix
        return f"{slug}-{uuid4().hex[:8]}"

    def _add_to_index(self, bp: CardBlueprint) -> None:
        """Add or update an entry in the index. Caller must hold self._lock."""
        index_data = self._read_index()
        entries: list[dict[str, Any]] = index_data.get("entries", [])

        # Extract runtime_hints
        runtime_hints: list[str] = []
        rr = bp.runtime_requirements
        if isinstance(rr.python, BlueprintRuntimeRequirement) and rr.python.env_hint:
            runtime_hints.append(rr.python.env_hint)
        if isinstance(rr.r, BlueprintRuntimeRequirement) and rr.r.env_hint:
            runtime_hints.append(rr.r.env_hint)

        entry = CardBlueprintIndexEntry(
            blueprint_id=bp.blueprint_id,
            title=bp.title,
            summary=bp.summary,
            tags=bp.tags,
            domain=bp.domain,
            skills=bp.skills,
            mcp_servers=bp.mcp_servers,
            runtime_hints=runtime_hints,
            use_count=bp.provenance.use_count,
            last_used_at=bp.provenance.last_used_at,
            created_at=bp.provenance.created_at,
        )

        entry_dict = entry.model_dump()
        # Replace if exists, else append
        replaced = False
        for i, existing in enumerate(entries):
            if existing.get("blueprint_id") == bp.blueprint_id:
                entries[i] = entry_dict
                replaced = True
                break
        if not replaced:
            entries.append(entry_dict)

        index_data["entries"] = entries
        self._write_index(index_data)

    def _remove_from_index(self, blueprint_id: str) -> None:
        """Remove an entry from the index. Caller must hold self._lock."""
        index_data = self._read_index()
        entries = index_data.get("entries", [])
        index_data["entries"] = [e for e in entries if e.get("blueprint_id") != blueprint_id]
        self._write_index(index_data)

    @staticmethod
    def _resolve_input_asset(graph: GraphState, asset_id: str) -> Asset | None:
        """Resolve a concrete asset from the graph, following materialization bindings."""
        asset = next((a for a in graph.assets if a.asset_id == asset_id), None)
        if asset is None:
            binding = AssetMaterializationService.current_for_logical(graph, asset_id)
            current_id = binding.get("current_asset_id") if binding else None
            if current_id:
                asset = next((a for a in graph.assets if a.asset_id == current_id), None)
        return asset

    @staticmethod
    def _infer_formats_from_asset(asset: Asset) -> list[str]:
        """Infer accepted input formats from an asset's file extension."""
        fmt = normalize_output_format(Path(asset.path).suffix)
        return [fmt] if fmt else []

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_+\-/.]{2,}", value.lower(), re.I))

    def _score_entry(
        self,
        entry: CardBlueprintIndexEntry,
        query_terms: list[str],
        tag_filters: set[str],
        domain_filter: str,
        runtime_filter: str,
    ) -> float:
        score = 0.0
        haystack = " ".join([
            entry.title.lower(),
            entry.summary.lower(),
            " ".join(entry.tags).lower(),
            entry.domain.lower(),
        ])
        if query_terms:
            matched = sum(1 for term in query_terms if term in haystack)
            score += matched * 1.4
            if matched == 0:
                return 0.0
        if domain_filter:
            if domain_filter in entry.domain.lower():
                score += 1.0
            elif entry.domain:
                return 0.0
        if runtime_filter:
            rt_hints = {self._normalize_text(h) for h in entry.runtime_hints if h}
            if runtime_filter in rt_hints:
                score += 1.0
            elif rt_hints:
                return 0.0
        if tag_filters:
            entry_tags = {self._normalize_text(t) for t in entry.tags if self._normalize_text(t)}
            overlap = len(tag_filters & entry_tags)
            score += overlap * 0.8
            if overlap == 0:
                return 0.0
        return max(score, 0.1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_blueprints(self) -> list[dict[str, Any]]:
        """List all blueprints from the index."""
        with self._lock:
            index_data = self._read_index()
        return index_data.get("entries", [])

    def search_blueprints(
        self,
        query: str = "",
        tags: list[str] | None = None,
        domain: str | None = None,
        runtime: str | None = None,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Search blueprints by query, tags, domain, runtime."""
        entries = [CardBlueprintIndexEntry.model_validate(e) for e in self.list_blueprints()]
        compact_query = self._normalize_text(query)
        query_terms = compact_query.split() if compact_query else []
        tag_filters = {self._normalize_text(t) for t in (tags or []) if self._normalize_text(t)}
        domain_filter = self._normalize_text(domain or "")
        runtime_filter = self._normalize_text(runtime or "")

        scored: list[tuple[float, CardBlueprintIndexEntry]] = []
        for entry in entries:
            score = self._score_entry(entry, query_terms, tag_filters, domain_filter, runtime_filter)
            if score <= 0:
                continue
            scored.append((score, entry))

        scored.sort(key=lambda item: (-item[0], item[1].title.lower()))

        results: list[dict[str, Any]] = []
        for score, entry in scored[:max(1, min(top_k, 50))]:
            results.append(entry.model_dump())
        return results

    def get_blueprint(self, blueprint_id: str) -> dict[str, Any]:
        """Read a single blueprint.json."""
        bp_path = self._blueprint_dir(blueprint_id) / "blueprint.json"
        if not bp_path.exists():
            raise ValueError(f"Blueprint not found: {blueprint_id}")
        return read_json(bp_path, {})

    def save_from_card(self, project_id: str, card_id: str) -> SaveResult:
        """Extract, desensitize, and save a card as a blueprint.

        AI review (Step 2 in the design doc) is deferred to P1.
        For P0, only rule-based desensitization is applied, and a
        degradation warning is emitted to inform the user.
        """
        warnings: list[str] = ["未进行 AI 泛化检查（规则脱敏已完成）"]

        # Read source card
        store = self.project_service.graph_store(project_id)
        cards = store.load_cards()
        source_card: Card | None = None
        for c in cards:
            if c.card_id == card_id:
                source_card = c
                break
        if source_card is None:
            raise ValueError(f"Card not found: {card_id} in project {project_id}")

        # Load graph for asset format inference
        graph = store.load_graph()

        # Extract from card
        title = _scrub_text(source_card.title)
        summary = _scrub_text(source_card.summary)

        # executor_context
        ec: ExecutorContext | None = source_card.executor_context
        skills = list(ec.skills) if ec else []
        mcp_servers = list(ec.mcp_servers) if ec else []
        instruction_blocks = [_scrub_text(b) for b in (ec.instruction_blocks if ec else [])]

        # Runtime requirements from runtime_bindings
        runtime_requirements = BlueprintRuntimeRequirements()
        if ec and ec.runtime_bindings:
            rb = ec.runtime_bindings
            if rb.conda_env and rb.conda_env != "__system__":
                hint = rb.conda_env
                runtime_requirements.python = BlueprintRuntimeRequirement(env_hint=hint)
            if rb.r_env and rb.r_env != "__system__":
                runtime_requirements.r = BlueprintRuntimeRequirement(env_hint=rb.r_env)

        # Inputs schema — strip asset_id; infer accepted_formats from the bound asset
        inputs_schema: list[BlueprintInputSchema] = []
        for inp in source_card.inputs:
            accepted_formats: list[str] = []
            if inp.asset_id:
                asset = self._resolve_input_asset(graph, inp.asset_id)
                if asset is not None:
                    accepted_formats = self._infer_formats_from_asset(asset)
            inputs_schema.append(BlueprintInputSchema(
                slot=_slugify(inp.label),
                label=inp.label,
                accepted_formats=accepted_formats,
                required=True,
            ))

        # Outputs schema — from CardOutputSpec, strip asset_id/status
        outputs_schema: list[BlueprintOutputSchema] = []
        for out in source_card.outputs:
            outputs_schema.append(BlueprintOutputSchema(
                role=out.role,
                label=out.label,
                artifact_class=out.artifact_class,
                accepted_formats=out.accepted_formats,
                preferred_format=out.preferred_format,
                required=out.required,
            ))

        # Generate ID and save
        with self._lock:
            self._ensure_dirs()
            blueprint_id = self._generate_blueprint_id(title)
            now = utc_now()

            bp = CardBlueprint(
                blueprint_id=blueprint_id,
                title=title,
                summary=summary,
                instruction_blocks=instruction_blocks,
                skills=skills,
                mcp_servers=mcp_servers,
                runtime_requirements=runtime_requirements,
                inputs_schema=inputs_schema,
                outputs_schema=outputs_schema,
                parameters=[],  # No parameters from card
                provenance=BlueprintProvenance(
                    source_card_id=None,  # Desensitized
                    source_project_id=None,  # Desensitized
                    created_at=now,
                    created_by="user",
                    use_count=0,
                ),
            )

            bp_dir = self._blueprint_dir(blueprint_id)
            bp_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(bp_dir / "blueprint.json", bp.model_dump())
            self._add_to_index(bp)

        return SaveResult(blueprint_id=blueprint_id, warnings=warnings)

    def save_from_import(self, blueprint_data: dict[str, Any]) -> SaveResult:
        """Import a blueprint from raw JSON data."""
        warnings: list[str] = []

        bp = CardBlueprint.model_validate(blueprint_data)

        with self._lock:
            self._ensure_dirs()

            # Regenerate ID to avoid collision
            blueprint_id = self._generate_blueprint_id(bp.title)
            bp.blueprint_id = blueprint_id
            bp.provenance.created_at = bp.provenance.created_at or utc_now()

            bp_dir = self._blueprint_dir(blueprint_id)
            bp_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(bp_dir / "blueprint.json", bp.model_dump())
            self._add_to_index(bp)

        return SaveResult(blueprint_id=blueprint_id, warnings=warnings)

    def update_blueprint(self, blueprint_id: str, updates: UpdateBlueprintRequest) -> dict[str, Any]:
        """Update editable metadata fields of a blueprint."""
        with self._lock:
            bp_data = self.get_blueprint(blueprint_id)
            bp = CardBlueprint.model_validate(bp_data)

            if updates.title is not None:
                bp.title = updates.title
            if updates.summary is not None:
                bp.summary = updates.summary
            if updates.tags is not None:
                bp.tags = updates.tags
            if updates.domain is not None:
                bp.domain = updates.domain

            bp_dir = self._blueprint_dir(blueprint_id)
            atomic_write_json(bp_dir / "blueprint.json", bp.model_dump())
            self._add_to_index(bp)

            return bp.model_dump()

    def delete_blueprint(self, blueprint_id: str) -> dict[str, Any]:
        """Delete a blueprint."""
        with self._lock:
            bp_dir = self._blueprint_dir(blueprint_id)
            if not bp_dir.exists():
                raise ValueError(f"Blueprint not found: {blueprint_id}")

            # Remove blueprint files
            import shutil
            shutil.rmtree(bp_dir)
            self._remove_from_index(blueprint_id)

        return {"ok": True, "blueprint_id": blueprint_id}

    def instantiate(
        self,
        blueprint_id: str,
        project_id: str,
        request: InstantiateRequest,
    ) -> InstantiateResult:
        """Instantiate a blueprint as a project card."""
        warnings: list[str] = []
        blockers: list[str] = []

        # ── Fix #3: Validate project exists ──────────────────────────────
        project_path = self.project_service.project_path(project_id)
        if not (project_path / "project.json").exists():
            return InstantiateResult(
                card_id="",
                warnings=[],
                blockers=[f"Project not found: {project_id}"],
            )

        # Read blueprint and project graph
        try:
            bp_data = self.get_blueprint(blueprint_id)
            bp = CardBlueprint.model_validate(bp_data)
        except ValueError as exc:
            return InstantiateResult(card_id="", warnings=[], blockers=[str(exc)])

        graph = self.project_service.graph_store(project_id).load_graph()

        # ── Fix #1: Validate required parameters ─────────────────────────
        for param in bp.parameters:
            if param.required and param.name not in request.parameter_values:
                blockers.append(f"Required parameter '{param.name}' is not provided.")
        if blockers:
            return InstantiateResult(card_id="", warnings=warnings, blockers=blockers)

        # Validate parameter value types and content
        for param in bp.parameters:
            value = request.parameter_values.get(param.name, param.default)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                blockers.append(f"Parameter '{param.name}' must be a simple value, not a complex type.")
                continue
            value_str = str(value)
            # Check for path patterns (all three: /home, /Users, C:\)
            if any(pat.search(value_str) for pat in _PATH_PATTERNS):
                blockers.append(f"Parameter '{param.name}' contains a file path.")
            # Check for secrets — design doc requires blocking, not just warning
            if _SECRET_PATTERN.search(value_str):
                blockers.append(f"Parameter '{param.name}' may contain sensitive information (key/token/password).")

        if blockers:
            return InstantiateResult(card_id="", warnings=warnings, blockers=blockers)

        # ── Fix #1: Validate required inputs are bound ───────────────────
        for inp_schema in bp.inputs_schema:
            if inp_schema.required and not request.input_bindings.get(inp_schema.slot):
                blockers.append(
                    f"Required input '{inp_schema.label}' (slot: {inp_schema.slot}) is not bound."
                )

        # ── Fix #2: Validate bound input assets exist, are usable, and match formats
        for inp_schema in bp.inputs_schema:
            asset_id = request.input_bindings.get(inp_schema.slot)
            if not asset_id:
                continue
            asset = self._resolve_input_asset(graph, asset_id)
            if asset is None:
                blockers.append(
                    f"Bound input '{inp_schema.label}' (slot: {inp_schema.slot}) references unknown asset '{asset_id}'."
                )
                continue
            if asset.status not in _VALID_INPUT_STATUSES:
                blockers.append(
                    f"Bound input '{inp_schema.label}' (slot: {inp_schema.slot}) asset '{asset_id}' has unusable status '{asset.status}'."
                )
            if inp_schema.accepted_formats:
                asset_format = normalize_output_format(Path(asset.path).suffix)
                if not asset_format:
                    blockers.append(
                        f"Bound input '{inp_schema.label}' (slot: {inp_schema.slot}) has no inferable format, but accepted formats are {inp_schema.accepted_formats}."
                    )
                elif asset_format not in inp_schema.accepted_formats:
                    blockers.append(
                        f"Bound input '{inp_schema.label}' (slot: {inp_schema.slot}) format '{asset_format}' is not in accepted formats {inp_schema.accepted_formats}."
                    )

        # ── Fix #3: Validate skill/MCP availability and enabled state ─────
        if self.library_registry_service is not None:
            for skill_id in bp.skills:
                try:
                    entry = self.library_registry_service.get_entry("skill", skill_id)
                except ValueError:
                    blockers.append(f"Skill '{skill_id}' is not available in the library.")
                else:
                    if not entry.get("item", {}).get("enabled", True):
                        blockers.append(f"Skill '{skill_id}' is disabled in the library.")
            for mcp_id in bp.mcp_servers:
                try:
                    entry = self.library_registry_service.get_entry("mcp", mcp_id)
                except ValueError:
                    blockers.append(f"MCP server '{mcp_id}' is not available in the library.")
                else:
                    if not entry.get("item", {}).get("enabled", True):
                        blockers.append(f"MCP server '{mcp_id}' is disabled in the library.")

        # ── Fix #1: Validate runtime requirements ────────────────────────
        rr = bp.runtime_requirements
        needs_python = isinstance(rr.python, BlueprintRuntimeRequirement) and rr.python.packages
        needs_r = isinstance(rr.r, BlueprintRuntimeRequirement) and rr.r.packages
        if needs_python:
            if not request.python_runtime or request.python_runtime == "__system__":
                blockers.append(
                    f"Blueprint requires a Python runtime (hint: {rr.python.env_hint or 'any'}) "
                    "but none was selected."
                )
            else:
                plan = self.runtime_dependency_resolver_service.resolve(
                    project_id,
                    {
                        "ecosystem": "python",
                        "runtime": request.python_runtime,
                        "packages": rr.python.packages,
                    },
                    settings=self.settings,
                )
                if not plan.ok:
                    blockers.append(
                        f"Python runtime requirement cannot be satisfied: {plan.message or plan.status}"
                    )
        if needs_r:
            if not request.r_runtime or request.r_runtime == "__system__":
                blockers.append(
                    f"Blueprint requires an R runtime (hint: {rr.r.env_hint or 'any'}) "
                    "but none was selected."
                )
            else:
                plan = self.runtime_dependency_resolver_service.resolve(
                    project_id,
                    {
                        "ecosystem": "R",
                        "runtime": request.r_runtime,
                        "packages": rr.r.packages,
                    },
                    settings=self.settings,
                )
                if not plan.ok:
                    blockers.append(
                        f"R runtime requirement cannot be satisfied: {plan.message or plan.status}"
                    )

        if blockers:
            return InstantiateResult(card_id="", warnings=warnings, blockers=blockers)

        # Build inputs from blueprint input schema + bindings
        inputs: list[CardAssetRef] = []
        for inp_schema in bp.inputs_schema:
            asset_id = request.input_bindings.get(inp_schema.slot)
            inputs.append(CardAssetRef(
                label=inp_schema.label,
                asset_id=asset_id,
                status="bound" if asset_id else "pending",
            ))

        # Build outputs from blueprint output schema (no asset_id/status)
        outputs: list[CardOutputSpec] = []
        for out_schema in bp.outputs_schema:
            outputs.append(CardOutputSpec(
                role=out_schema.role,
                label=out_schema.label,
                artifact_class=out_schema.artifact_class,
                accepted_formats=out_schema.accepted_formats,
                preferred_format=out_schema.preferred_format,
                required=out_schema.required,
                description=out_schema.description,
            ))

        # Build instruction blocks with parameter injection
        instruction_blocks = list(bp.instruction_blocks)
        for param in bp.parameters:
            value = request.parameter_values.get(param.name, param.default)
            if value is not None:
                instruction_blocks.append(f"Parameter {param.name} = {value}")

        # Build runtime bindings
        runtime_bindings = RuntimeBindings(
            conda_env=request.python_runtime,
            r_env=request.r_runtime,
            python_runtime_source="card_library" if request.python_runtime else None,
            r_runtime_source="card_library" if request.r_runtime else None,
        )

        # Build executor context
        executor_context = ExecutorContext(
            skills=list(bp.skills),
            mcp_servers=list(bp.mcp_servers),
            instruction_blocks=instruction_blocks,
            runtime_bindings=runtime_bindings,
        )

        # Create card
        from uuid import uuid4 as _uuid4
        card_id = str(_uuid4())
        card = Card(
            card_id=card_id,
            card_type="module",
            title=bp.title,
            status="proposed",
            summary=bp.summary,
            inputs=inputs,
            outputs=outputs,
            executor_context=executor_context,
        )

        # ── Fix #4: Save card under project lock ─────────────────────────
        try:
            lock = self.project_service.lock_for(project_id)
            with lock:
                store = self.project_service.graph_store(project_id)
                cards = store.load_cards()
                cards.append(card)
                store.save_cards(cards)
        except Exception as exc:
            return InstantiateResult(
                card_id=card_id,
                warnings=warnings,
                blockers=[f"Failed to save card to project: {exc}"],
            )

        # Update provenance
        with self._lock:
            try:
                bp.provenance.use_count += 1
                bp.provenance.last_used_at = utc_now()
                bp_dir = self._blueprint_dir(blueprint_id)
                if bp_dir.exists():
                    atomic_write_json(bp_dir / "blueprint.json", bp.model_dump())
                    self._add_to_index(bp)
            except Exception:
                warnings.append("Provenance update failed (card was created successfully).")

        return InstantiateResult(card_id=card_id, warnings=warnings, blockers=blockers)

    def export_blueprint(self, blueprint_id: str) -> dict[str, Any]:
        """Export a blueprint as a JSON-serializable dict."""
        return self.get_blueprint(blueprint_id)

    # ------------------------------------------------------------------
    # Cover image
    # ------------------------------------------------------------------

    _ALLOWED_COVER_EXTENSIONS = {".png", ".jpeg", ".jpg", ".webp"}
    _MAX_COVER_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB

    def get_cover_path(self, blueprint_id: str) -> Path | None:
        """Return the cover image path, or None if not found."""
        bp_dir = self._blueprint_dir(blueprint_id)
        if not bp_dir.exists():
            raise ValueError(f"Blueprint not found: {blueprint_id}")
        for ext in ("png", "jpeg", "jpg", "webp"):
            candidate = bp_dir / f"cover.{ext}"
            if candidate.exists():
                return candidate
        return None

    def save_cover(
        self,
        blueprint_id: str,
        file_content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """Save an uploaded cover image. Returns {ok, cover_art} or raises."""

        # Validate blueprint exists
        bp_data = self.get_blueprint(blueprint_id)
        bp = CardBlueprint.model_validate(bp_data)

        # Validate extension
        suffix = Path(filename).suffix.lower()
        if suffix == ".jpg":
            suffix = ".jpeg"
        if suffix == ".svg":
            raise ValueError("SVG covers are not allowed (XSS risk).")
        if suffix not in self._ALLOWED_COVER_EXTENSIONS:
            raise ValueError(f"Unsupported cover format: {suffix}. Allowed: PNG, JPEG, WebP.")

        # Validate size
        if len(file_content) > self._MAX_COVER_SIZE_BYTES:
            raise ValueError(f"Cover image too large ({len(file_content)} bytes). Max: 2 MB.")

        # Validate actual image content via magic bytes
        _IMAGE_SIGNATURES = {
            b"\x89PNG": "png",
            b"\xff\xd8\xff": "jpeg",
            b"RIFF": "webp",  # WebP files start with RIFF....WEBP
        }
        detected_fmt: str | None = None
        for sig, fmt in _IMAGE_SIGNATURES.items():
            if file_content[:len(sig)] == sig:
                detected_fmt = fmt
                break
        if detected_fmt not in ("png", "jpeg", "webp"):
            raise ValueError(f"Invalid image format. Allowed: PNG, JPEG, WebP.")

        # Remove old cover if extension changed
        bp_dir = self._blueprint_dir(blueprint_id)
        for ext in ("png", "jpeg", "jpg", "webp"):
            old = bp_dir / f"cover.{ext}"
            if old.exists() and old.suffix != suffix:
                old.unlink()

        cover_filename = f"cover{suffix}"
        cover_path = bp_dir / cover_filename
        cover_path.write_bytes(file_content)

        # Update blueprint cover_art field
        bp.cover_art = cover_filename
        with self._lock:
            atomic_write_json(bp_dir / "blueprint.json", bp.model_dump())
            self._add_to_index(bp)

        return {"ok": True, "cover_art": cover_filename}
