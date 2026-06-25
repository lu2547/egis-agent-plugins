"""DocGen WorkflowTool 适配器 — Workflow → AgentTool 桥接

DocgenEntryTool: 统一入口流程（选方式 + 选模板/格式 + 建项目）
  show_entry 走 frontend_digest 通道，由前端 Vue 组件渲染。

标书等具体业务的 WorkflowTool 由 Agent 层定义（如 doc_creator_agent/flows）。
"""

from __future__ import annotations

from typing import Any, ClassVar

from ark_agentic.core.types import AgentToolResult, CustomToolEvent, ToolCall
from ark_agentic.core.workflow import WorkflowTool

from .entry import DocgenEntryFlow

__all__ = [
    "DocgenEntryTool",
    "create_docgen_flow_tools",
]


class DocgenEntryTool(WorkflowTool):
    """DocGen 统一入口流程工具 — 选方式 + 选模板/格式 + 建项目

    show_entry 时通过 frontend_digest 通道推送数据，
    前端 DocgenEntryCard.vue 组件负责渲染 Step1→Step2 交互。
    """

    name = "docgen_entry_flow"
    description = (
        "DocGen 统一入口流程：Step1(选方式) → Step2(选方案)。\n"
        "actions:\n"
        "  start - 创建流程实例\n"
        "  show_entry - 展示入口卡片（BLOCKING）\n"
        "  select_tender - 选择标书 [word]（标化）\n"
        "  select_pension_intro - 选择平安养老险优势介绍 [word]（标化）\n"
        "  select_investment_report - 选择投资报告 [word]（标化）\n"
        "  select_word - 选择 AI Word 制作\n"
        "  select_ppt - 选择 AI PPT 制作\n"
        "show_entry 会基于项目状态 JSON 判断展示制作方式选择，还是直接展示标化模板列表。\n"
        "show_entry 后必须 final_answer(is_blocking=true)。"
    )
    flow_cls: ClassVar[type] = DocgenEntryFlow

    async def execute(
        self,
        tool_call: ToolCall,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        action = str(tool_call.arguments.get("action") or "")

        # 非 show_entry：走父类默认 A2UI / json 通道
        if action != "show_entry":
            return await super().execute(tool_call, context)

        # 入口展示：走 Workflow 状态机
        instance_id_raw = str(
            tool_call.arguments.get(self.INSTANCE_PARAM_NAME) or "",
        )
        result = await self.flow.execute(
            instance_id_raw=instance_id_raw,
            action=action,
            args={
                k: v for k, v in tool_call.arguments.items()
                if k not in (self.INSTANCE_PARAM_NAME, "action")
            },
            session_ctx=context or {},
        )

        if not result.success:
            assert result.rejection is not None
            return AgentToolResult.error_result(
                tool_call.id, result.rejection.to_llm_text(),
            )

        # 构建 json_result（给 LLM 看的 digest）+ frontend_digest（给前端渲染）
        agent_result = AgentToolResult.json_result(
            tool_call_id=tool_call.id,
            data=result.message or result.digest,
            metadata=(
                {"state_delta": result.state_delta}
                if result.state_delta else {}
            ),
            llm_digest=result.digest,
        )

        # 通过 frontend_digest 通道推送给前端 Vue 组件
        initial_mode = str(
            tool_call.arguments.get("initial_mode")
            or tool_call.arguments.get("mode")
            or "",
        ).strip()
        state_delta = result.state_delta or {}
        if not initial_mode:
            initial_mode = str(state_delta.get("docgen_state.entry_mode") or "").strip()
        digest_payload = {"tool_name": "docgen_entry_flow"}
        if initial_mode in {"standard", "ai"}:
            digest_payload["initial_mode"] = initial_mode

        digest_event = CustomToolEvent(
            custom_type="frontend_digest",
            payload=digest_payload,
        )
        agent_result.events.append(digest_event)

        return agent_result


def create_docgen_flow_tools() -> list:
    """创建 DocGen Flow 工具实例列表"""
    return [DocgenEntryTool()]
