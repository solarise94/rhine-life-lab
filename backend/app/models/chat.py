from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field

from app.models.patches import Proposal


class ChatContext(BaseModel):
    selected_card_id: str | None = None
    selected_result_id: str | None = None


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "manager"]
    content: str


class ChatRequest(BaseModel):
    message: str
    context: ChatContext = Field(default_factory=ChatContext)
    thinking_effort: Literal["low", "medium", "high"] = "medium"
    messages: list[ChatHistoryMessage] = Field(default_factory=list)


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


class ChatSessionMessage(BaseModel):
    id: str
    role: Literal["user", "manager"]
    content: str
    proposal: Proposal | None = None
    thinking: str | None = None
    attachments: list[ChatMessageAttachment] = Field(default_factory=list)
    state: Literal["idle", "thinking", "streaming", "done", "error"] | None = None


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
