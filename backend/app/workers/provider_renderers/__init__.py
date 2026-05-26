from app.workers.provider_renderers.base import (
    ProviderRenderResult,
    ProviderRenderer,
    RendererRegistry,
    get_renderer_registry,
)
from app.workers.provider_renderers.claude_code import ClaudeCodeRenderer
from app.workers.provider_renderers.codex import CodexRenderer
from app.workers.provider_renderers.opencode import OpenCodeRenderer
from app.workers.provider_renderers.pi import PiRenderer


def build_default_registry() -> RendererRegistry:
    registry = RendererRegistry()
    registry.register(PiRenderer())
    registry.register(OpenCodeRenderer())
    registry.register(ClaudeCodeRenderer())
    registry.register(CodexRenderer())
    return registry


__all__ = [
    "ProviderRenderResult",
    "ProviderRenderer",
    "RendererRegistry",
    "build_default_registry",
    "get_renderer_registry",
]
