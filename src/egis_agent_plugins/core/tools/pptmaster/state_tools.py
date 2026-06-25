"""PPT 状态持久化工具

ppt_save_state — LLM 在 PPT pipeline 关键节点调用，将状态写入 session state。
续接时 react 检查这些状态，决定从哪个断点继续。

所有状态写入 user:pptmaster_state 命名空间（通过 dot-path: user:pptmaster_state.xxx）。

状态 key 清单:
  - step:                       当前步骤编号 (1-7)
  - draft_outline:              未确认的大纲草稿（用户确认前在此迭代）
  - outline:                    用户已确认的大纲正式版（从 draft_outline 固化）
  - outline_confirmed:          大纲是否已被用户确认 (true/false)
  - eight_params:               Strategist 产出的八项参数 (dict)
  - eight_params_confirmed:     八项参数是否已被用户确认 (true/false)
  - awaiting_confirmation:      当前等待用户确认的条目标识（outline / eight_params 等）
  - last_project_artifact_paths: 最近一次项目产物路径集合（dict: project_path / design_spec_path / spec_lock_path）
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

# 允许写入的 state key 白名单（叶子 key，自动加 pptmaster_state. 前缀）
_ALLOWED_KEYS = frozenset({
    "step",
    "outline",
    "outline_confirmed",
    "eight_params",
    "eight_params_confirmed",
    # 断点模型扩展：大纲草稿和确认等待态
    "draft_outline",
    "awaiting_confirmation",
    "last_project_artifact_paths",
})


class PptSaveStateTool(AgentTool):
    """保存 PPT pipeline 状态到 session state。

    在以下关键节点调用：
    - 进入新 step 时:   key="ppt_step"           value=<1-7>
    - 产出大纲后:       key="ppt_outline"        value="<markdown>"
    - 用户确认大纲后:   key="ppt_outline_confirmed"  value=true
    - 产出八项参数后:   key="ppt_eight_params"    value=<json dict>
    - 用户确认参数后:   key="ppt_eight_params_confirmed" value=true
    """

    name = "ppt_save_state"
    description = (
        "将 PPT pipeline 状态保存到 session，用于断点追踪。"
        "在 PPT pipeline 的关键决策/确认节点调用。"
        "支持的 key："
        "step（1-7）、"
        "draft_outline（markdown，用户确认前）、"
        "outline（markdown，用户确认后）、"
        "outline_confirmed（true/false）、"
        "eight_params（dict）、"
        "eight_params_confirmed（true/false）、"
        "awaiting_confirmation（如 'outline' | 'eight_params'）、"
        "last_project_artifact_paths（dict，含 project_path / design_spec_path / spec_lock_path）。"
        "所有状态存储在 pptmaster_state 命名空间下。"
    )
    parameters = [
        ToolParameter(
            name="key",
            type="string",
            description=(
                "要写入的状态 key。可选值：step、draft_outline、outline、"
                "outline_confirmed、eight_params、eight_params_confirmed、"
                "awaiting_confirmation、last_project_artifact_paths"
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
    thinking_hint = "正在保存 PPT 状态…"

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

        dot_key = f"user:pptmaster_state.{key}"
        logger.info("[PptSaveState] key=%s value=%s", dot_key, str(value)[:100])

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
            tool_name="ppt_save_state",
            display_type=ToolDisplayType.DATA,
            minimal=MinimalView(title=f"{key}", summary="该项进度已保存", icon="pptmaster", status="success"),
            detailed=DetailedView(title="状态保存", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{dot_key} = {str(value)[:60]}"),
            ]),
        )
        apply_dual_layer(result, digest, f"[PPTMaster] 状态已保存: {key}")
        return result
