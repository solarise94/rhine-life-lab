"""Doc 48: Upload integrity remediation tests.

Covers staging write, exception-safe cleanup, project-lock serialization,
single-file persistence, and startup reconcile.
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
import threading
import unittest
from io import BytesIO
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import UploadFile
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.models.graph import Asset, GraphState
from app.services.graph_store import GraphStore
from app.api.chat import upload_chat_file
from app.services.project_file_service import (
    ORPHAN_FINAL_GRACE_SECONDS,
    ORPHAN_PART_GRACE_SECONDS,
    ProjectFileService,
)
from app.services.project_service import ProjectService


class ChatUploadRemediationTest(unittest.TestCase):
    """Integration tests for upload route integrity remediation."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="bp-upload-test-")
        self.settings = get_settings()
        self._original_data_root = self.settings.data_root
        self.settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="upload-test",
            name="Upload Test",
            current_goal="test uploads",
            seed_demo=False,
        )
        self.client = TestClient(app)
        self.project_root = self.project_service.project_path("upload-test")

    def tearDown(self) -> None:
        self.settings.data_root = self._original_data_root
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _uploads_dir(self) -> Path:
        return self.project_root / "data" / "uploads"

    def _assets(self) -> list[Asset]:
        store = self.project_service.graph_store("upload-test")
        return store.load_assets()

    # ------------------------------------------------------------------
    # 1. Happy path
    # ------------------------------------------------------------------

    def test_upload_creates_asset_and_file(self):
        response = self.client.post(
            "/api/projects/upload-test/chat-uploads",
            files={"file": ("hello.txt", b"hello world", "text/plain")},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["asset"]["title"], "hello.txt")

        assets = self._assets()
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].title, "hello.txt")

        upload_path = self.project_root / assets[0].path
        self.assertTrue(upload_path.exists())
        self.assertEqual(upload_path.read_bytes(), b"hello world")

        # No .part residue
        self.assertEqual(list(self._uploads_dir().glob("*.part")), [])

    # ------------------------------------------------------------------
    # 2. Staging write failure cleans .part
    # ------------------------------------------------------------------

    def test_part_cleaned_on_read_failure(self):
        """Simulate disconnect during staging write: .part removed, no final file."""
        from app.api.chat import upload_chat_file

        broken_file = MagicMock(spec=UploadFile)
        broken_file.filename = "broken.txt"
        broken_file.content_type = "text/plain"
        broken_file.read = MagicMock(side_effect=ConnectionResetError("client disconnect"))
        broken_file.close = AsyncMock(return_value=None)

        with self.assertRaises(ConnectionResetError):
            import asyncio
            asyncio.run(upload_chat_file("upload-test", broken_file, self.project_service))

        self.assertEqual(list(self._uploads_dir().glob("*.part")), [])
        # No final files either
        self.assertEqual(list(self._uploads_dir().glob("*.txt")), [])

    # ------------------------------------------------------------------
    # 3. save_assets failure leaves orphan for reconcile
    # ------------------------------------------------------------------

    def test_save_assets_failure_leaves_orphan_for_reconcile(self):
        """save_assets failure after replace(): final file is kept as an orphan
        and cleaned up by reconcile_project_uploads(). Unlinking it inline
        would risk the forbidden state if save_assets actually committed but
        still raised.
        """
        from app.services.project_file_service import ProjectFileService

        store = self.project_service.graph_store("upload-test")

        def fail_before_commit(assets):
            raise RuntimeError("disk full")

        with patch.object(self.project_service, "graph_store", return_value=store):
            with patch.object(store, "save_assets", side_effect=fail_before_commit):
                async def _read_chunk(*_a, **_k):
                    if not _read_chunk._done:
                        _read_chunk._done = True
                        return b"content"
                    return b""
                _read_chunk._done = False

                broken_file = MagicMock(spec=UploadFile)
                broken_file.filename = "savefail.txt"
                broken_file.content_type = "text/plain"
                broken_file.read = _read_chunk
                broken_file.close = AsyncMock(return_value=None)

                with self.assertRaises(RuntimeError):
                    import asyncio
                    asyncio.run(upload_chat_file("upload-test", broken_file, self.project_service))

        # No asset registered (save_assets raised before persistence).
        self.assertEqual(self._assets(), [])
        # No .part residue (finally cleaned it up).
        self.assertEqual(list(self._uploads_dir().glob("*.part")), [])
        # Final file survives as an orphan.
        orphans = list(self._uploads_dir().glob("*.txt"))
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0].read_bytes(), b"content")

        # Reconcile closes the loop. Make the orphan look stale so grace passes.
        old_time = time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1
        os.utime(orphans[0], (old_time, old_time))
        pfs = ProjectFileService(self.project_service)
        result = pfs.reconcile_project_uploads("upload-test")
        self.assertFalse(orphans[0].exists())
        self.assertEqual(result["removed"], [orphans[0].name])

    # ------------------------------------------------------------------
    # 4. Post-persist failure does NOT delete final file
    # ------------------------------------------------------------------

    def test_final_file_preserved_after_persist(self):
        """Simulate failure after save_assets has returned and assets_persisted is true.

        A custom context manager raises on __exit__ so the exception fires *inside*
        the try/except block after assets_persisted has become True.
        """
        store = self.project_service.graph_store("upload-test")

        class PostPersistError(Exception):
            pass

        class FailingLock:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                raise PostPersistError("post-persist fault")

        with patch.object(self.project_service, "graph_store", return_value=store):
            with patch.object(self.project_service, "lock_for", return_value=FailingLock()):
                async def _read_chunk(*_a, **_k):
                    if not _read_chunk._done:
                        _read_chunk._done = True
                        return b"content"
                    return b""
                _read_chunk._done = False

                broken_file = MagicMock(spec=UploadFile)
                broken_file.filename = "postfail.txt"
                broken_file.content_type = "text/plain"
                broken_file.read = _read_chunk
                broken_file.close = AsyncMock(return_value=None)

                with self.assertRaises(PostPersistError):
                    import asyncio
                    asyncio.run(upload_chat_file("upload-test", broken_file, self.project_service))

        # Final file must survive because save_assets already committed.
        txt_files = list(self._uploads_dir().glob("*.txt"))
        self.assertEqual(len(txt_files), 1)
        self.assertEqual(txt_files[0].read_bytes(), b"content")

        # Asset should also be persisted (save_assets wrote before raising).
        assets = self._assets()
        self.assertTrue(any(a.title == "postfail.txt" for a in assets))

    # ------------------------------------------------------------------
    # 5. Asset id collision returns 409
    # ------------------------------------------------------------------

    def test_asset_id_collision_returns_409(self):
        # Pin timestamp so asset_id is deterministic across requests.
        with patch("app.api.chat.utc_now", return_value="20260606T120000Z"):
            # First upload succeeds
            response1 = self.client.post(
                "/api/projects/upload-test/chat-uploads",
                files={"file": ("dup.txt", b"first", "text/plain")},
            )
            self.assertEqual(response1.status_code, 200)
            asset_id = response1.json()["asset"]["asset_id"]
            first_asset_path = self.project_root / self._assets()[0].path
            self.assertTrue(first_asset_path.exists())
            self.assertEqual(first_asset_path.read_bytes(), b"first")

            # Patch uuid4 to force identical asset_id.
            with patch("app.api.chat.uuid4") as mock_uuid:
                mock_uuid.return_value.hex = asset_id.split("_")[-2]
                response2 = self.client.post(
                    "/api/projects/upload-test/chat-uploads",
                    files={"file": ("dup.txt", b"second", "text/plain")},
                )
                self.assertEqual(response2.status_code, 409)

        # The original upload file must still exist and be untouched.
        self.assertTrue(first_asset_path.exists())
        self.assertEqual(first_asset_path.read_bytes(), b"first")

    # ------------------------------------------------------------------
    # 6. Concurrent uploads without lock lose update
    # ------------------------------------------------------------------

    def test_concurrent_uploads_without_lock_lose_update(self):
        """Without project lock, concurrent uploads cause lost update.

        Two threads each run upload_chat_file() with their own UploadFile
        mock, sharing one GraphStore instance. A barrier after load_graph
        forces both threads to read the same (empty) graph before either
        saves, reproducing the stale-read race.
        """
        store = self.project_service.graph_store("upload-test")
        original_load = store.load_graph

        barrier = Barrier(2)

        def synced_load():
            result = original_load()
            barrier.wait(timeout=5)
            return result

        dummy_lock = MagicMock()
        dummy_lock.__enter__ = MagicMock(return_value=None)
        dummy_lock.__exit__ = MagicMock(return_value=None)

        errors: list[BaseException] = []

        def run_upload(idx: int) -> None:
            try:
                with patch.object(self.project_service, "graph_store", return_value=store):
                    with patch.object(store, "load_graph", side_effect=synced_load):
                        with patch.object(self.project_service, "lock_for", return_value=dummy_lock):
                            async def _read_chunk(*_a, **_k):
                                if not _read_chunk._done:
                                    _read_chunk._done = True
                                    return b"data"
                                return b""
                            _read_chunk._done = False

                            file = MagicMock(spec=UploadFile)
                            file.filename = f"concurrent_{idx}.txt"
                            file.content_type = "text/plain"
                            file.read = _read_chunk
                            file.close = AsyncMock(return_value=None)

                            asyncio.run(upload_chat_file("upload-test", file, self.project_service))
            except BaseException as exc:
                errors.append(exc)

        t1 = Thread(target=run_upload, args=(0,))
        t2 = Thread(target=run_upload, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertFalse(t1.is_alive(), "thread 1 did not finish (barrier or patch deadlock)")
        self.assertFalse(t2.is_alive(), "thread 2 did not finish (barrier or patch deadlock)")
        self.assertEqual(errors, [], f"Thread raised during upload: {errors}")

        # Without a real lock both coroutines read the same (empty) graph,
        # append their asset, then save — the second save overwrites the first.
        assets = self._assets()
        self.assertEqual(len(assets), 1, "Expected lost update without lock")

    # ------------------------------------------------------------------
    # 6b. Concurrent uploads with real lock preserve both
    # ------------------------------------------------------------------

    def test_concurrent_uploads_with_lock_preserve_both(self):
        """With real project lock, concurrent uploads both survive.

        Calls upload_chat_file() directly from each thread (not via
        TestClient, which is not safe to drive concurrently) so the only
        serialization comes from project_service.lock_for().
        """
        errors: list[BaseException] = []

        def do_upload(idx: int) -> None:
            try:
                async def _read_chunk(*_a, **_k):
                    if not _read_chunk._done:
                        _read_chunk._done = True
                        return b"data"
                    return b""
                _read_chunk._done = False

                file = MagicMock(spec=UploadFile)
                file.filename = f"concurrent_{idx}.txt"
                file.content_type = "text/plain"
                file.read = _read_chunk
                file.close = AsyncMock(return_value=None)

                asyncio.run(upload_chat_file("upload-test", file, self.project_service))
            except BaseException as exc:
                errors.append(exc)

        t1 = Thread(target=do_upload, args=(0,))
        t2 = Thread(target=do_upload, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertFalse(t1.is_alive(), "thread 1 did not finish")
        self.assertFalse(t2.is_alive(), "thread 2 did not finish")
        self.assertEqual(errors, [], f"Thread raised during upload: {errors}")

        assets = self._assets()
        titles = {a.title for a in assets}
        self.assertIn("concurrent_0.txt", titles)
        self.assertIn("concurrent_1.txt", titles)


class ReconcileProjectUploadsTest(unittest.TestCase):
    """Tests for ProjectFileService.reconcile_project_uploads."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="bp-reconcile-test-")
        self.settings = get_settings()
        self._original_data_root = self.settings.data_root
        self.settings.data_root = Path(self.tmpdir)
        self.project_service = ProjectService()
        self.project_service.create_project(
            project_id="reconcile-test",
            name="Reconcile Test",
            current_goal="test reconcile",
            seed_demo=False,
        )
        self.pfs = ProjectFileService(self.project_service)
        self.project_root = self.project_service.project_path("reconcile-test")
        self.uploads_dir = self.project_root / "data" / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.settings.data_root = self._original_data_root
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_asset(self, filename: str, asset_id: str) -> str:
        """Create a registered session-upload asset and its file."""
        relative_path = f"data/uploads/{filename}"
        path = self.project_root / relative_path
        path.write_bytes(b"registered")

        store = self.project_service.graph_store("reconcile-test")
        graph = store.load_graph()
        asset = Asset(
            asset_id=asset_id,
            asset_type="uploaded_file",
            title=filename,
            status="candidate",
            path=relative_path,
            summary="test",
            metadata={"source": "manager_chat_upload"},
        )
        graph.assets.append(asset)
        store.save_assets(graph.assets)
        return relative_path

    # ------------------------------------------------------------------
    # 7. Stale .part removed
    # ------------------------------------------------------------------

    def test_reconcile_removes_stale_part(self):
        part = self.uploads_dir / "orphan.txt.part"
        part.write_bytes(b"partial")
        # Make it very old
        old_time = time.time() - ORPHAN_PART_GRACE_SECONDS - 1
        os.utime(part, (old_time, old_time))

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertIn("orphan.txt.part", result["removed"])
        self.assertFalse(part.exists())

    # ------------------------------------------------------------------
    # 8. Fresh .part kept
    # ------------------------------------------------------------------

    def test_reconcile_keeps_fresh_part(self):
        part = self.uploads_dir / "fresh.txt.part"
        part.write_bytes(b"partial")
        # Very recent
        os.utime(part, (time.time(), time.time()))

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertNotIn("fresh.txt.part", result["removed"])
        self.assertTrue(part.exists())

    # ------------------------------------------------------------------
    # 9. Stale unregistered final file removed
    # ------------------------------------------------------------------

    def test_reconcile_removes_stale_orphan_final(self):
        orphan = self.uploads_dir / "orphan_final.txt"
        orphan.write_bytes(b"orphan")
        old_time = time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1
        os.utime(orphan, (old_time, old_time))

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertIn("orphan_final.txt", result["removed"])
        self.assertFalse(orphan.exists())

    # ------------------------------------------------------------------
    # 10. Registered upload survives reconcile
    # ------------------------------------------------------------------

    def test_reconcile_keeps_registered_upload(self):
        self._write_asset("registered.txt", "upload_registered_001")
        # Make it very old so grace does not matter
        path = self.project_root / "data/uploads/registered.txt"
        old_time = time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1
        os.utime(path, (old_time, old_time))

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertNotIn("registered.txt", result["removed"])
        self.assertTrue(path.exists())

    # ------------------------------------------------------------------
    # 11. Broken graph: .part removed, final files preserved
    # ------------------------------------------------------------------

    def test_reconcile_broken_graph_removes_part_keeps_finals(self):
        part = self.uploads_dir / "stale.txt.part"
        part.write_bytes(b"partial")
        os.utime(part, (time.time() - ORPHAN_PART_GRACE_SECONDS - 1, time.time() - ORPHAN_PART_GRACE_SECONDS - 1))

        orphan = self.uploads_dir / "orphan.txt"
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1, time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1))

        # Corrupt assets.json so graph load fails
        assets_path = self.project_root / "graph" / "assets.json"
        assets_path.write_text("not-json{{{")

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertIn("stale.txt.part", result["removed"])
        self.assertNotIn("orphan.txt", result["removed"])
        self.assertFalse(part.exists())
        self.assertTrue(orphan.exists())
        self.assertGreater(result["errors"], 0)

    # ------------------------------------------------------------------
    # 11b. Registered upload reached via symlink survives reconcile
    # ------------------------------------------------------------------

    def test_reconcile_keeps_registered_upload_reached_via_symlink(self):
        """Relative-path comparison: symlinked root does not cause reconcile
        to misclassify a registered upload as an orphan.
        """
        self._write_asset("sym_registered.txt", "upload_sym_001")
        # Make it old so grace is not the reason it survives.
        path = self.project_root / "data/uploads/sym_registered.txt"
        old_time = time.time() - ORPHAN_FINAL_GRACE_SECONDS - 1
        os.utime(path, (old_time, old_time))

        # Reach the same project through a symlinked data_root.
        symlink_root = Path(self.tmpdir) / "data_root_via_symlink"
        symlink_root.symlink_to(Path(self.tmpdir))

        sym_settings = get_settings()
        original_data_root = sym_settings.data_root
        try:
            sym_settings.data_root = symlink_root
            sym_project_service = ProjectService()
            sym_pfs = ProjectFileService(sym_project_service)
            result = sym_pfs.reconcile_project_uploads("reconcile-test")
        finally:
            sym_settings.data_root = original_data_root

        self.assertNotIn("sym_registered.txt", result["removed"])
        self.assertTrue(path.exists())

    # ------------------------------------------------------------------
    # 12. Fresh orphan final kept (grace window)
    # ------------------------------------------------------------------

    def test_reconcile_keeps_fresh_orphan_final(self):
        orphan = self.uploads_dir / "fresh_orphan.txt"
        orphan.write_bytes(b"orphan")
        os.utime(orphan, (time.time(), time.time()))

        result = self.pfs.reconcile_project_uploads("reconcile-test")
        self.assertNotIn("fresh_orphan.txt", result["removed"])
        self.assertTrue(orphan.exists())


if __name__ == "__main__":
    unittest.main()
