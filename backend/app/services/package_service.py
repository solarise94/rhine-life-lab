from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.models.packages import (
    PackageCompatibility,
    PackageImportResult,
    PackageImportStatus,
    PackageIndexEntry,
    PackageManifest,
    PortableCardPackage,
)
from app.services.library_registry_service import LibraryRegistryService
from app.services.utils import atomic_write_json, read_json, utc_now


# Security scan patterns for bundle files
_RISKY_SCRIPT_PATTERNS = [
    re.compile(r"\beval\s*\(", re.I),
    re.compile(r"\bexec\s*\(", re.I),
    re.compile(r"\bos\.system\s*\(", re.I),
    re.compile(r"\bsubprocess\b", re.I),
    re.compile(r"\bsystem\s*\(", re.I),
    re.compile(r"\bshell\s*=\s*True", re.I),
    re.compile(r"\burlopen\s*\(", re.I),
    re.compile(r"\burllib\.request\b", re.I),
    re.compile(r"\brequests\.get\s*\(", re.I),
]

_RISKY_URL_PATTERNS = [
    re.compile(r"https?://[^\s\"'`]+\.(exe|dll|so|dylib|bin)", re.I),
    re.compile(r"\bcurl\s+.*\|\s*(sh|bash|python)", re.I),
]

_RISKY_PROMPT_PATTERNS = [
    re.compile(r"\bignore\s+(previous\s+)?instructions?\b", re.I),
    re.compile(r"\bignore\s+(your\s+)?(safety\s+)?(guidelines?|rules?)\b", re.I),
    re.compile(r"\bdisregard\s+system\s+(prompt|instruction)\b", re.I),
    re.compile(r"\b(exfiltrate|leak|send)\s+(secret|key|token|password)\b", re.I),
]

_MAX_BUNDLE_FILES = 32
_MAX_BUNDLE_FILE_SIZE_BYTES = 256 * 1024  # 256 KB
_ALLOWED_BUNDLE_EXTENSIONS = {
    ".py",
    ".r",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".sh",
    ".js",
    ".css",
    ".html",
    ".csv",
    ".tsv",
}


class PackageService:
    def __init__(
        self,
        library_registry_service: LibraryRegistryService,
        settings: Settings | None = None,
    ) -> None:
        self.library_registry_service = library_registry_service
        self.settings = settings or get_settings()
        self.packages_root = Path(self.settings.data_root) / "_system" / "packages"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def import_package(
        self,
        source_path: str,
        *,
        overwrite: bool = False,
    ) -> PackageImportResult:
        """Import a portable card package from a local directory or archive."""
        source = Path(source_path)
        if not source.exists():
            return PackageImportResult(
                status="blocked",
                package_id="",
                version="",
                blockers=[f"Source path does not exist: {source_path}"],
            )

        # Load manifest
        manifest_dict = self._load_manifest_dict(source)
        if manifest_dict is None:
            return PackageImportResult(
                status="blocked",
                package_id="",
                version="",
                blockers=[f"manifest.json not found in {source_path}"],
            )

        # Validate manifest
        try:
            manifest = PackageManifest.model_validate(manifest_dict)
        except Exception as exc:
            return PackageImportResult(
                status="blocked",
                package_id=manifest_dict.get("package_id", ""),
                version=manifest_dict.get("version", ""),
                blockers=[f"Invalid manifest: {exc}"],
            )

        # Schema version check (v1 only)
        if manifest.schema_version != "portable_card_package.v1":
            return PackageImportResult(
                status="blocked",
                package_id=manifest.package_id,
                version=manifest.version,
                blockers=[f"Unsupported schema version: {manifest.schema_version}"],
            )

        # Load bundle files
        bundle_files = self._load_bundle_files(source, manifest)

        # Security scan
        scan_warnings = self._security_scan_bundle(bundle_files)

        # Compute content hash
        content_hash = self._compute_content_hash(manifest, bundle_files)

        # Verify declared hash if present
        if manifest.provenance.content_hash and manifest.provenance.content_hash != content_hash:
            return PackageImportResult(
                status="blocked",
                package_id=manifest.package_id,
                version=manifest.version,
                blockers=["Content hash mismatch. Package may be corrupted or tampered with."],
            )

        # Resolve capabilities (skills / MCPs)
        cap_warnings, cap_blockers = self._resolve_capabilities(manifest)

        warnings = scan_warnings + cap_warnings
        blockers = cap_blockers

        if blockers:
            return PackageImportResult(
                status="blocked",
                package_id=manifest.package_id,
                version=manifest.version,
                warnings=warnings,
                blockers=blockers,
            )

        # Check existing
        package_dir = self._package_dir(manifest.package_id, manifest.version)
        if package_dir.exists() and not overwrite:
            return PackageImportResult(
                status="blocked",
                package_id=manifest.package_id,
                version=manifest.version,
                blockers=[f"Package {manifest.package_id}@{manifest.version} already imported. Use overwrite=True to replace."],
            )

        # Store
        package = PortableCardPackage(
            manifest=manifest,
            bundle_files=bundle_files,
        )
        self._store_package(package, content_hash)

        status: PackageImportStatus = "ready_with_warnings" if warnings else "ready"
        return PackageImportResult(
            status=status,
            package_id=manifest.package_id,
            version=manifest.version,
            warnings=warnings,
        )

    def list_packages(self) -> list[PackageIndexEntry]:
        """List all imported packages from the lightweight index."""
        index = self._read_index()
        return [PackageIndexEntry.model_validate(item) for item in index.get("entries", [])]

    def search_packages(
        self,
        query: str = "",
        tags: list[str] | None = None,
        runtime: str | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        """Search packages by query, tags, or runtime."""
        entries = self.list_packages()
        compact_query = self._normalize_text(query)
        query_terms = compact_query.split() if compact_query else []
        tag_filters = {self._normalize_text(t) for t in (tags or []) if self._normalize_text(t)}
        runtime_filter = self._normalize_text(runtime or "")

        scored: list[tuple[float, PackageIndexEntry]] = []
        for entry in entries:
            score = self._score_index_entry(entry, query_terms, tag_filters, runtime_filter)
            if score <= 0:
                continue
            scored.append((score, entry))

        scored.sort(key=lambda item: (-item[0], item[1].title.lower()))

        results: list[dict[str, Any]] = []
        for score, entry in scored[:max(1, min(top_k, 20))]:
            results.append(
                {
                    "package_id": entry.package_id,
                    "version": entry.version,
                    "title": entry.title,
                    "summary": entry.summary,
                    "compatibility": entry.compatibility.model_dump(),
                    "match_reason": self._build_package_match_reason(
                        entry, query_terms, tag_filters, runtime_filter
                    ),
                    "score": round(score, 4),
                }
            )
        return results

    def get_package(self, package_id: str, version: str | None = None) -> PortableCardPackage | None:
        """Retrieve a full package by id and optional version (latest if omitted)."""
        if version:
            manifest_path = self._package_dir(package_id, version) / "manifest.json"
            if not manifest_path.exists():
                return None
            manifest_dict = read_json(manifest_path, {})
            manifest = PackageManifest.model_validate(manifest_dict)
            bundle_files = self._load_stored_bundle_files(package_id, version)
            return PortableCardPackage(manifest=manifest, bundle_files=bundle_files)

        # Find latest version
        package_root = self.packages_root / package_id
        if not package_root.exists():
            return None
        versions = sorted(
            [d.name for d in package_root.iterdir() if d.is_dir()],
            key=lambda v: v.split(".")[:3],
            reverse=True,
        )
        if not versions:
            return None
        return self.get_package(package_id, versions[0])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _package_dir(self, package_id: str, version: str) -> Path:
        return self.packages_root / package_id / version

    def _load_manifest_dict(self, source: Path) -> dict[str, Any] | None:
        """Load manifest.json from source directory or archive."""
        if source.is_dir():
            manifest_path = source / "manifest.json"
            if manifest_path.exists():
                return read_json(manifest_path, None)
            return None

        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source, "r") as zf:
                for name in ("manifest.json",):
                    try:
                        return json.loads(zf.read(name).decode("utf-8"))
                    except (KeyError, json.JSONDecodeError):
                        continue
        return None

    def _load_bundle_files(self, source: Path, manifest: PackageManifest) -> dict[str, str]:
        """Load text bundle files from source directory."""
        files: dict[str, str] = {}
        if not source.is_dir():
            return files

        bundle_dir = source / "bundle"
        if not bundle_dir.exists():
            return files

        for bf in manifest.bundle.files:
            file_path = bundle_dir / bf.path
            if not file_path.exists():
                continue
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lower()
            if ext not in _ALLOWED_BUNDLE_EXTENSIONS:
                continue
            size = file_path.stat().st_size
            if size > _MAX_BUNDLE_FILE_SIZE_BYTES:
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
                files[bf.path] = content
            except (OSError, UnicodeDecodeError):
                continue
        return files

    def _load_stored_bundle_files(self, package_id: str, version: str) -> dict[str, str]:
        """Load bundle files from stored location."""
        files: dict[str, str] = {}
        bundle_dir = self._package_dir(package_id, version) / "bundle"
        if not bundle_dir.exists():
            return files
        for file_path in bundle_dir.rglob("*"):
            if not file_path.is_file():
                continue
            ext = file_path.suffix.lower()
            if ext not in _ALLOWED_BUNDLE_EXTENSIONS:
                continue
            try:
                rel_path = str(file_path.relative_to(bundle_dir))
                files[rel_path] = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return files

    def _security_scan_bundle(self, bundle_files: dict[str, str]) -> list[str]:
        """Lightweight heuristic scan of bundle file contents."""
        warnings: list[str] = []
        for path, content in bundle_files.items():
            # Script risk
            for pattern in _RISKY_SCRIPT_PATTERNS:
                if pattern.search(content):
                    warnings.append(f"[{path}] Potential script risk detected.")
                    break
            # URL risk
            for pattern in _RISKY_URL_PATTERNS:
                if pattern.search(content):
                    warnings.append(f"[{path}] Suspicious URL or remote execution pattern detected.")
                    break
            # Prompt injection risk
            for pattern in _RISKY_PROMPT_PATTERNS:
                if pattern.search(content):
                    warnings.append(f"[{path}] Potential prompt injection pattern detected.")
                    break
        return warnings

    def _resolve_capabilities(self, manifest: PackageManifest) -> tuple[list[str], list[str]]:
        """Check whether required skills and MCPs exist in the local registry."""
        warnings: list[str] = []
        blockers: list[str] = []

        try:
            skill_registry = self.library_registry_service._ensure_registry("skill")
            skill_ids = {item.id for item in skill_registry.items}
        except Exception:
            skill_ids = set()

        try:
            mcp_registry = self.library_registry_service._ensure_registry("mcp")
            mcp_ids = {item.id for item in mcp_registry.items}
        except Exception:
            mcp_ids = set()

        for skill_id in manifest.compatibility.required_skills:
            if skill_id not in skill_ids:
                blockers.append(f"Required skill not found: {skill_id}")

        for skill_id in manifest.compatibility.optional_skills:
            if skill_id not in skill_ids:
                warnings.append(f"Optional skill not found: {skill_id}")

        for mcp_id in manifest.compatibility.required_mcps:
            if mcp_id not in mcp_ids:
                blockers.append(f"Required MCP not found: {mcp_id}")

        for mcp_id in manifest.compatibility.optional_mcps:
            if mcp_id not in mcp_ids:
                warnings.append(f"Optional MCP not found: {mcp_id}")

        return warnings, blockers

    def _compute_content_hash(self, manifest: PackageManifest, bundle_files: dict[str, str]) -> str:
        """Compute a SHA-256 hash over manifest + sorted bundle files."""
        hasher = hashlib.sha256()
        hasher.update(manifest.model_dump_json().encode("utf-8"))
        for path in sorted(bundle_files):
            hasher.update(path.encode("utf-8"))
            hasher.update(bundle_files[path].encode("utf-8"))
        return f"sha256:{hasher.hexdigest()}"

    def _store_package(self, package: PortableCardPackage, content_hash: str) -> None:
        """Persist package manifest and bundle files to disk."""
        manifest = package.manifest
        package_dir = self._package_dir(manifest.package_id, manifest.version)
        package_dir.mkdir(parents=True, exist_ok=True)

        # Write manifest (update content_hash)
        manifest_dict = manifest.model_dump()
        manifest_dict["provenance"]["content_hash"] = content_hash
        atomic_write_json(package_dir / "manifest.json", manifest_dict)

        # Write bundle files
        bundle_dir = package_dir / "bundle"
        if package.bundle_files:
            bundle_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in package.bundle_files.items():
                file_path = bundle_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content, encoding="utf-8")

        # Update index
        self._update_index(
            PackageIndexEntry(
                package_id=manifest.package_id,
                version=manifest.version,
                title=manifest.title,
                summary=manifest.summary,
                tags=list(manifest.tags),
                compatibility=manifest.compatibility,
            )
        )

    def _read_index(self) -> dict[str, Any]:
        index_path = self.packages_root / "index.json"
        return read_json(index_path, {"entries": []})

    def _write_index(self, index: dict[str, Any]) -> None:
        index_path = self.packages_root / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(index_path, index)

    def _update_index(self, entry: PackageIndexEntry) -> None:
        index = self._read_index()
        entries: list[dict[str, Any]] = index.get("entries", [])
        # Remove existing entry for same package_id + version
        entries = [
            item
            for item in entries
            if not (item.get("package_id") == entry.package_id and item.get("version") == entry.version)
        ]
        entries.append(entry.model_dump())
        index["entries"] = entries
        index["updated_at"] = utc_now()
        self._write_index(index)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_+\-/.]{2,}", value.lower(), re.I))

    def _score_index_entry(
        self,
        entry: PackageIndexEntry,
        query_terms: list[str],
        tag_filters: set[str],
        runtime_filter: str,
    ) -> float:
        score = 0.0
        haystack = " ".join(
            [
                entry.title.lower(),
                entry.summary.lower(),
                " ".join(entry.tags).lower(),
            ]
        )
        if query_terms:
            matched = sum(1 for term in query_terms if term in haystack)
            score += matched * 1.4
            if matched == 0:
                return 0.0
        if runtime_filter:
            runtimes = {
                self._normalize_text(r)
                for r in entry.compatibility.supported_runtimes
                if self._normalize_text(r)
            }
            if runtime_filter in runtimes:
                score += 1.0
            elif runtimes:
                return 0.0
        if tag_filters:
            entry_tags = {self._normalize_text(t) for t in entry.tags if self._normalize_text(t)}
            overlap = len(tag_filters & entry_tags)
            score += overlap * 0.8
            if overlap == 0:
                return 0.0
        return max(score, 0.1)

    @staticmethod
    def _build_package_match_reason(
        entry: PackageIndexEntry,
        query_terms: list[str],
        tag_filters: set[str],
        runtime_filter: str,
    ) -> str:
        norm = PackageService._normalize_text
        parts: list[str] = []
        norm_title = norm(entry.title)
        name_hits = [t for t in query_terms if t in norm_title]
        if name_hits:
            parts.append(f"title: {', '.join(name_hits)}")
        norm_summary = norm(entry.summary)
        summary_hits = [t for t in query_terms if t in norm_summary]
        if summary_hits:
            parts.append(f"summary: {', '.join(summary_hits)}")
        entry_tag_norms = {norm(t) for t in entry.tags}
        tag_hits = tag_filters & entry_tag_norms
        if tag_hits:
            parts.append(f"tags: {', '.join(tag_hits)}")
        if runtime_filter:
            runtime_norms = {norm(r) for r in entry.compatibility.supported_runtimes if norm(r)}
            if runtime_filter in runtime_norms:
                parts.append(f"runtime: {runtime_filter}")
        return "; ".join(parts) if parts else "broad match"
