from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.patches import Proposal


class ChatContext(BaseModel):
    selected_card_id: str | None = None
    selected_result_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    context: ChatContext = Field(default_factory=ChatContext)


class ChatAction(BaseModel):
    label: str
    action: str


class ChatResponse(BaseModel):
    message: str
    proposal: Proposal | None = None
    actions: list[ChatAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

