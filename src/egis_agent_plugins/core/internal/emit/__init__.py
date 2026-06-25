"""emit 模块 — 工具结果三通道发射层

把工具产出（``AgentToolResult`` + ``FrontendDigest`` + ``llm_summary``）
分发到三个独立通道，各自互不耦合：

- ``llm``      ── ``set_llm_digest``：精简摘要写入 ``result.llm_digest``，控制
                  传给 LLM 的上下文 token。
- ``frontend`` ── ``attach_frontend_digest``：把 digest 包成 CustomToolEvent
                  注入 AGUI 事件流，前端按 ``custom_type=='frontend_digest'``
                  识别并渲染。
- ``trace``    ── ``trace_tool_span``：把 digest 关键字段写入当前 OpenTelemetry
                  span attribute，与 ark-agentic 0.7.6 的 ``TracingLifecycle``
                  协同（OTLP 链路追踪）。

工具实现侧推荐统一调 ``emit_result(result, digest, llm_summary)``，避免漏发任何通道。
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.types import AgentToolResult

from ..a2ui.types import DisplayMode, FrontendDigest
from .frontend import attach_frontend_digest
from .llm import set_llm_digest
from .trace import trace_tool_span


def emit_result(
    result: AgentToolResult,
    digest: FrontendDigest,
    llm_summary: str,
    mode: DisplayMode | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    """一站式三通道发射 — 同时打 LLM digest / 前端事件 / OTel span。

    ``llm_digest`` 已足够让 runner 替换 LLM 上下文；**不再覆盖**
    ``result.content``，以保留结构化数据供内部工具调用使用。

    Args:
        result: 工具执行结果（ark AgentToolResult）。
        digest: 双模式前端展示数据。
        llm_summary: LLM 精简摘要（写入 llm_digest，runner 自动替代 content）。
        mode: 前端展示模式；None 时从 context 或环境变量解析。
        context: 工具执行上下文，用于解析请求级 display_mode 覆盖。
    """
    set_llm_digest(result, llm_summary)
    attach_frontend_digest(result, digest, mode, context=context)
    trace_tool_span(digest)


__all__ = [
    "set_llm_digest",
    "attach_frontend_digest",
    "trace_tool_span",
    "emit_result",
]
