"""A2UI 模块 — 双通道前端展示基础设施（强约束骨架）

提供：
- DisplayMode: 极简/详细 两种显示模式枚举
- FrontendDigest / MinimalView / DetailedView / ViewSection: 标准骨架类型
- ALLOWED_CONTENT_TYPES: 允许的 content_type 白名单
- attach_frontend_digest: 将展示数据注入 AGUI 事件流
- resolve_display_mode: 解析当前生效的显示模式（优先级链）
"""

from .types import (
    ALLOWED_CONTENT_TYPES,
    ALLOWED_DISPLAY_TYPES,
    DetailedView,
    DisplayMode,
    FrontendDigest,
    MinimalView,
    ToolDisplayType,
    ViewSection,
)
from .config import resolve_display_mode, get_display_mode

_EMITTER_EXPORTS = {
    "attach_frontend_digest",
    "apply_dual_layer",
    "set_llm_digest",
}


def __getattr__(name: str):
    if name in _EMITTER_EXPORTS:
        from . import emitter

        return getattr(emitter, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_DISPLAY_TYPES",
    "DetailedView",
    "DisplayMode",
    "FrontendDigest",
    "MinimalView",
    "ToolDisplayType",
    "ViewSection",
    "attach_frontend_digest",
    "apply_dual_layer",
    "set_llm_digest",
    "resolve_display_mode",
    "get_display_mode",
]
