"""Runtime dependency resolution planner (P1).

The resolver is a deterministic planning layer that runs *before* the installer
creates a background job. Its job is to answer the question "what would happen
if I tried to install this dependency request?" without mutating any runtime
state.

The resolver:

- inspects every package in the request, classifying it as ``conda_installable``,
  ``fallback_required``, ``manual_preparation_required``, ``unsupported_source_spec``,
  ``runtime_missing``, or ``unknown``;
- produces a request-level summary status that the caller can map directly to
  P0 normalized fields;
- caches channel probes in memory for a short TTL to avoid repeated slow
  ``mamba repoquery search`` / ``conda search --json`` invocations;
- never executes installers and never mutates the environment.

The fallback policy is enforced here as well: ``report_only`` is the default
and never produces an executable fallback action; ``allow_safe_registry_install``
may emit structured registry installer actions for validated bare names, but
the actual execution is still owned by the backend installer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Iterable

from app.services.runtime_dependency_state_service import compute_dedupe_key


# ---------------------------------------------------------------------------
# Status vocabularies
# ---------------------------------------------------------------------------

# Request-level statuses. The mapping to P0 fields is documented in doc 41 P1.1.
RESOLVER_STATUS_FULLY_INSTALLABLE = "fully_installable"
RESOLVER_STATUS_PARTIAL_RESOLUTION = "partial_resolution_requires_manual_preparation"
RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS = "fallback_available_but_policy_disallows"
RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED = "manual_preparation_required"
RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC = "unsupported_source_spec"
RESOLVER_STATUS_RUNTIME_MISSING = "runtime_missing"
RESOLVER_STATUS_RESOLUTION_UNKNOWN = "resolution_unknown"

RESOLVER_REQUEST_STATUSES: frozenset[str] = frozenset(
    {
        RESOLVER_STATUS_FULLY_INSTALLABLE,
        RESOLVER_STATUS_PARTIAL_RESOLUTION,
        RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS,
        RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED,
        RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC,
        RESOLVER_STATUS_RUNTIME_MISSING,
        RESOLVER_STATUS_RESOLUTION_UNKNOWN,
    }
)

# Per-package statuses.
PACKAGE_STATUS_CONDA_INSTALLABLE = "conda_installable"
PACKAGE_STATUS_FALLBACK_REQUIRED = "fallback_required"
PACKAGE_STATUS_MANUAL_PREPARATION_REQUIRED = "manual_preparation_required"
PACKAGE_STATUS_UNSUPPORTED_SOURCE_SPEC = "unsupported_source_spec"
PACKAGE_STATUS_RUNTIME_MISSING = "runtime_missing"
PACKAGE_STATUS_UNKNOWN = "unknown"

# Mapping from resolver request-level status to P0 normalized fields.
RESOLVER_TO_P0_FIELDS: dict[str, dict[str, str | None]] = {
    RESOLVER_STATUS_FULLY_INSTALLABLE: {"error_code": None, "retry_hint": None},
    RESOLVER_STATUS_PARTIAL_RESOLUTION: {
        "error_code": RESOLVER_STATUS_PARTIAL_RESOLUTION,
        "retry_hint": "manual_preparation_required",
    },
    RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS: {
        "error_code": "package_not_found_in_conda_channels",
        "retry_hint": "choose_fallback",
    },
    RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED: {
        "error_code": RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED,
        "retry_hint": "manual_preparation_required",
    },
    RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC: {
        "error_code": "github_source_install_not_supported",
        "retry_hint": "do_not_retry_installer",
    },
    RESOLVER_STATUS_RUNTIME_MISSING: {
        "error_code": "dependency_install_start_failed",
        "retry_hint": "manual_runtime_preparation_required",
    },
    RESOLVER_STATUS_RESOLUTION_UNKNOWN: {
        "error_code": "dependency_resolution_unknown",
        "retry_hint": "inspect_stderr",
    },
}

# Fallback families for each ecosystem.
FALLBACK_FAMILIES_PYTHON: list[str] = ["pip"]
FALLBACK_FAMILIES_R: list[str] = ["cran", "bioconductor"]

# Grammar used to validate bare names when a fallback install action is emitted.
# We keep it strict to refuse source-style inputs.
BARE_NAME_GRAMMAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Strict version pin grammar: <bare-name>[==<version>]?  (e.g. numpy or numpy==1.26.4)
VERSION_PIN_GRAMMAR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(==[A-Za-z0-9_.+-]+)?$")

# Source-install patterns that the resolver must always reject.
SOURCE_SPEC_PATTERNS = (
    "github.com",
    "git+",
    "http://",
    "https://",
    ".tar.gz",
    ".zip",
    ".whl",
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    value: dict[str, Any] | None  # None means "checked and not found"
    expires_at: float


class _ResolverCache:
    """In-memory cache for conda package probes.

    Keys are tuples of (channel_set_signature, package_name). Values are
    _CacheEntry instances. Entries are bounded by a default TTL of 1 hour.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._entries: dict[tuple[str, str], _CacheEntry] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> dict[str, Any] | None | object:
        """Return cached value or the sentinel ``_MISS`` if absent/expired.

        Use ``is`` checks to distinguish from a real ``None`` value.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISS
            if entry.expires_at < time.time():
                self._entries.pop(key, None)
                return _MISS
            return entry.value

    def set(self, key: tuple[str, str], value: dict[str, Any] | None) -> None:
        with self._lock:
            self._entries[key] = _CacheEntry(
                value=value,
                expires_at=time.time() + self._ttl_seconds,
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_MISS = object()


# ---------------------------------------------------------------------------
# Public dataclasses (plan + actions)
# ---------------------------------------------------------------------------


@dataclass
class ResolverPackageEntry:
    name: str
    normalized_name: str
    classification: str  # e.g. "conda", "pip", "cran", "bioconductor", "source"
    conda_candidates: list[str] = field(default_factory=list)
    conda_match: str | None = None
    fallback_available: list[str] = field(default_factory=list)
    status: str = PACKAGE_STATUS_UNKNOWN
    reason: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "normalized_name": self.normalized_name,
            "classification": self.classification,
            "conda_candidates": list(self.conda_candidates),
            "fallback_available": list(self.fallback_available),
            "status": self.status,
        }
        if self.conda_match is not None:
            out["conda_match"] = self.conda_match
        if self.reason is not None:
            out["reason"] = self.reason
        if self.message is not None:
            out["message"] = self.message
        return out


@dataclass
class ResolverInstallAction:
    installer: str  # "conda" | "pip" | "cran" | "bioconductor"
    name: str  # bare name (no source paths or URLs)
    candidate: str | None = None  # conda candidate if applicable
    version_pin: str | None = None  # exact version if pinned

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": "install",
            "installer": self.installer,
            "name": self.name,
        }
        if self.candidate is not None:
            out["candidate"] = self.candidate
        if self.version_pin is not None:
            out["version_pin"] = self.version_pin
        return out


@dataclass
class ResolverBlockedEntry:
    name: str
    reason: str
    attempted_candidates: list[str] = field(default_factory=list)
    fallback_available: list[str] = field(default_factory=list)
    recommended_action: str = "manual_preparation_or_policy_approved_fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "attempted_candidates": list(self.attempted_candidates),
            "fallback_available": list(self.fallback_available),
            "recommended_action": self.recommended_action,
        }


@dataclass
class RuntimeDependencyResolutionPlan:
    ok: bool
    tool: str = "resolve_runtime_dependencies"
    ecosystem: str = ""
    runtime: str = ""
    status: str = RESOLVER_STATUS_RESOLUTION_UNKNOWN
    error_code: str | None = None
    retry_hint: str | None = None
    message: str | None = None
    request_dedupe_key: str = ""
    packages: list[ResolverPackageEntry] = field(default_factory=list)
    installable: list[ResolverInstallAction] = field(default_factory=list)
    blocked: list[ResolverBlockedEntry] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    # Internal: raw conda actions discovered before the policy step.
    # Not serialized.
    _candidate_actions: list[ResolverInstallAction] = field(default_factory=list)
    cache: _ResolverCache | None = None  # internal; not serialized

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "tool": self.tool,
            "ecosystem": self.ecosystem,
            "runtime": self.runtime,
            "status": self.status,
            "packages": [pkg.to_dict() for pkg in self.packages],
            "installable": [act.to_dict() for act in self.installable],
            "blocked": [blk.to_dict() for blk in self.blocked],
            "recommended_actions": list(self.recommended_actions),
            "request_dedupe_key": self.request_dedupe_key,
        }
        if self.error_code is not None:
            out["error_code"] = self.error_code
        if self.retry_hint is not None:
            out["retry_hint"] = self.retry_hint
        if self.message is not None:
            out["message"] = self.message
        return out


# ---------------------------------------------------------------------------
# Resolver service
# ---------------------------------------------------------------------------


class RuntimeDependencyResolverService:
    """Deterministic resolver for runtime dependency requests.

    The service does not perform installation. It only inspects a request,
    optionally probes conda channels for package presence, and returns a
    structured plan.
    """

    def __init__(
        self,
        *,
        conda_solver: str | None = None,
        cache_ttl_seconds: int = 3600,
        probe_timeout_seconds: int = 60,
    ) -> None:
        self._explicit_conda_solver = conda_solver
        self._cache = _ResolverCache(ttl_seconds=cache_ttl_seconds)
        self._probe_timeout = probe_timeout_seconds

    # -- public API --------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()

    def resolve(
        self,
        project_id: str,
        payload: dict[str, Any],
        *,
        settings: Any | None = None,
        policy: str = "report_only",
    ) -> RuntimeDependencyResolutionPlan:
        ecosystem = str(payload.get("ecosystem") or "").strip()
        runtime = str(payload.get("runtime") or "").strip()
        raw_packages = payload.get("packages") or []
        if not isinstance(raw_packages, list):
            raw_packages = [str(raw_packages)]
        # Preserve order, dedupe case-insensitively, strip empties.
        seen: set[str] = set()
        packages: list[str] = []
        for item in raw_packages:
            name = str(item or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            packages.append(name)

        # Stash the active settings on the resolver service so internal
        # helpers (e.g. _probe_runtime) can use the same object without
        # threading it through every call.
        self._settings = settings or _resolver_settings_stub()
        # Normalize policy once so the rest of the resolver can use the
        # canonical ``"report_only"`` / ``"allow_safe_registry_install"`` pair.
        self._active_policy = normalize_fallback_policy(policy)

        plan = RuntimeDependencyResolutionPlan(
            ok=False,
            ecosystem=ecosystem,
            runtime=runtime,
            request_dedupe_key=compute_dedupe_key(
                ecosystem or "unknown",
                runtime or "unknown",
                packages,
            ),
            cache=self._cache,
        )

        if ecosystem.lower() not in {"python", "r"}:
            plan.status = RESOLVER_STATUS_RESOLUTION_UNKNOWN
            plan.error_code = "dependency_resolution_unknown"
            plan.message = f"Unsupported ecosystem: {ecosystem!r}; expected python or R."
            plan.recommended_actions = ["Do not call install_runtime_dependencies with an unknown ecosystem."]
            return plan

        if ecosystem.lower() == "r":
            # Preserve "R" casing so the dedupe key aligns with the
            # ``runtime_dependency_state_service`` cooling lookup.
            ecosystem = "R"

        if not runtime or runtime == "__system__":
            plan.status = RESOLVER_STATUS_RUNTIME_MISSING
            plan.error_code = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_RUNTIME_MISSING]["error_code"]
            plan.retry_hint = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_RUNTIME_MISSING]["retry_hint"]
            plan.message = "Selected runtime is missing or marked as __system__; manual runtime preparation is required."
            plan.recommended_actions = ["Choose a non-system Python or R runtime before retrying."]
            return plan

        if not packages:
            plan.status = RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED
            plan.error_code = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED]["error_code"]
            plan.retry_hint = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED]["retry_hint"]
            plan.message = "No packages were provided."
            plan.recommended_actions = ["Provide at least one bare package name before retrying."]
            return plan

        # Source install inputs are rejected up-front.
        source_packages = [pkg for pkg in packages if _looks_like_source_spec(pkg)]
        if source_packages:
            for pkg in packages:
                if _looks_like_source_spec(pkg):
                    plan.packages.append(
                        ResolverPackageEntry(
                            name=pkg,
                            normalized_name=pkg.lower(),
                            classification="source",
                            conda_candidates=[],
                            fallback_available=[],
                            status=PACKAGE_STATUS_UNSUPPORTED_SOURCE_SPEC,
                            reason="source_install_not_supported",
                            message="Source-install dependencies are not supported; use a registry-backed bare name.",
                        )
                    )
                else:
                    plan.packages.append(
                        ResolverPackageEntry(
                            name=pkg,
                            normalized_name=pkg.lower(),
                            classification=_classify_for_ecosystem(pkg, ecosystem),
                            conda_candidates=[],
                            fallback_available=_fallback_families_for(ecosystem),
                            status=PACKAGE_STATUS_MANUAL_PREPARATION_REQUIRED,
                            reason="request_rejected_for_source_specs",
                        )
                    )
            plan.status = RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC
            plan.error_code = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC]["error_code"]
            plan.retry_hint = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC]["retry_hint"]
            plan.message = "Source-install dependencies are not supported by the resolver."
            plan.recommended_actions = [
                "Use a bare package name (or version pin) instead of a URL, GitHub reference, or tarball.",
            ]
            for pkg in source_packages:
                plan.blocked.append(
                    ResolverBlockedEntry(
                        name=pkg,
                        reason="source_install_not_supported",
                        attempted_candidates=[],
                        fallback_available=[],
                    )
                )
            return plan

        # Try to resolve the runtime path. If we cannot find it, mark all
        # packages as runtime_missing rather than guess.
        runtime_present, runtime_message = self._probe_runtime(runtime, ecosystem)
        if not runtime_present:
            for pkg in packages:
                plan.packages.append(
                    ResolverPackageEntry(
                        name=pkg,
                        normalized_name=_normalize_for_ecosystem(pkg, ecosystem),
                        classification=_classify_for_ecosystem(pkg, ecosystem),
                        conda_candidates=[],
                        fallback_available=_fallback_families_for(ecosystem),
                        status=PACKAGE_STATUS_RUNTIME_MISSING,
                        reason="runtime_path_unresolved",
                        message=runtime_message,
                    )
                )
            plan.status = RESOLVER_STATUS_RUNTIME_MISSING
            plan.error_code = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_RUNTIME_MISSING]["error_code"]
            plan.retry_hint = RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_RUNTIME_MISSING]["retry_hint"]
            plan.message = runtime_message
            plan.recommended_actions = [
                "Verify the selected runtime path exists and is executable before retrying.",
            ]
            return plan

        # Per-package resolution. First probe conda for each package; this
        # populates ``plan.packages`` with the per-package status. We do NOT
        # add conda actions to ``plan.installable`` yet — that decision is
        # made after we know which fallback actions, if any, the policy
        # allows.
        conda_bin = self._resolve_conda_solver(ecosystem)
        channel_signature = self._channel_signature(conda_bin, ecosystem, runtime)
        for pkg in packages:
            entry, conda_action = self._resolve_package(
                pkg,
                ecosystem=ecosystem,
                conda_bin=conda_bin,
                channel_signature=channel_signature,
            )
            plan.packages.append(entry)
            # Record the conda action as a candidate; whether we keep it
            # in the final plan depends on the request-level status.
            if conda_action is not None:
                plan._candidate_actions.append(conda_action)  # type: ignore[attr-defined]

        # Build the installable / blocked action split. Mixed-installer
        # requests are blocked even under allow_safe_registry_install.
        self._populate_installable_actions(plan)

        # Build a stable mapping of blocked entries (every package whose
        # final action was not retained is "blocked" relative to the
        # installer's current plan).
        for entry in plan.packages:
            if entry.status in {
                PACKAGE_STATUS_CONDA_INSTALLABLE,
                PACKAGE_STATUS_FALLBACK_REQUIRED,
            }:
                # May or may not be retained; reflect whatever the final
                # plan.installable / plan.blocked list says.
                if not any(
                    action.name == entry.name for action in plan.installable
                ):
                    plan.blocked.append(
                        ResolverBlockedEntry(
                            name=entry.name,
                            reason=entry.reason or entry.status,
                            attempted_candidates=list(entry.conda_candidates),
                            fallback_available=list(entry.fallback_available),
                        )
                    )
                continue
            plan.blocked.append(
                ResolverBlockedEntry(
                    name=entry.name,
                    reason=entry.reason or entry.status,
                    attempted_candidates=list(entry.conda_candidates),
                    fallback_available=list(entry.fallback_available),
                )
            )

        plan.status, plan.error_code, plan.message = self._aggregate_status(plan)
        plan.ok = plan.status == RESOLVER_STATUS_FULLY_INSTALLABLE
        plan.recommended_actions = self._recommended_actions(plan)
        # Always populate error_code / retry_hint from the P0 mapping for tool
        # consumers (Manager, frontend, workboard).
        mapping = RESOLVER_TO_P0_FIELDS.get(plan.status)
        if mapping is not None:
            if plan.error_code is None:
                plan.error_code = mapping["error_code"]
            if plan.retry_hint is None:
                plan.retry_hint = mapping["retry_hint"]
        return plan

    # -- internals ---------------------------------------------------------

    def _resolve_package(
        self,
        pkg: str,
        *,
        ecosystem: str,
        conda_bin: Path | None,
        channel_signature: str,
    ) -> tuple[ResolverPackageEntry, ResolverInstallAction | None]:
        normalized = _normalize_for_ecosystem(pkg, ecosystem)
        classification = _classify_for_ecosystem(pkg, ecosystem)
        fallback = _fallback_families_for(ecosystem)
        candidates = _conda_candidates_for(pkg, ecosystem)

        # Grammar check uses the lower-cased ecosystem so the same rule applies
        # regardless of whether the caller sent "r" or "R".
        # Names outside the supported grammar (extras, version comparisons,
        # shell chars, local paths, etc.) are rejected as
        # ``unsupported_source_spec`` so the request-level status gives
        # the caller a strong control signal per doc 41 §P1.3.
        if not _is_valid_grammar(pkg, ecosystem.lower()):
            return ResolverPackageEntry(
                name=pkg,
                normalized_name=normalized,
                classification=classification,
                conda_candidates=candidates,
                fallback_available=[],
                status=PACKAGE_STATUS_UNSUPPORTED_SOURCE_SPEC,
                reason="unsupported_spec",
                message=(
                    f"Package spec {pkg!r} does not match the supported "
                    "bare-name grammar. Only bare names (e.g. numpy) or "
                    "exact version pins (e.g. numpy==1.26.4) are allowed."
                ),
            ), None

        if conda_bin is None:
            return ResolverPackageEntry(
                name=pkg,
                normalized_name=normalized,
                classification=classification,
                conda_candidates=candidates,
                fallback_available=fallback,
                status=PACKAGE_STATUS_FALLBACK_REQUIRED,
                reason="conda_solver_unavailable",
                message="No conda solver was found; only fallback families are available.",
            ), None

        cache_key = (channel_signature, candidates[0] if candidates else normalized)
        cached = self._cache.get(cache_key)
        if cached is _MISS:
            match = self._probe_conda(conda_bin, candidates, ecosystem=ecosystem)
            self._cache.set(cache_key, match)
        else:
            match = cached  # type: ignore[assignment]

        if match:
            return ResolverPackageEntry(
                name=pkg,
                normalized_name=normalized,
                classification=classification,
                conda_candidates=candidates,
                conda_match=match,
                fallback_available=fallback,
                status=PACKAGE_STATUS_CONDA_INSTALLABLE,
            ), ResolverInstallAction(
                installer="conda",
                name=pkg,
                candidate=match,
            )

        return ResolverPackageEntry(
            name=pkg,
            normalized_name=normalized,
            classification=classification,
            conda_candidates=candidates,
            fallback_available=fallback,
            status=PACKAGE_STATUS_FALLBACK_REQUIRED,
            reason="package_not_found_in_conda_channels",
            message=(
                f"None of the conda candidates {', '.join(candidates) or '(none)'} "
                f"were found in the configured channels."
            ),
        ), None

    def _populate_installable_actions(
        self,
        plan: RuntimeDependencyResolutionPlan,
    ) -> None:
        """Decide which actions to keep in ``plan.installable``.

        Rules (P1.3):
        - All-conda actions are always surfaced.
        - For packages without a conda match, the resolver may build
          registry-fallback actions only under ``allow_safe_registry_install``
          and only when every fallback action belongs to the same family.
        - ``plan.installable`` reflects the RESOLVER's opinion of which
          packages are individually installable.  The request-level
          ``plan.status`` (set by ``_aggregate_status``) controls whether a
          background job is created.  A mixed-installer plan keeps conda
          actions visible but stays at ``partial_resolution_*`` so the
          installer gate blocks it.
        """
        plan.installable = list(
            [a for a in plan._candidate_actions if a.installer == "conda"]
        )
        non_conda_packages = [
            pkg
            for pkg in plan.packages
            if pkg.status == PACKAGE_STATUS_FALLBACK_REQUIRED
        ]
        policy = getattr(self, "_active_policy", "report_only")

        if not non_conda_packages:
            return

        # P1.3: mixed installer families (conda + pip, conda + cran,
        # etc.) are never auto-executable.  If the plan already contains
        # conda actions, fallback actions must not be added — the caller
        # receives a partial resolution plan and may submit a narrower
        # request.
        if plan.installable:
            return

        if not fallback_policy_allows(policy):
            return

        # Build safe fallback actions from the same family.
        families = {
            pkg.fallback_available[0]
            for pkg in non_conda_packages
            if pkg.fallback_available
        }
        if len(families) != 1:
            return
        family = next(iter(families))
        for pkg in non_conda_packages:
            action = ResolverInstallAction(installer=family, name=pkg.name)
            if is_registry_fallback_action_safe(action):
                plan.installable.append(action)

    def _aggregate_status(
        self, plan: RuntimeDependencyResolutionPlan
    ) -> tuple[str, str | None, str]:
        statuses = [pkg.status for pkg in plan.packages]
        if not statuses:
            return RESOLVER_STATUS_RESOLUTION_UNKNOWN, "dependency_resolution_unknown", "Empty plan."

        # Source spec rejection wins regardless of policy.
        if any(s == PACKAGE_STATUS_UNSUPPORTED_SOURCE_SPEC for s in statuses):
            return (
                RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC,
                RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC]["error_code"],
                "Source-install dependencies are not supported by the resolver.",
            )

        if any(s == PACKAGE_STATUS_RUNTIME_MISSING for s in statuses):
            return (
                RESOLVER_STATUS_RUNTIME_MISSING,
                RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_RUNTIME_MISSING]["error_code"],
                "The selected runtime path could not be resolved.",
            )

        has_conda = any(s == PACKAGE_STATUS_CONDA_INSTALLABLE for s in statuses)
        has_fallback = any(
            pkg.status == PACKAGE_STATUS_FALLBACK_REQUIRED and pkg.fallback_available
            for pkg in plan.packages
        )
        has_unknown = any(
            s in {PACKAGE_STATUS_UNKNOWN, PACKAGE_STATUS_MANUAL_PREPARATION_REQUIRED}
            for s in statuses
        )

        # All packages have a final action: fully installable.
        if plan.installable and not plan.blocked:
            installer_summary = ", ".join(
                sorted({a.installer for a in plan.installable})
            )
            return (
                RESOLVER_STATUS_FULLY_INSTALLABLE,
                None,
                (
                    "All requested packages are installable via the resolver-approved "
                    f"installer set: {installer_summary}."
                ),
            )

        # Mixed installer families: partial.
        if has_conda and has_fallback:
            return (
                RESOLVER_STATUS_PARTIAL_RESOLUTION,
                RESOLVER_STATUS_PARTIAL_RESOLUTION,
                (
                    "Some packages are not installable through the configured conda "
                    "channels; the rest are installable but the resolver does not "
                    "execute a mixed-installer request."
                ),
            )

        # All-fallback: policy-aware.
        if has_fallback and not has_conda:
            policy = getattr(self, "_active_policy", "report_only")
            if fallback_policy_allows(policy) and plan.installable:
                # Single-family safe fallback already accepted.
                return (
                    RESOLVER_STATUS_FULLY_INSTALLABLE,
                    None,
                    "All requested packages are installable via the approved fallback family.",
                )
            return (
                RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS,
                RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS]["error_code"],
                (
                    "Fallback families are available for every package, but the active "
                    "policy does not allow automatic fallback installation."
                ),
            )

        if has_unknown:
            return (
                RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED,
                RESOLVER_TO_P0_FIELDS[RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED]["error_code"],
                "The request cannot proceed automatically; manual preparation is required.",
            )

        return (
            RESOLVER_STATUS_RESOLUTION_UNKNOWN,
            "dependency_resolution_unknown",
            "The resolver could not classify the request.",
        )

    def _recommended_actions(self, plan: RuntimeDependencyResolutionPlan) -> list[str]:
        actions: list[str] = []
        if plan.status == RESOLVER_STATUS_FULLY_INSTALLABLE:
            actions.append("Call install_runtime_dependencies with the original payload; the resolver approved every package.")
            return actions

        if plan.installable:
            actions.append(
                "Do not call install_runtime_dependencies for the blocked subset. If the user wants only the installable subset, submit a narrower explicit request."
            )
        if plan.status == RESOLVER_STATUS_PARTIAL_RESOLUTION:
            actions.append(
                "Ask the user to manually prepare the missing packages or approve a narrower install of just the installable subset."
            )
        elif plan.status == RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS:
            actions.append(
                "Fallback families are available, but the active fallback policy is report_only. Switch the policy to allow_safe_registry_install or proceed with manual preparation."
            )
        elif plan.status == RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED:
            actions.append("Ask the user to prepare the runtime manually before retrying.")
        elif plan.status == RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC:
            actions.append("Replace source-style package specs with registry-backed bare names.")
        elif plan.status == RESOLVER_STATUS_RUNTIME_MISSING:
            actions.append("Verify the selected runtime path before retrying.")
        return actions

    def _probe_runtime(self, runtime: str, ecosystem: str) -> tuple[bool, str]:
        """Return (is_present, message). Never raises."""
        if not runtime:
            return False, "Runtime path is empty."
        settings = _resolve_settings(self)
        try:
            if ecosystem.lower() == "python":
                # Lazy import to keep the resolver independent of the workers module.
                from app.workers.command_worker import CommandTemplateWorkerAdapter

                conda_base, env_path = CommandTemplateWorkerAdapter._resolve_conda_runtime(
                    runtime, settings
                )
                if not env_path.exists():
                    return False, f"Python runtime not found: {env_path}"
                return True, str(env_path)
            from app.workers.command_worker import CommandTemplateWorkerAdapter

            rscript = CommandTemplateWorkerAdapter._resolve_rscript_runtime(
                runtime, settings
            )
            if rscript is None or not rscript.exists():
                return False, f"R runtime not found: {runtime}"
            return True, str(rscript)
        except Exception as exc:  # noqa: BLE001 - the resolver is best-effort.
            return False, f"Could not resolve runtime: {exc}"

    def _resolve_conda_solver(self, ecosystem: str | None = None) -> Path | None:
        """Locate the conda solver from the configured conda base or $PATH."""
        if self._explicit_conda_solver:
            candidate = Path(self._explicit_conda_solver)
            if candidate.exists():
                return candidate
        settings = _resolve_settings(self)
        candidates: list[Path] = []
        # Use the same conda base detection as the worker adapter
        # so the resolver and installer always agree.
        try:
            from app.services.utils import resolve_within
            configured_base = Path(
                getattr(settings, "executor_conda_base", Path.home() / "miniconda3")
            )
            for base in [configured_base, *(
                getattr(settings, "_extra_conda_bases", None) or []
            )]:
                for name in ("mamba", "conda"):
                    for subdir in ("bin", "condabin"):
                        candidate = base / subdir / name
                        if candidate.exists():
                            return candidate
        except Exception:
            pass
        # Fallback: $PATH.
        for name in ("mamba", "conda", "micromamba"):
            which = shutil.which(name)
            if which:
                return Path(which)
        return None

    def _channel_signature(self, conda_bin: Path | None, ecosystem: str, runtime: str) -> str:
        """Stable, content-addressable signature for the probe environment.

        Includes:
        - the resolved conda solver's realpath (so a swap from mamba → mamba
          at a different path is treated as a new channel set);
        - the configured conda base (from settings);
        - the ecosystem casing (``python`` / ``R``);
        - the runtime name (so different runtimes that point at different
          channels get distinct entries);
        - the active fallback policy (so flipping
          ``report_only`` ↔ ``allow_safe_registry_install`` cannot poison a
          cache from the previous policy);
        - the list of channels actually configured for the solver, exposed
          via the resolver settings.
        """
        if conda_bin is None:
            return "no-conda"
        try:
            solver_realpath = str(conda_bin.resolve())
        except OSError:
            solver_realpath = str(conda_bin)
        conda_base = ""
        settings = _resolve_settings(self)
        if settings is not None:
            base = getattr(settings, "executor_conda_base", None)
            if base:
                try:
                    conda_base = str(Path(base).resolve())
                except OSError:
                    conda_base = str(base)
        runtime_str = str(runtime or "")
        policy = getattr(self, "_active_policy", "report_only")
        channels = _configured_channels(conda_bin, settings)
        return "|".join(
            [
                solver_realpath,
                conda_base,
                ecosystem,
                runtime_str,
                policy,
                ",".join(channels) if channels else "",
            ]
        )

    def _probe_conda(
        self,
        conda_bin: Path,
        candidates: list[str],
        *,
        ecosystem: str,
    ) -> str | None:
        """Return the first conda candidate that exists, or None."""
        if not candidates:
            return None
        solver_name = conda_bin.name.lower()
        if solver_name in {"mamba", "micromamba"}:
            result = self._repoquery_search(conda_bin, candidates)
            if result is not None:
                return result
        return self._json_search(conda_bin, candidates)

    def _repoquery_search(self, conda_bin: Path, candidates: list[str]) -> str | None:
        try:
            result = subprocess.run(
                [str(conda_bin), "repoquery", "search", *candidates],
                text=True,
                capture_output=True,
                timeout=self._probe_timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        try:
            import json as _json
            payload = _json.loads(result.stdout or "{}")
        except ValueError:
            return None
        # mamba repoquery returns a dict keyed by the searched name.
        if not isinstance(payload, dict):
            return None
        # Order matters: prefer the first candidate.
        for candidate in candidates:
            entry = payload.get(candidate)
            if not entry:
                continue
            if isinstance(entry, dict) and entry.get("result"):
                return candidate
            if isinstance(entry, list) and entry:
                return candidate
        return None

    def _json_search(self, conda_bin: Path, candidates: list[str]) -> str | None:
        try:
            result = subprocess.run(
                [str(conda_bin), "search", "--json", *candidates],
                text=True,
                capture_output=True,
                timeout=self._probe_timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        try:
            import json as _json
            payload = _json.loads(result.stdout or "{}")
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        for candidate in candidates:
            entry = payload.get(candidate)
            if not entry:
                continue
            if isinstance(entry, dict) and entry.get("result"):
                return candidate
            if isinstance(entry, list) and entry:
                return candidate
        return None


# ---------------------------------------------------------------------------
# Module-level helpers (exported for tests and shared use)
# ---------------------------------------------------------------------------


def _looks_like_source_spec(pkg: str) -> bool:
    lowered = pkg.lower()
    return any(token in lowered for token in SOURCE_SPEC_PATTERNS) or lowered.startswith("git+") or "/" in pkg


# Characters that must not appear in any package name destined for a shelled
# installer invocation.  The set includes classic shell metacharacters plus a
# few extras that are harmless in isolation but risky in a structured command
# builder (e.g. backtick for command substitution, newline for multi-command
# injection).
_SHELL_DANGER_CHARS = re.compile(r"[;&|$`'\"\\()\n\r\0]")
# Editable-install prefix or local-path prefix for pip.
_PIP_EDITABLE_LOCAL_RE = re.compile(r"^(-e\s+|\./|\.\./|~|/)", re.IGNORECASE)


def _contains_shell_danger(name: str) -> bool:
    """True when ``name`` contains shell metacharacters that make it unsafe."""
    if _SHELL_DANGER_CHARS.search(name):
        return True
    if _PIP_EDITABLE_LOCAL_RE.match(name):
        return True
    return False


def _is_valid_grammar(pkg: str, ecosystem: str) -> bool:
    if ecosystem == "python":
        return bool(VERSION_PIN_GRAMMAR.fullmatch(pkg))
    return bool(BARE_NAME_GRAMMAR.fullmatch(pkg))


def _normalize_for_ecosystem(pkg: str, ecosystem: str) -> str:
    return pkg.strip().lower()


def _classify_for_ecosystem(pkg: str, ecosystem: str) -> str:
    if ecosystem.lower() == "r":
        return "r-package"
    return "python-package"


def _fallback_families_for(ecosystem: str) -> list[str]:
    if ecosystem.lower() == "r":
        return list(FALLBACK_FAMILIES_R)
    return list(FALLBACK_FAMILIES_PYTHON)


def _conda_candidates_for(pkg: str, ecosystem: str) -> list[str]:
    """Return the conda-family candidate names to probe for ``pkg``."""
    if ecosystem.lower() == "r":
        lowered = pkg.strip().lower()
        return [f"r-{lowered}", f"bioconductor-{lowered}"]
    base = pkg.strip()
    lowered = base.lower()
    return [base, lowered, lowered.replace("_", "-"), lowered.replace("-", "_")]


def _resolver_settings_stub() -> Any:
    """Return a settings-like object that the worker adapter accepts.

    The resolver only needs to call ``_resolve_conda_runtime`` /
    ``_resolve_rscript_runtime`` for path resolution. We construct a minimal
    duck-typed stub here so the resolver does not depend on the project service
    for the runtime lookup.
    """

    class _Settings:
        executor_conda_base = Path.home() / "miniconda3"
        default_r_runtime = None

    return _Settings()


def _configured_channels(conda_bin: Path, settings: Any | None) -> list[str]:
    """Return the ordered list of channels the solver will actually use.

    The default conda solver auto-discovers channels from
    ``~/.condarc`` / the conda base, but we cannot reliably parse that file
    from a forked backend. We do the next best thing: inspect the conda
    solver's own ``config`` output if available, falling back to an empty
    list (which still produces a stable signature based on the solver
    realpath and ecosystem).
    """
    if conda_bin is None:
        return []
    try:
        result = subprocess.run(
            [str(conda_bin), "config", "--show", "channels", "--json"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0 or not (result.stdout or "").strip():
        return []
    try:
        import json as _json
        payload = _json.loads(result.stdout)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("channels") or []
    if not isinstance(raw, list):
        return []
    # conda sometimes lists defaults at the start; preserve declared order.
    return [str(item) for item in raw if item]


def _resolve_settings(resolver_or_plan: Any) -> Any:
    """Best-effort lookup of the settings object to pass to worker helpers.

    Looks at the plan's ``_settings`` attribute first (set by ``resolve`` when
    called from a manager tool), then at the resolver service's
    ``_settings`` attribute, then falls back to a stub so direct callers
    (unit tests) keep working.
    """
    if hasattr(resolver_or_plan, "_settings"):
        return resolver_or_plan._settings
    return _resolver_settings_stub()


def is_registry_fallback_action_safe(action: ResolverInstallAction | dict[str, Any]) -> bool:
    """True when ``action`` is a safe structured fallback that the installer may execute.

    Safety rules (P1.3 hardening):
    - ``pip``: bare name or ``name==exact_version`` only (no extras ``[]``, no
      editable ``-e``, no local paths, no URLs, no VCS refs, no arbitrary flags).
    - ``cran`` / ``bioconductor``: bare R package name (``^[A-Za-z][A-Za-z0-9.]*$``)
      with no quotes, no semicolons, no callbacks.
    - Always rejected: shell metacharacters (``;``, ``|``, ``$``, ``(``, ``)``,
      backticks, newlines), source-style specs (URLs, GitHub, tarballs), and
      any action whose installer is not one of the three approved families.
    """
    payload: dict[str, Any]
    if isinstance(action, ResolverInstallAction):
        if action.installer not in {"pip", "cran", "bioconductor"}:
            return False
        payload = action.to_dict()
    elif isinstance(action, dict):
        if action.get("installer") not in {"pip", "cran", "bioconductor"}:
            return False
        payload = action
    else:
        return False

    installer = str(payload.get("installer") or "").strip()
    name = str(payload.get("name") or "").strip()
    version_pin = str(payload.get("version_pin") or "").strip() or None

    # Reject empty / obviously dangerous names.
    if not name:
        return False
    if _contains_shell_danger(name):
        return False
    if _looks_like_source_spec(name):
        return False

    if installer == "pip":
        # Must be ``^name$`` or ``^name==version$``. No extras, no editable,
        # no paths, no URLs, no VCS.
        if version_pin is None:
            return bool(VERSION_PIN_GRAMMAR.fullmatch(name))
        candidate = f"{name}=={version_pin}"
        return bool(VERSION_PIN_GRAMMAR.fullmatch(candidate))
    else:
        # cran / bioconductor: bare R package name only, no version pin
        # or extra syntax.
        if version_pin is not None:
            return False
        if payload.get("candidate") is not None:
            return False
        return bool(BARE_NAME_GRAMMAR.fullmatch(name)) and name.strip() != ""


def collect_fallback_actions(
    plan: RuntimeDependencyResolutionPlan,
    *,
    policy: str,
) -> list[ResolverInstallAction]:
    """Translate resolver blocked entries into structured fallback actions.

    - ``report_only`` (default) returns an empty list.
    - ``allow_safe_registry_install`` returns one ``ResolverInstallAction`` per
      blocked entry, filtered by ``is_registry_fallback_action_safe``. The
      caller is responsible for further ecosystem mapping (Python → pip, R →
      cran/bioconductor).
    """
    if policy != "allow_safe_registry_install":
        return []
    actions: list[ResolverInstallAction] = []
    for entry in plan.packages:
        if entry.status != PACKAGE_STATUS_FALLBACK_REQUIRED:
            continue
        for family in entry.fallback_available:
            candidate = ResolverInstallAction(
                installer=family,
                name=entry.name,
            )
            if is_registry_fallback_action_safe(candidate):
                actions.append(candidate)
    return actions


def fallback_policy_allows(policy: str | None) -> bool:
    return policy == "allow_safe_registry_install"


def normalize_fallback_policy(value: str | None) -> str:
    if value in (None, "", "report_only"):
        return "report_only"
    if value == "allow_safe_registry_install":
        return "allow_safe_registry_install"
    return "report_only"


def summarize_blocked(plan: RuntimeDependencyResolutionPlan) -> Iterable[dict[str, Any]]:
    for entry in plan.blocked:
        yield entry.to_dict()
