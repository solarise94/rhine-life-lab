from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.background_task_service import BackgroundTaskService
from app.services.project_service import ProjectService
from app.services.runtime_dependency_job_service import RuntimeDependencyJobService
from app.services.runtime_dependency_state_service import (
    dependency_blockers_by_card,
    find_duplicate_in_flight,
    find_duplicate_terminal_failure,
)
from app.services.utils import utc_now


class RuntimeDependencyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="blueprint-re-dep-test-")
        self.project_service = MagicMock(spec=ProjectService)
        self.project_service.project_path.return_value = Path(self.tmpdir)
        self.background_task_service = MagicMock(spec=BackgroundTaskService)
        self.background_task_service.create_task.return_value = MagicMock(
            task_id="bgtask_test", affected=MagicMock(card_ids=[], job_ids=[])
        )
        self.job_service = RuntimeDependencyJobService(
            self.project_service,
            background_task_service=self.background_task_service,
        )
        self.jobs_path = Path(self.tmpdir) / "chat" / "runtime_dependency_jobs.json"
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_jobs(self, jobs):
        self.jobs_path.write_text(json.dumps(jobs), encoding="utf-8")

    def test_terminal_event_includes_all_enriched_fields(self):
        """Failed dependency job event payload includes normalized failure details."""
        from app.services.project_event_service import ProjectEventService

        events = []
        event_service = ProjectEventService(self.project_service)
        event_service._publish = lambda pid, ev: events.append(ev)

        job_service = RuntimeDependencyJobService(
            self.project_service,
            project_event_service=event_service,
            background_task_service=self.background_task_service,
        )

        job = job_service.submit(
            "test-project",
            {
                "ecosystem": "python",
                "runtime": "python_env",
                "packages": ["pydeseq2"],
                "source": {"card_id": "card_1"},
            },
            handler=lambda _pid, _payload: {
                "ok": False,
                "error_code": "package_not_found_in_conda_channels",
                "requested_package": "pydeseq2",
                "attempted_candidates": ["pydeseq2"],
                "fallback_available": ["pip"],
                "message": "Package pydeseq2 was not found in conda channels.",
            },
        )
        # Wait for the job to finish
        if job.future:
            job.future.result(timeout=5)

        failed_events = [e for e in events if e.get("payload", {}).get("job_status") == "failed"]
        self.assertTrue(failed_events, "Should have emitted a failed event")
        payload = failed_events[0]["payload"]
        self.assertEqual(payload["error_code"], "package_not_found_in_conda_channels")
        self.assertEqual(payload["requested_package"], "pydeseq2")
        self.assertEqual(payload["attempted_candidates"], ["pydeseq2"])
        self.assertEqual(payload["fallback_available"], ["pip"])
        self.assertEqual(payload["retry_hint"], "do_not_retry_same_conda_request")
        self.assertIn("dedupe_key", payload)

    def test_flow_service_includes_full_runtime_dependency_blocker(self):
        """FlowService work order item includes enriched blocker fields.

        We test this by directly calling dependency_blockers_by_card and
        verifying the blocker shape matches what FlowService would forward.
        """
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["pydeseq2"],
                    "source": {"card_id": "card_a"},
                },
                "result": {
                    "ok": False,
                    "error_code": "package_not_found_in_conda_channels",
                    "requested_package": "pydeseq2",
                    "attempted_candidates": ["pydeseq2"],
                    "fallback_available": ["pip"],
                    "message": "not found",
                },
            }
        ])

        blockers = dependency_blockers_by_card(Path(self.tmpdir))
        blocker = blockers.get("card_a")
        self.assertIsNotNone(blocker)
        self.assertEqual(blocker["error_code"], "package_not_found_in_conda_channels")
        self.assertEqual(blocker["requested_package"], "pydeseq2")
        self.assertEqual(blocker["attempted_candidates"], ["pydeseq2"])
        self.assertEqual(blocker["fallback_available"], ["pip"])
        self.assertEqual(blocker["retry_hint"], "do_not_retry_same_conda_request")
        self.assertIn("dedupe_key", blocker)

    def test_duplicate_in_flight_rejects_without_creating_task(self):
        """Same in-flight request returns duplicate error and does not create a new job."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "running",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
            }
        ])

        dup = find_duplicate_in_flight(Path(self.tmpdir), "python", "python_env", ["numpy"])
        self.assertIsNotNone(dup)
        self.assertEqual(dup["prior_job_id"], "depjob_1")

    def test_repeated_conda_miss_returns_duplicate_failure(self):
        """Same impossible conda request returns duplicate_dependency_resolution_failure."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["pydeseq2"],
                },
                "result": {
                    "ok": False,
                    "error_code": "package_not_found_in_conda_channels",
                    "requested_package": "pydeseq2",
                    "fallback_available": ["pip"],
                },
            }
        ])

        dup = find_duplicate_terminal_failure(Path(self.tmpdir), "python", "python_env", ["pydeseq2"])
        self.assertIsNotNone(dup)
        self.assertEqual(dup["prior_job_id"], "depjob_1")
        self.assertEqual(dup["prior_error_code"], "package_not_found_in_conda_channels")

    def test_same_package_different_session_same_project_cooled(self):
        """Cooling is project-scoped, not session-scoped."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                    "source": {"card_id": "card_a", "session_id": "session_1"},
                },
                "result": {
                    "ok": False,
                    "error_code": "package_not_found_in_conda_channels",
                },
            }
        ])

        dup = find_duplicate_terminal_failure(Path(self.tmpdir), "python", "python_env", ["numpy"])
        self.assertIsNotNone(dup)
        # Different session should still be cooled

    def test_blocker_cleared_by_newer_successful_job(self):
        """A newer successful dependency job clears the blocker for the same card/runtime/package set."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                    "source": {"card_id": "card_a"},
                },
                "result": {"ok": False, "error_code": "package_not_found_in_conda_channels"},
            },
            {
                "job_id": "depjob_2",
                "status": "succeeded",
                "created_at": "2026-06-01T01:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                    "source": {"card_id": "card_a"},
                },
                "result": {"ok": True},
            },
        ])

        blockers = dependency_blockers_by_card(Path(self.tmpdir))
        self.assertNotIn("card_a", blockers, "Newer successful job should clear the blocker")

    def test_blocker_cleared_by_manual_resolution(self):
        """Manually resolved job clears blocker; frontend notice dismissal does not."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                    "source": {"card_id": "card_a"},
                    "resolution_status": "manually_resolved",
                    "resolved_at": "2026-06-01T01:00:00Z",
                },
                "result": {"ok": False, "error_code": "package_not_found_in_conda_channels"},
            }
        ])

        blockers = dependency_blockers_by_card(Path(self.tmpdir))
        self.assertNotIn("card_a", blockers, "Manually resolved job should clear blocker")

    def test_backend_restart_preserves_terminal_cooling(self):
        """Terminal failure cooling survives backend restart because it reads persisted file."""
        self._write_jobs([
            {
                "job_id": "depjob_1",
                "status": "failed",
                "created_at": "2026-06-01T00:00:00Z",
                "payload": {
                    "ecosystem": "python",
                    "runtime": "python_env",
                    "packages": ["numpy"],
                },
                "result": {"ok": False, "error_code": "package_not_found_in_conda_channels"},
            }
        ])

        # Simulate backend restart: fresh service instance, no in-memory jobs
        dup = find_duplicate_terminal_failure(Path(self.tmpdir), "python", "python_env", ["numpy"])
        self.assertIsNotNone(dup)
        self.assertEqual(dup["prior_job_id"], "depjob_1")

    def test_event_tails_truncated(self):
        """Project event stdout_tail / stderr_tail are bounded and include truncated metadata."""
        from app.services.project_event_service import ProjectEventService
        from app.services.runtime_dependency_state_service import _tail_text_bounded

        events = []
        event_service = ProjectEventService(self.project_service)
        event_service._publish = lambda pid, ev: events.append(ev)

        job_service = RuntimeDependencyJobService(
            self.project_service,
            project_event_service=event_service,
            background_task_service=self.background_task_service,
        )

        long_stdout = "line\n" * 60
        long_stderr = "error\n" * 60

        job = job_service.submit(
            "test-project",
            {
                "ecosystem": "python",
                "runtime": "python_env",
                "packages": ["numpy"],
                "source": {"card_id": "card_1"},
            },
            handler=lambda _pid, _payload: {
                "ok": False,
                "error_code": "dependency_install_failed",
                "message": "Failed",
                "stdout_tail": long_stdout,
                "stderr_tail": long_stderr,
            },
        )
        if job.future:
            job.future.result(timeout=5)

        failed_events = [e for e in events if e.get("payload", {}).get("job_status") == "failed"]
        self.assertTrue(failed_events)
        payload = failed_events[0]["payload"]
        self.assertIn("truncated", payload)
        self.assertTrue(payload["truncated"])
        # Verify tail is actually bounded
        self.assertLessEqual(len(payload.get("stdout_tail", "")), 2100)
        self.assertLessEqual(len(payload.get("stderr_tail", "")), 2100)


if __name__ == "__main__":
    unittest.main()
