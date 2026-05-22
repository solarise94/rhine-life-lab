#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Blueprint agent output timeline JSONL files.")
    parser.add_argument("timeline", type=Path, help="Path to runs/<run_id>/agent_output_timeline.jsonl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    totals: dict[str, dict[str, float]] = defaultdict(lambda: {"lines": 0, "chars": 0, "gaps": 0.0})
    events = 0
    first_offset: float | None = None
    last_offset: float | None = None
    max_gap = {"seconds": 0.0, "kind": "", "text": ""}

    with args.timeline.open(encoding="utf-8") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            events += 1
            offset = float(event.get("offset_seconds") or 0)
            first_offset = offset if first_offset is None else min(first_offset, offset)
            last_offset = offset if last_offset is None else max(last_offset, offset)
            if event.get("event_type") != "stdout_line":
                continue
            kind = str(event.get("kind") or "unknown")
            char_count = int(event.get("char_count") or 0)
            gap = float(event.get("gap_since_previous_seconds") or 0)
            totals[kind]["lines"] += 1
            totals[kind]["chars"] += char_count
            totals[kind]["gaps"] += gap
            if gap > max_gap["seconds"]:
                max_gap = {"seconds": gap, "kind": kind, "text": str(event.get("text") or "")[:160]}

    total_chars = sum(item["chars"] for item in totals.values()) or 1
    total_lines = sum(item["lines"] for item in totals.values()) or 1
    duration = (last_offset or 0) - (first_offset or 0)
    print(f"Timeline: {args.timeline}")
    print(f"Events: {events}")
    print(f"Visible duration: {duration:.3f}s")
    print()
    print("| Kind | Lines | Line % | Chars | Char % | Pre-line gap seconds |")
    print("|---|---:|---:|---:|---:|---:|")
    for kind, item in sorted(totals.items(), key=lambda pair: (-pair[1]["chars"], pair[0])):
        print(
            f"| {kind} | {int(item['lines'])} | {item['lines'] / total_lines * 100:.1f}% | "
            f"{int(item['chars'])} | {item['chars'] / total_chars * 100:.1f}% | {item['gaps']:.3f} |"
        )
    print()
    print(f"Max gap before stdout: {max_gap['seconds']:.3f}s ({max_gap['kind']})")
    if max_gap["text"]:
        print(f"Next line: {max_gap['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
