from pathlib import Path
import json

from app.models.cards import Card
from app.models.graph import GraphState
from app.models.patches import GraphPatch
from app.models.runs import Manifest, TaskPacket


SCHEMAS = {
    "graph.schema.json": GraphState,
    "card.schema.json": Card,
    "patch.schema.json": GraphPatch,
    "manifest.schema.json": Manifest,
    "task_packet.schema.json": TaskPacket,
}


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "backend" / "app" / "schemas"
    root.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        path = root / name
        path.write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

