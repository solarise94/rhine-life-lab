from __future__ import annotations

import csv
import mimetypes
from pathlib import Path

from app.models.output_contracts import ArtifactClass, normalize_output_format


FORMAT_TO_CLASS: dict[str, ArtifactClass] = {
    "svg": "figure",
    "png": "figure",
    "jpg": "figure",
    "jpeg": "figure",
    "gif": "figure",
    "webp": "figure",
    "pdf": "figure",
    "csv": "table",
    "tsv": "table",
    "xlsx": "table",
    "parquet": "table",
    "md": "document",
    "markdown": "document",
    "html": "document",
    "htm": "document",
    "txt": "document",
    "rds": "model",
    "rdata": "model",
    "h5ad": "model",
    "h5seurat": "model",
    "pkl": "model",
    "pickle": "model",
    "zip": "archive",
    "tar": "archive",
    "tar.gz": "archive",
    "tgz": "archive",
    "bin": "binary",
}

DEFAULT_CLASS_FORMAT: dict[ArtifactClass, str] = {
    "figure": "svg",
    "table": "tsv",
    "document": "md",
    "model": "rds",
    "archive": "zip",
    "binary": "bin",
}


def detect_artifact_format(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return "tar.gz"
    suffix = normalize_output_format(path.suffix)
    if suffix:
        return suffix
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime == "image/svg+xml":
        return "svg"
    if mime == "application/pdf":
        return "pdf"
    header = path.read_bytes()[:8192]
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header.startswith(b"PK\x03\x04"):
        return "zip"
    if header.startswith(b"\x89HDF\r\n\x1a\n"):
        return "h5ad"
    text = _decode_text(header)
    if text is None:
        return None
    lower = text.lower()
    if "<svg" in lower:
        return "svg"
    if "<html" in lower or "<!doctype html" in lower:
        return "html"
    table_format = _detect_table_format(path)
    if table_format:
        return table_format
    if lower.strip().startswith(("#", "-", "*")):
        return "md"
    return "txt"


def artifact_class_for_format(output_format: str | None) -> ArtifactClass | None:
    if output_format is None:
        return None
    return FORMAT_TO_CLASS.get(normalize_output_format(output_format))


def detect_artifact_class(path: Path) -> ArtifactClass | None:
    return artifact_class_for_format(detect_artifact_format(path))


def default_format_for_artifact_class(artifact_class: ArtifactClass) -> str:
    return DEFAULT_CLASS_FORMAT[artifact_class]


def _decode_text(chunk: bytes) -> str | None:
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _detect_table_format(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            sample = handle.read(4096)
    except UnicodeDecodeError:
        return None
    if "\t" in sample:
        return "tsv"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return None
    if dialect.delimiter == "\t":
        return "tsv"
    if dialect.delimiter in {",", ";"}:
        return "csv"
    return None
