"""docgen_project_append_event — 向项目 events.jsonl 追加事件"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.tools.docgen._project_model import append_event

logger = logging.getLogger(__name__)


class DocgenAppendEventTool(AgentTool):
    """向项目的 events.jsonl 追加一条事件记录"""

    name = "docgen_project_append_event"
    description = (
        "向项目事件日志追加一条记录。"
        "用于跟踪 workflow 进度、用户交互和工具调用。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="event_name",
            type="string",
            description="事件名称。示例: 'step_completed', 'user_confirmed', 'error_occurred'",
            required=True,
        ),
        ToolParameter(
            name="flow_id",
            type="string",
            description="关联的 flow ID",
            required=False,
        ),
        ToolParameter(
            name="step",
            type="string",
            description="关联的 step ID",
            required=False,
        ),
        ToolParameter(
            name="extra",
            type="string",
            description="附加数据 JSON 字符串",
            required=False,
        ),
    ]
    thinking_hint = "正在记录事件…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        event_name = args.get("event_name", "").strip()
        flow_id = args.get("flow_id", "").strip()
        step = args.get("step", "").strip()
        extra_str = args.get("extra", "{}")

        if not project_path_str or not event_name:
            return AgentToolResult.error_result(
                tool_call.id, "project_path and event_name are required"
            )

        extra: dict[str, Any] = {}
        if extra_str:
            try:
                import json
                extra = json.loads(extra_str)
            except Exception:
                pass

        event = append_event(
            project_path_str,
            event_name,
            flow_id=flow_id,
            step=step,
            **extra,
        )

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=f"Event recorded: {event_name}",
            is_error=False,
            metadata={"event": event},
            events=[],
        )
