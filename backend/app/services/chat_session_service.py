from __future__ import annotations

import re
from uuid import uuid4

from app.models.chat import ChatSession, ChatSessionMessage, ChatSessionSummary
from app.services.project_service import ProjectService
from app.services.utils import utc_now


class ChatSessionService:
    def __init__(self, project_service: ProjectService) -> None:
        self.project_service = project_service

    def list_sessions(self, project_id: str) -> list[ChatSessionSummary]:
        sessions = self.project_service.graph_store(project_id).load_chat_sessions()
        return [
            ChatSessionSummary(
                session_id=session.session_id,
                summary=session.summary,
                created_at=session.created_at,
                updated_at=session.updated_at,
                message_count=len(session.messages),
            )
            for session in sorted(sessions, key=lambda item: item.updated_at, reverse=True)
        ]

    def create_session(self, project_id: str, summary: str | None = None) -> ChatSession:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            sessions = store.load_chat_sessions()
            now = utc_now()
            session = ChatSession(
                session_id=f"session_{uuid4().hex[:12]}",
                summary=(summary or "新会话").strip() or "新会话",
                created_at=now,
                updated_at=now,
                messages=[],
            )
            sessions.append(session)
            store.save_chat_sessions(sessions)
            return session

    def get_session(self, project_id: str, session_id: str) -> ChatSession:
        sessions = self.project_service.graph_store(project_id).load_chat_sessions()
        session = next((item for item in sessions if item.session_id == session_id), None)
        if not session:
            raise ValueError(f"Chat session not found: {session_id}")
        return session

    def save_session(
        self,
        project_id: str,
        session_id: str,
        messages: list[ChatSessionMessage],
        summary: str | None = None,
    ) -> ChatSession:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            sessions = store.load_chat_sessions()
            session = next((item for item in sessions if item.session_id == session_id), None)
            if not session:
                raise ValueError(f"Chat session not found: {session_id}")
            session.messages = messages
            session.summary = self._derive_summary(messages, summary or session.summary)
            session.updated_at = utc_now()
            store.save_chat_sessions(sessions)
            return session

    def delete_session(self, project_id: str, session_id: str) -> None:
        lock = self.project_service.lock_for(project_id)
        with lock:
            store = self.project_service.graph_store(project_id)
            sessions = store.load_chat_sessions()
            remaining = [item for item in sessions if item.session_id != session_id]
            if len(remaining) == len(sessions):
                raise ValueError(f"Chat session not found: {session_id}")
            store.save_chat_sessions(remaining)

    @staticmethod
    def _derive_summary(messages: list[ChatSessionMessage], fallback: str) -> str:
        first_user_message = next((item.content for item in messages if item.role == "user" and item.content.strip()), "")
        if not first_user_message:
            return fallback.strip() or "新会话"
        compact = re.sub(r"\s+", " ", first_user_message).strip()
        return compact[:40] + ("…" if len(compact) > 40 else "")
