from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ArtifactClass = Literal["figure", "table", "document", "model", "archive", "binary"]


def normalize_output_format(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("."):
        normalized = normalized[1:]
    return normalized


class OutputContractBase(BaseModel):
    role: str
    label: str
    artifact_class: ArtifactClass
    accepted_formats: list[str] = Field(default_factory=list)
    preferred_format: str | None = None
    required: bool = True
    description: str | None = None

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        if not normalized:
            raise ValueError("output role is required.")
        return normalized

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("output label is required.")
        return normalized

    @field_validator("accepted_formats")
    @classmethod
    def _normalize_accepted_formats(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values or []:
            item = normalize_output_format(value)
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("preferred_format")
    @classmethod
    def _normalize_preferred_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_output_format(value)
        return normalized or None

    @model_validator(mode="after")
    def _validate_preferred_format(self) -> "OutputContractBase":
        if self.preferred_format is None:
            return self
        if self.accepted_formats and self.preferred_format not in self.accepted_formats:
            raise ValueError("preferred_format must be one of accepted_formats.")
        return self


class CardOutputSpec(OutputContractBase):
    asset_id: str | None = None
    status: str | None = None


class TaskOutputSpec(OutputContractBase):
    path_hint: str
    asset_id: str | None = None
    status: str | None = None

    @field_validator("path_hint")
    @classmethod
    def _validate_path_hint(cls, value: str) -> str:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized:
            raise ValueError("path_hint is required.")
        return normalized

    def allowed_path_hints(self) -> list[str]:
        base, _, _suffix = self.path_hint.rpartition(".")
        stem = base if base else self.path_hint
        hints = [self.path_hint]
        for output_format in self.accepted_formats:
            candidate = f"{stem}.{output_format}"
            if candidate not in hints:
                hints.append(candidate)
        return hints


class CreatedAssetRecord(BaseModel):
    role: str
    path: str
    label: str | None = None
    asset_id: str | None = None
    description: str | None = None
    artifact_class: ArtifactClass | None = None
    format: str | None = None

    @field_validator("role")
    @classmethod
    def _validate_created_role(cls, value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
        if not normalized:
            raise ValueError("created asset role is required.")
        return normalized

    @field_validator("path")
    @classmethod
    def _validate_created_path(cls, value: str) -> str:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized:
            raise ValueError("created asset path is required.")
        return normalized

    @field_validator("format")
    @classmethod
    def _normalize_created_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_output_format(value)
        return normalized or None
