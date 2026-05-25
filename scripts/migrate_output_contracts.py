from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any


ARTIFACT_CLASSES = {"figure", "table", "document", "model", "archive", "binary"}
FORMAT_TO_CLASS = {
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
    "zip": "archive",
    "tar": "archive",
    "tar.gz": "archive",
    "tgz": "archive",
    "bin": "binary",
}


def normalize_format(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("."):
        normalized = normalized[1:]
    return normalized or None


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "output"


def role_slug(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    slug = slugify(text)
    if slug == "output":
        return None
    if slug.startswith("asset_"):
        slug = slug[len("asset_") :] or slug
    return slug


def detect_format_from_path(path: str | None) -> str | None:
    value = str(path or "").strip().lower()
    if not value:
        return None
    if value.endswith(".tar.gz"):
        return "tar.gz"
    suffix = Path(value).suffix.lower().lstrip(".")
    return suffix or None


def infer_artifact_class(label: str, asset: dict[str, Any] | None, detected_format: str | None) -> str:
    asset_type = str((asset or {}).get("asset_type") or "").strip().lower()
    if asset_type in ARTIFACT_CLASSES:
        return asset_type
    if detected_format and detected_format in FORMAT_TO_CLASS:
        return FORMAT_TO_CLASS[detected_format]
    normalized = label.lower()
    if any(token in normalized for token in ["plot", "heatmap", "bubble", "figure", "图", "热图", "气泡图", "散点图"]):
        return "figure"
    if any(token in normalized for token in ["report", "summary", "brief", "报告", "总结"]):
        return "document"
    if any(token in normalized for token in ["rds", "h5ad", "model", "seurat"]):
        return "model"
    if any(token in normalized for token in ["zip", "archive", "bundle", "压缩包"]):
        return "archive"
    return "table"


def migrate_output(
    output: dict[str, Any],
    asset_by_id: dict[str, dict[str, Any]],
    used_roles: set[str],
) -> tuple[dict[str, Any], bool]:
    changed = False
    migrated = dict(output)
    if "allowed_formats" in migrated and "accepted_formats" not in migrated:
        migrated["accepted_formats"] = migrated.pop("allowed_formats")
        changed = True

    label = str(migrated.get("label") or "").strip()
    asset_id = migrated.get("asset_id")
    asset = asset_by_id.get(str(asset_id)) if asset_id else None
    detected_format = normalize_format(
        migrated.get("preferred_format")
        or next(iter(migrated.get("accepted_formats") or []), None)
        or detect_format_from_path((asset or {}).get("path"))
    )

    current_role = role_slug(migrated.get("role"))
    if current_role is None:
        role_candidate = (
            role_slug((asset or {}).get("metadata", {}).get("role"))
            or role_slug((asset or {}).get("metadata", {}).get("planned_asset_id"))
            or role_slug(asset_id)
            or role_slug(label)
            or "output"
        )
        base_role = role_candidate
        suffix = 2
        while role_candidate in used_roles:
            role_candidate = f"{base_role}_{suffix}"
            suffix += 1
        migrated["role"] = role_candidate
        changed = True
    used_roles.add(str(migrated["role"]))

    artifact_class = migrated.get("artifact_class")
    if artifact_class not in ARTIFACT_CLASSES:
        migrated["artifact_class"] = infer_artifact_class(label or migrated["role"], asset, detected_format)
        changed = True

    formats = []
    for value in migrated.get("accepted_formats") or []:
        normalized = normalize_format(value)
        if normalized and normalized not in formats:
            formats.append(normalized)
    if not formats and detected_format:
        formats = [detected_format]
        changed = True
    migrated["accepted_formats"] = formats

    preferred_format = normalize_format(migrated.get("preferred_format"))
    if preferred_format and formats and preferred_format not in formats:
        formats.append(preferred_format)
        migrated["accepted_formats"] = formats
        changed = True
    if not preferred_format and formats:
        migrated["preferred_format"] = formats[0]
        changed = True
    else:
        migrated["preferred_format"] = preferred_format

    migrated["label"] = label or migrated["role"]
    migrated.setdefault("required", True)
    return migrated, changed


def migrate_project(project_root: Path) -> bool:
    cards_path = project_root / "graph" / "cards.json"
    graph_path = project_root / "graph" / "graph.json"
    assets_path = project_root / "graph" / "assets.json"
    if not cards_path.exists() or not assets_path.exists():
        return False
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    assets = json.loads(assets_path.read_text(encoding="utf-8"))
    asset_by_id = {str(item.get("asset_id")): item for item in assets if item.get("asset_id")}
    changed = False
    for card in cards:
        outputs = []
        used_roles: set[str] = set()
        for output in card.get("outputs") or []:
            if not isinstance(output, dict):
                outputs.append(output)
                continue
            migrated, output_changed = migrate_output(output, asset_by_id, used_roles)
            outputs.append(migrated)
            changed = changed or output_changed
        if outputs != card.get("outputs"):
            card["outputs"] = outputs
            changed = True
    if not changed:
        return False
    cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if graph_path.exists():
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["output_contracts_schema"] = "v2"
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def iter_projects(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("_"))


def main(argv: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root / "workspace"
    targets = [Path(arg).resolve() for arg in argv[1:]] if len(argv) > 1 else iter_projects(workspace_root)
    updated = 0
    for project_root in targets:
        if migrate_project(project_root):
            updated += 1
            print(f"migrated {project_root}")
    print(f"updated_projects={updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
