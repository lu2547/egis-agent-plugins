"""Trace 通道 — 把 FrontendDigest 关键字段注入 OpenTelemetry span attribute。

与 ark-agentic 0.7.6 的 ``TracingLifecycle`` 协同：TracingLifecycle 负责
``setup_tracing_from_env`` / ``shutdown_tracing``，本模块在工具执行末段把
digest 中对人类可读的关键信息（tool_name / display_type / minimal view 摘要）
作为 span attribute 写入当前 span，便于 OTLP 后端排查。

仅当 ``opentelemetry-api`` 安装且当前存在活跃 span 时生效，否则静默跳过。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..a2ui.types import DisplayMode, FrontendDigest

logger = logging.getLogger(__name__)

_ATTR_PREFIX = "egis.tool"
_MAX_VIEW_LEN = 1000  # 单条 attribute 截断长度，避免巨型 view 撑爆 OTLP 包


def trace_tool_span(digest: FrontendDigest) -> None:
    """把 digest 关键字段写入当前 OTel span attribute。

    无 opentelemetry-api / 无活跃 span 时静默跳过。
    """
    try:
        from opentelemetry import trace  # type: ignore
    except ImportError:
        return

    span = trace.get_current_span()
    if span is None or not span.is_recording():
        return

    try:
        view: Any = digest.select(DisplayMode.MINIMAL)
        view_str = json.dumps(view, ensure_ascii=False, default=str)
        if len(view_str) > _MAX_VIEW_LEN:
            view_str = view_str[:_MAX_VIEW_LEN] + "...<truncated>"

        span.set_attribute(f"{_ATTR_PREFIX}.name", digest.tool_name)
        span.set_attribute(f"{_ATTR_PREFIX}.display_type", str(digest.display_type))
        span.set_attribute(f"{_ATTR_PREFIX}.view_minimal", view_str)
    except Exception as exc:  # 任何异常都不应中断业务
        logger.debug("trace_tool_span skipped: %s", exc)


__all__ = ["trace_tool_span"]
