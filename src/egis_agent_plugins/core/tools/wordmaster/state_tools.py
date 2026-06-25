"""Word Master 状态持久化工具

docx_save_state — LLM 在 Word 工作流关键节点调用，将状态写入 session state。
续接时检查这些状态，决定从哪个断点继续。

所有状态写入 user:wordmaster_state 命名空间（通过 dot-path: user:wordmaster_state.xxx）。

状态 key 清单:
  - step:           当前步骤编号
  - project_path:   当前项目路径
  - mode:           当前模式（create / edit）
  - source_file:    原始文档路径（编辑模式）
  - unpacked_dir:   unpacked 目录路径
  - output_file:    最终输出文件路径
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

# 允许写入的 state key 白名单（叶子 key，自动加 wordmaster_state. 前缀）
_ALLOWED_KEYS = frozenset({
    "step",
    "project_path",
    "mode",
    "source_file",
    "unpacked_dir",
    "output_file",
})


class DocxSaveStateTool(AgentTool):
    """保存 Word 工作流状态到 session state。

    在以下关键节点调用：
    - 初始化项目后:     key="project_path"   value=<path>
    - 确定工作模式后:   key="mode"           value="create" | "edit"
    - unpack 后:        key="unpacked_dir"   value=<path>
    - 生成/导出后:      key="output_file"    value=<path>
    """

    name = "docx_save_state"
    description = (
        "将 Word 工作流状态保存到 session，用于断点追踪。"
        "在工作流关键节点调用。"
        "支持的 key："
        "step（当前步骤）、"
        "project_path（项目目录）、"
        "mode（create / edit）、"
        "source_file（原始文档路径）、"
        "unpacked_dir（unpacked 目录路径）、"
        "output_file（最终输出文件路径）。"
        "所有状态存储在 wordmaster_state 命名空间下。"
    )
    parameters = [
        ToolParameter(
            name="key",
            type="string",
            description=(
                "要写入的状态 key。可选值：step、project_path、mode、"
                "source_file、unpacked_dir、output_file"
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
    thinking_hint = "正在保存 Word 状态…"

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

        dot_key = f"user:wordmaster_state.{key}"
        logger.info("[DocxSaveState] key=%s value=%s", dot_key, str(value)[:100])

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
            tool_name="docx_save_state",
            display_type=ToolDisplayType.DATA,
            minimal=MinimalView(title=f"{key}", summary="该项进度已保存", icon="wordmaster", status="success"),
            detailed=DetailedView(title="状态保存", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{key} = {str(value)[:60]}"),
            ]),
        )
        apply_dual_layer(result, digest, f"[WordMaster] 状态已保存: {key}={str(value)[:60]}")
        return result
