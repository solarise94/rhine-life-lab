from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any


def iter_sse_payloads(chunks: Iterable[bytes], *, invalid_json_message: str) -> Iterator[dict[str, Any]]:
    buffer = ""
    for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while True:
            boundary = _sse_boundary(buffer)
            if boundary is None:
                break
            raw_event = buffer[: boundary[0]]
            buffer = buffer[boundary[1] :]
            payload = parse_sse_payload(raw_event, invalid_json_message=invalid_json_message)
            if payload is not None:
                yield payload
    if buffer.strip():
        payload = parse_sse_payload(buffer, invalid_json_message=invalid_json_message)
        if payload is not None:
            yield payload


def parse_sse_payload(raw_event: str, *, invalid_json_message: str) -> dict[str, Any] | None:
    lines = []
    for line in raw_event.splitlines():
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
    if not lines:
        return None
    payload = "\n".join(lines).strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{invalid_json_message}: {exc}") from exc
    return parsed if isinstance(parsed, dict) else None


def _sse_boundary(buffer: str) -> tuple[int, int] | None:
    boundaries = [(index, index + 2) for index in [buffer.find("\n\n")] if index >= 0]
    crlf_index = buffer.find("\r\n\r\n")
    if crlf_index >= 0:
        boundaries.append((crlf_index, crlf_index + 4))
    return min(boundaries, key=lambda item: item[0]) if boundaries else None
