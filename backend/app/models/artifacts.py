from __future__ import annotations

from pydantic import BaseModel, Field


class ArtifactPointer(BaseModel):
    artifact_id: str
    logical_name: str
    asset_type: str
    format: str
    hash: dict[str, str]
    quick_fingerprint: dict[str, str] = Field(default_factory=dict)
    size_bytes: int
    local_path: str
    remote_uri: str | None = None

