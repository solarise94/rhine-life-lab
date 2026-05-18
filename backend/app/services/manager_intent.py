from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


IntentKind = Literal["chat", "mutation"]
MutationAction = Literal["add_module", "update_existing", "rerun", "rollback", "unknown"]


@dataclass(frozen=True)
class ManagerIntent:
    kind: IntentKind
    action: MutationAction | None = None
    reason: str = ""


class ManagerIntentRouter:
    """Conservative router that keeps normal chat away from proposal planning."""

    ADD_KEYWORDS = (
        "新增",
        "增加",
        "添加",
        "加入",
        "加一个",
        "新建",
        "生成",
        "创建",
        "add",
        "create",
    )
    UPDATE_KEYWORDS = (
        "修改",
        "调整",
        "更新",
        "改成",
        "改为",
        "强调",
        "补充",
        "完善",
        "approve",
        "accept",
        "update",
        "modify",
        "change",
    )
    RERUN_KEYWORDS = ("重跑", "重新运行", "重新执行", "rerun", "re-run")
    ROLLBACK_KEYWORDS = ("回退", "撤销", "取消", "删除", "移除", "rollback", "delete", "remove")
    CHAT_HINTS = (
        "解释",
        "说明",
        "查看",
        "有哪些",
        "为什么",
        "怎么理解",
        "总结",
        "状态",
        "进展",
        "聊聊",
        "介绍",
        "能不能",
        "是否",
        "吗",
        "?",
        "？",
    )
    DIRECTIVE_HINTS = (
        "请",
        "帮我",
        "需要",
        "我要",
        "我想",
        "把",
        "将",
        "直接",
        "现在",
        "please",
    )

    def classify(self, message: str) -> ManagerIntent:
        text = message.strip()
        lowered = text.lower()
        if not text:
            return ManagerIntent(kind="chat", reason="empty message")

        action = self._detect_action(lowered)
        if action is None:
            return ManagerIntent(kind="chat", reason="no mutation keyword")

        has_chat_hint = self._contains_any(lowered, self.CHAT_HINTS)
        has_directive = self._contains_any(lowered, self.DIRECTIVE_HINTS)
        starts_with_mutation = self._starts_with_any(lowered, self.ADD_KEYWORDS + self.UPDATE_KEYWORDS + self.RERUN_KEYWORDS + self.ROLLBACK_KEYWORDS)

        if has_chat_hint and not has_directive and not starts_with_mutation:
            return ManagerIntent(kind="chat", reason="question or explanation request")

        return ManagerIntent(kind="mutation", action=action, reason=f"matched {action} keyword")

    def _detect_action(self, lowered: str) -> MutationAction | None:
        if self._contains_any(lowered, self.ADD_KEYWORDS):
            return "add_module"
        if self._contains_any(lowered, self.UPDATE_KEYWORDS):
            return "update_existing"
        if self._contains_any(lowered, self.RERUN_KEYWORDS):
            return "rerun"
        if self._contains_any(lowered, self.ROLLBACK_KEYWORDS):
            return "rollback"
        return None

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _starts_with_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(text.startswith(keyword) for keyword in keywords)
