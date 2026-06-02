"""Unit tests for the runtime dependency resolver service (P1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.runtime_dependency_resolver_service import (
    PACKAGE_STATUS_CONDA_INSTALLABLE,
    PACKAGE_STATUS_FALLBACK_REQUIRED,
    PACKAGE_STATUS_MANUAL_PREPARATION_REQUIRED,
    PACKAGE_STATUS_RUNTIME_MISSING,
    PACKAGE_STATUS_UNSUPPORTED_SOURCE_SPEC,
    RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS,
    RESOLVER_STATUS_FULLY_INSTALLABLE,
    RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED,
    RESOLVER_STATUS_PARTIAL_RESOLUTION,
    RESOLVER_STATUS_RUNTIME_MISSING,
    RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC,
    RESOLVER_TO_P0_FIELDS,
    RuntimeDependencyResolverService,
    collect_fallback_actions,
    fallback_policy_allows,
    is_registry_fallback_action_safe,
    normalize_fallback_policy,
)


class ResolverServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = RuntimeDependencyResolverService(probe_timeout_seconds=1)

    def test_invalid_ecosystem(self) -> None:
        plan = self.resolver.resolve("proj", {"ecosystem": "ruby", "runtime": "r_env", "packages": ["x"]})
        self.assertEqual(plan.status, "resolution_unknown")
        self.assertEqual(plan.error_code, "dependency_resolution_unknown")

    def test_system_runtime_short_circuits(self) -> None:
        plan = self.resolver.resolve(
            "proj", {"ecosystem": "python", "runtime": "__system__", "packages": ["numpy"]}
        )
        self.assertEqual(plan.status, RESOLVER_STATUS_RUNTIME_MISSING)
        self.assertEqual(plan.error_code, "dependency_install_start_failed")
        self.assertEqual(plan.retry_hint, "manual_runtime_preparation_required")

    def test_empty_packages(self) -> None:
        plan = self.resolver.resolve(
            "proj", {"ecosystem": "python", "runtime": "python_env", "packages": []}
        )
        self.assertEqual(plan.status, RESOLVER_STATUS_MANUAL_PREPARATION_REQUIRED)
        self.assertFalse(plan.ok)

    def test_source_spec_rejected(self) -> None:
        plan = self.resolver.resolve(
            "proj",
            {
                "ecosystem": "python",
                "runtime": "python_env",
                "packages": ["pydeseq2", "https://github.com/foo/bar"],
            },
        )
        self.assertEqual(plan.status, RESOLVER_STATUS_UNSUPPORTED_SOURCE_SPEC)
        self.assertEqual(plan.error_code, "github_source_install_not_supported")
        self.assertFalse(plan.ok)
        # Both packages are inspected; the source one is blocked, the bare one
        # is included as a separate entry.
        names = [pkg.name for pkg in plan.packages]
        self.assertIn("https://github.com/foo/bar", names)
        self.assertIn("pydeseq2", names)

    def test_partial_resolution_classification(self) -> None:
        # Force the runtime probe to succeed and conda probe to find only one
        # of the two packages.
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=None,
        ):
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy", "pydeseq2"],
                },
            )
        # No conda solver means both packages need a fallback, status falls back
        # to "fallback_available_but_policy_disallows" because both are pip-fallback.
        self.assertEqual(plan.status, RESOLVER_STATUS_FALLBACK_AVAILABLE_POLICY_DISALLOWS)
        self.assertEqual(plan.error_code, "package_not_found_in_conda_channels")
        self.assertEqual(plan.retry_hint, "choose_fallback")

    def test_fully_installable_with_conda_solver(self) -> None:
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            return_value="numpy",
        ):
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
            )
        self.assertEqual(plan.status, RESOLVER_STATUS_FULLY_INSTALLABLE)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.installable[0].installer, "conda")
        self.assertEqual(plan.installable[0].candidate, "numpy")
        self.assertEqual(plan.packages[0].status, PACKAGE_STATUS_CONDA_INSTALLABLE)

    def test_partial_with_conda_solver(self) -> None:
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            side_effect=["r-ggplot2", None],
        ):
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "R",
                    "runtime": "R_env",
                    "packages": ["ggplot2", "limma"],
                },
            )
        # ggplot2 is conda-installable, limma is not → mixed plan → partial.
        # P1.3: the plan shows what IS installable, but status blocks the job.
        self.assertEqual(plan.status, RESOLVER_STATUS_PARTIAL_RESOLUTION)
        self.assertFalse(plan.ok)
        self.assertEqual(plan.error_code, RESOLVER_STATUS_PARTIAL_RESOLUTION)
        self.assertEqual(plan.retry_hint, "manual_preparation_required")
        self.assertEqual(len(plan.installable), 1)
        self.assertEqual(plan.installable[0].candidate, "r-ggplot2")
        self.assertEqual(len(plan.blocked), 1)
        self.assertEqual(plan.blocked[0].name, "limma")
        self.assertEqual(plan.blocked[0].reason, "package_not_found_in_conda_channels")
        self.assertIn("bioconductor", plan.blocked[0].fallback_available)

    def test_runtime_missing_blocks_all(self) -> None:
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(False, "Python runtime not found"),
        ):
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "missing_env",
                    "packages": ["numpy", "pandas"],
                },
            )
        self.assertEqual(plan.status, RESOLVER_STATUS_RUNTIME_MISSING)
        self.assertEqual(plan.error_code, "dependency_install_start_failed")
        for pkg in plan.packages:
            self.assertEqual(pkg.status, PACKAGE_STATUS_RUNTIME_MISSING)
        self.assertFalse(plan.installable)

    def test_request_dedupe_key_matches_state_service(self) -> None:
        plan = self.resolver.resolve(
            "proj",
            {
                "ecosystem": "R",
                "runtime": "R_env",
                "packages": ["limma", "ggplot2"],
            },
        )
        self.assertIn("dep:R:R_env", plan.request_dedupe_key)
        self.assertIn("ggplot2,limma", plan.request_dedupe_key)

    def test_resolver_to_p0_fields_mapping(self) -> None:
        # Every request-level status used by the resolver must map to a P0
        # normalized error_code / retry_hint pair.
        for status, mapping in RESOLVER_TO_P0_FIELDS.items():
            self.assertIn("error_code", mapping)
            self.assertIn("retry_hint", mapping)
        # And both fully_installable and runtime_missing should be present.
        self.assertIn(RESOLVER_STATUS_FULLY_INSTALLABLE, RESOLVER_TO_P0_FIELDS)
        self.assertIn(RESOLVER_STATUS_RUNTIME_MISSING, RESOLVER_TO_P0_FIELDS)

    def test_dedupe_repeated_packages(self) -> None:
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            return_value="numpy",
        ):
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy", "NumPy", "numpy"],
                },
            )
        # Only one entry should be inspected.
        self.assertEqual(len(plan.packages), 1)
        self.assertEqual(plan.packages[0].name, "numpy")

    def test_cache_returns_same_match(self) -> None:
        calls: list[str] = []
        cache = self.resolver._cache

        def _probe(conda_bin, candidates, *, ecosystem):
            calls.append(",".join(candidates))
            return candidates[0] if candidates else None

        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            side_effect=_probe,
        ):
            self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
            )
            # Second call should be served from cache, not _probe_conda.
            self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
            )
        self.assertEqual(len(calls), 1)


    def test_invalid_grammar_specs_are_rejected_as_unsupported_source(self) -> None:
        """numpy>=1.0, package[extra], and similar non-bare specs must be unsupported_source_spec."""
        # Avoid subprocess calls from _channel_signature / _configured_channels.
        def _mock_channel_sig(_self, conda_bin, ecosystem, runtime):
            return f"mock:{getattr(conda_bin, 'name', conda_bin)}:{ecosystem}:{runtime}"

        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_channel_signature",
            _mock_channel_sig,
        ):
            # Version comparison (>=) is rejected.
            plan = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy>=1.0"],
                },
            )
            self.assertEqual(plan.status, "unsupported_source_spec")
            self.assertEqual(plan.error_code, "github_source_install_not_supported")
            self.assertEqual(plan.packages[0].status, "unsupported_source_spec")

            # Extras syntax is rejected.
            plan2 = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["package[extra]"],
                },
            )
            self.assertEqual(plan2.status, "unsupported_source_spec")
            self.assertEqual(plan2.packages[0].status, "unsupported_source_spec")

            # Bare name is fine.
            plan3 = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
            )
            self.assertNotEqual(plan3.status, "unsupported_source_spec")

            # Exact version pin is fine.
            plan4 = self.resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy==1.26.4"],
                },
            )
            self.assertNotEqual(plan4.status, "unsupported_source_spec")


class ResolverFallbackPolicyTest(unittest.TestCase):
    def test_normalize_policy(self) -> None:
        self.assertEqual(normalize_fallback_policy(None), "report_only")
        self.assertEqual(normalize_fallback_policy(""), "report_only")
        self.assertEqual(normalize_fallback_policy("report_only"), "report_only")
        self.assertEqual(
            normalize_fallback_policy("allow_safe_registry_install"),
            "allow_safe_registry_install",
        )
        self.assertEqual(normalize_fallback_policy("unknown"), "report_only")

    def test_fallback_policy_allows(self) -> None:
        self.assertFalse(fallback_policy_allows(None))
        self.assertFalse(fallback_policy_allows("report_only"))
        self.assertTrue(fallback_policy_allows("allow_safe_registry_install"))

    def test_collect_fallback_actions_report_only(self) -> None:
        resolver = RuntimeDependencyResolverService()
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            return_value=None,
        ):
            plan = resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["pydeseq2"],
                },
            )
        self.assertEqual(plan.packages[0].status, PACKAGE_STATUS_FALLBACK_REQUIRED)
        self.assertEqual(collect_fallback_actions(plan, policy="report_only"), [])

    def test_collect_fallback_actions_allow_safe_registry(self) -> None:
        resolver = RuntimeDependencyResolverService()
        with patch.object(
            RuntimeDependencyResolverService,
            "_probe_runtime",
            return_value=(True, "/tmp/env"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_resolve_conda_solver",
            return_value=Path("/usr/bin/mamba"),
        ), patch.object(
            RuntimeDependencyResolverService,
            "_probe_conda",
            return_value=None,
        ):
            plan = resolver.resolve(
                "proj",
                {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["pydeseq2"],
                },
            )
        actions = collect_fallback_actions(plan, policy="allow_safe_registry_install")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].installer, "pip")
        self.assertEqual(actions[0].name, "pydeseq2")
        self.assertTrue(is_registry_fallback_action_safe(actions[0]))

    def test_is_registry_fallback_action_safe_rejects_shell_chars(self) -> None:
        # A name with a shell metacharacter must not be allowed even under
        # the relaxed policy.
        self.assertFalse(is_registry_fallback_action_safe({
            "installer": "pip",
            "name": "numpy; rm -rf /",
        }))
        self.assertFalse(is_registry_fallback_action_safe({
            "installer": "pip",
            "name": "https://github.com/x/y",
        }))
        self.assertFalse(is_registry_fallback_action_safe({
            "installer": "conda",
            "name": "numpy",
        }))


if __name__ == "__main__":
    unittest.main()
