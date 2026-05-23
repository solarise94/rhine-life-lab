from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

from app.models.patches import Proposal


class ChatContext(BaseModel):
    selected_card_id: str | None = None
    selected_result_id: str | None = None
    script_preference: Literal["auto", "prefer_python", "prefer_r", "prefer_mixed"] = "auto"
    python_runtime: str | None = None
    r_runtime: str | None = None


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "manager"]
    content: str


class ChatRequest(BaseModel):
    message: str
    context: ChatContext = Field(default_factory=ChatContext)
    thinking_effort: Literal["low", "medium", "high"] = "medium"
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    session_messages: list["ChatSessionMessage"] = Field(default_factory=list)


class ChatAction(BaseModel):
    label: str
    action: str


class ChatResponse(BaseModel):
    message: str
    thinking: str | None = None
    proposal: Proposal | None = None
    actions: list[ChatAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessageAttachment(BaseModel):
    type: Literal["card", "asset"]
    id: str
    label: str


class ChatTokenUsage(BaseModel):
    input_tokens: int | float = 0
    output_tokens: int | float = 0
    cache_read_tokens: int | float = 0
    cache_write_tokens: int | float = 0
    total_tokens: int | float = 0
    context_window_tokens: int | float | None = None
    max_output_tokens: int | float | None = None


class ChatSessionMessageTimelineItem(BaseModel):
    id: str
    kind: str
    content: str | None = None
    label: str | None = None
    tool_name: str | None = None
    status: str | None = None
    started_at: int | float | None = None
    ended_at: int | float | None = None
    first_kept_message_id: str | None = None
    tokens_before: int | float | None = None
    tokens_after: int | float | None = None
    duration_ms: int | float | None = None
    provider: str | None = None
    model: str | None = None


class ChatSessionMessage(BaseModel):
    id: str
    role: Literal["user", "manager"]
    content: str
    proposal: Proposal | None = None
    thinking: str | None = None
    attachments: list[ChatMessageAttachment] = Field(default_factory=list)
    state: Literal["idle", "thinking", "streaming", "done", "error"] | None = None
    timeline: list[ChatSessionMessageTimelineItem] | None = None
    token_usage: ChatTokenUsage | None = None


class ChatSession(BaseModel):
    session_id: str
    summary: str
    created_at: str
    updated_at: str
    messages: list[ChatSessionMessage] = Field(default_factory=list)


class ChatSessionSummary(BaseModel):
    session_id: str
    summary: str
    created_at: str
    updated_at: str
    message_count: int = 0
