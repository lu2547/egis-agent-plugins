"""材料制作流程状态持久化工具

material_save_state — LLM 在材料制作流程关键节点调用，将状态写入 session state。
续接时检查这些状态，决定从哪个断点继续。

所有状态写入 user:material_state 命名空间（通过 dot-path: user:material_state.xxx）。

状态 key 清单:
  - step:                    当前大步骤（1=选方式, 2=AI制作, 3=交付确认）
  - method:                  用户选择的制作方式（standard / ai_creation）
  - document_type:           文档类型（ppt / word）
  - outline:                 用户已确认的大纲（JSON 数组）
  - outline_confirmed:       大纲是否已被用户确认 (true/false)
  - generation_complete:     文档生成是否完成 (true/false)
  - project_path:            项目目录路径
  - download_url:            下载链接
"""

from __future__ import annotations

import logging
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

logger = logging.getLogger(__name__)

# 允许写入的 state key 白名单（叶子 key，自动加 material_state. 前缀）
_ALLOWED_KEYS = frozenset({
    "config",            # 核心配置 JSON: {topic, method, document_type, page_count, ...}
    "step",
    "method",
    "document_type",
    "outline",
    "outline_confirmed",
    "generation_complete",
    "project_path",
    "download_url",
})


class MaterialSaveStateTool(AgentTool):
    """保存材料制作流程状态到 session state。

    在以下关键节点调用：
    - 用户选择方式后:     key="method"           value="standard" | "ai_creation"
    - 进入 AI 制作后:     key="step"             value="2"
    - 大纲确认后:         key="outline_confirmed" value="true"
    - 文档生成后:         key="generation_complete" value="true"
    - 下载链接生成后:     key="download_url"      value="<url>"
    """

    name = "material_save_state"
    description = (
        "将材料制作流程状态保存到 session，用于断点追踪。"
        "在材料制作流程的关键决策/确认节点调用。"
        "支持的 key："
        "config（核心配置 JSON，包含 topic/method/document_type/page_count 等已知信息）、"
        "step（1-3）、method（standard/ai_creation）、document_type（ppt/word）、"
        "outline（JSON）、outline_confirmed（true/false）、"
        "generation_complete（true/false）、project_path（字符串）、download_url（字符串）。"
        "所有状态存储在 material_state 命名空间下。"
        "特别地，config 用于保存从用户消息中提取的结构化信息，避免重复询问用户已提供的内容。"
    )
    parameters = [
        ToolParameter(
            name="key",
            type="string",
            description=(
                "要写入的状态 key。可选值：config、step、method、document_type、outline、"
                "outline_confirmed、generation_complete、project_path、download_url"
            ),
            required=True,
        ),
        ToolParameter(
            name="value",
            type="string",
            description="要保存的值。字符串、布尔值和 JSON 可序列化对象均可接受。",
            required=True,
        ),
    ]
    thinking_hint = "正在保存材料制作状态…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        key = (args.get("key") or "").strip()
        value = args.get("value")

        if not key:
            return AgentToolResult.error_result(tool_call.id, "key is required")
        if key not in _ALLOWED_KEYS:
            return AgentToolResult.error_result(
                tool_call.id,
                f"Unknown key: {key}. Allowed: {sorted(_ALLOWED_KEYS)}",
            )

        dot_key = f"user:material_state.{key}"
        logger.info("[MaterialState] key=%s value=%s", dot_key, str(value)[:100])

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=f"State saved: {dot_key} = {value}",
            is_error=False,
            metadata={
                "state_delta": {dot_key: value},
                "saved_key": dot_key,
            },
            events=[],
        )
        digest = FrontendDigest(
            tool_name="material_save_state",
            display_type=ToolDisplayType.DATA,
            minimal=MinimalView(
                title=f"{key}",
                summary="材料制作进度已保存",
                icon="material",
                status="success",
            ),
            detailed=DetailedView(title="状态保存", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{dot_key} = {str(value)[:60]}"),
            ]),
        )
        apply_dual_layer(result, digest, f"[材料制作] 状态已保存: {key}")
        return result
