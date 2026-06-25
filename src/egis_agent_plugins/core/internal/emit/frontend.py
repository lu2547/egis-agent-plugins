"""前端通道 — 把 FrontendDigest 转为 CustomToolEvent 注入 AGUI 事件流。

通过 CustomToolEvent 机制，frontend_digest 数据会自动流经：
- internal 协议 → response.ui.component 事件
- enterprise 协议 → custom 事件 (AGUIEnvelope 包装)

前端通过 ``custom_type === "frontend_digest"`` 识别并渲染。
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.types import AgentToolResult, CustomToolEvent

from ..a2ui.config import resolve_display_mode
from ..a2ui.types import DisplayMode, FrontendDigest


def attach_frontend_digest(
    result: AgentToolResult,
    digest: FrontendDigest,
    mode: DisplayMode | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """将 frontend_digest 作为 CustomToolEvent 附加到工具结果的 events 列表。

    mode 未显式传入时，自动从 context 或环境变量解析（DISPLAY_MODE / *_DISPLAY_MODE / DISPLAY_TOOL_OVERRIDES）。

    payload 格式::

        {
            "tool_name": "...",
            "display_type": "text" | "search" | "data" | "file" | ...,
            "display_mode": "minimal" | "detailed",
            "view": { ... 当前模式对应的结构化数据 ... },
            "sections": [ ... detailed 视图中的 sections ... ]
        }
    """
    if mode is None:
        mode = resolve_display_mode(digest.tool_name, context=context)
    payload: dict[str, Any] = {
        "tool_name": digest.tool_name,
        "display_type": digest.display_type,
        "display_mode": mode.value,
        "view": digest.select(mode),
        "sections": [s.to_dict() for s in digest.detailed.sections],
    }

    event = CustomToolEvent(
        custom_type="frontend_digest",
        payload=payload,
    )
    result.events.append(event)


__all__ = ["attach_frontend_digest"]
