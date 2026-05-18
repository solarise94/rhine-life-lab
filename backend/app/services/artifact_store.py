from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil

from app.core.config import get_settings
from app.models.artifacts import ArtifactPointer


class ArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.settings = get_settings()
        self.store_root = project_root / "artifact_store" / "sha256"
        self.pointer_root = project_root / "artifacts" / "pointers"

    def register_candidate(self, relative_path: str, asset_type: str) -> ArtifactPointer | None:
        source = self.project_root / relative_path
        if not source.exists():
            return None
        threshold = self.settings.artifact_size_threshold_mb * 1024 * 1024
        if source.stat().st_size < threshold and source.suffix not in {".h5ad", ".bam", ".fastq", ".fq", ".cram"}:
            return None
        digest = self.compute_full_hash(source)
        ext = source.suffix
        bucket = digest[:2]
        target = self.store_root / bucket / f"{digest}{ext}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        pointer = ArtifactPointer(
            artifact_id=f"art_{digest[:12]}",
            logical_name=source.stem,
            asset_type=asset_type,
            format=source.suffix.lstrip("."),
            hash={"algo": "sha256", "value": digest},
            quick_fingerprint={"sha256_prefix": digest[:16]},
            size_bytes=source.stat().st_size,
            local_path=str(target.relative_to(self.project_root)),
        )
        self.pointer_root.mkdir(parents=True, exist_ok=True)
        (self.pointer_root / f"{pointer.artifact_id}.json").write_text(
            pointer.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return pointer

    @staticmethod
    def compute_full_hash(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
