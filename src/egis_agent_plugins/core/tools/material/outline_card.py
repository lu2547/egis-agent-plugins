"""大纲卡片工具

outline_card — 输出可编辑大纲卡片，支持调序、改标题/内容。
LLM 在生成大纲后调用此工具展示结构化大纲，前端渲染为可交互卡片。
用户确认/修改后，LLM 通过 final_answer(is_blocking=True) 等待进一步操作。

前端根据 tool_name="outline_card" 渲染大纲编辑卡片：
  - 左侧：页码/章节标签（如 P1、P2 章节）
  - 右侧：标题 + 内容摘要 + 要点列表
  - 支持拖拽调序、行内编辑
  - 底部：确认按钮
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

logger = logging.getLogger(__name__)


class OutlineCardTool(AgentTool):
    """大纲卡片工具

    输出结构化大纲数据，前端渲染为可编辑/可调序的交互卡片。
    调用后应紧跟 final_answer(is_blocking=True) 等待用户确认大纲。
    """

    name = "outline_card"
    description = (
        "输出可编辑大纲卡片。\n"
        "将结构化大纲数据展示为可交互的卡片，用户可以：\n"
        "- 拖拽调整章节顺序\n"
        "- 编辑标题和内容\n"
        "- 确认或提出修改意见\n\n"
        "调用此工具后，必须紧跟 final_answer(is_blocking=true) 询问用户是否满意大纲。\n"
        "参数 outline 为 JSON 数组，每个元素代表一个章节/页面。"
    )
    parameters = [
        ToolParameter(
            name="outline",
            type="string",
            description=(
                "JSON 格式的大纲数据。数组，每个元素包含：\n"
                "- page: 页码/序号（如 \"P1\", \"P2 章节\"）\n"
                "- type: 页面类型（cover/toc/section/content/transition/ending）\n"
                "- title: 标题\n"
                "- subtitle: 副标题（可选）\n"
                "- bullets: 要点列表（字符串数组）\n"
                "- visual: 视觉元素建议（可选）\n\n"
                "示例: [{\"page\":\"P1\",\"type\":\"cover\",\"title\":\"封面标题\",\"subtitle\":\"副标题\"}]"
            ),
            required=True,
        ),
        ToolParameter(
            name="document_type",
            type="string",
            description="目标文档类型：ppt 或 word。影响前端卡片的展示风格。默认 ppt。",
            required=False,
        ),
    ]
    thinking_hint = "正在生成大纲卡片…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        outline_raw = (args.get("outline") or "").strip()
        document_type = (args.get("document_type") or "ppt").strip().lower()

        if not outline_raw:
            return AgentToolResult.error_result(
                tool_call.id,
                "outline 参数不能为空",
            )

        # 解析大纲数据
        try:
            outline_data = json.loads(outline_raw)
            if not isinstance(outline_data, list):
                raise ValueError("outline 必须是 JSON 数组")
        except (json.JSONDecodeError, ValueError) as e:
            return AgentToolResult.error_result(
                tool_call.id,
                f"outline 参数解析失败: {e}",
            )

        page_count = len(outline_data)
        logger.info(
            "[OutlineCard] Generating outline card with %d pages, type=%s",
            page_count, document_type,
        )

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=f"已展示大纲卡片，共 {page_count} 个章节/页面（{document_type.upper()} 模式）",
            is_error=False,
            metadata={"page_count": page_count, "document_type": document_type},
            events=[],
        )

        # 构建大纲文本摘要（供 LLM 上下文使用）
        outline_summary_parts = []
        for item in outline_data:
            page = item.get("page", "")
            title = item.get("title", "")
            item_type = item.get("type", "")
            outline_summary_parts.append(f"{page} | {item_type} | {title}")
        outline_summary = "\n".join(outline_summary_parts)

        digest = FrontendDigest(
            tool_name="outline_card",
            display_type=ToolDisplayType.CHECKLIST,
            minimal=MinimalView(
                title="内容大纲",
                summary=f"共 {page_count} 个章节/页面，可编辑调序",
                icon="outline",
                status="info",
            ),
            detailed=DetailedView(
                title="内容大纲",
                sections=[
                    ViewSection(
                        heading="大纲预览",
                        content_type="checklist",
                        data=[
                            {
                                "text": f"{item.get('page', '')} {item.get('title', '')}",
                                "status": "pending",
                                "type": item.get("type", "content"),
                                "children": [
                                    {"text": b, "status": "pending"}
                                    for b in (item.get("bullets") or [])
                                ],
                            }
                            for item in outline_data
                        ],
                    ),
                ],
            ),
        )

        apply_dual_layer(
            result, digest,
            f"大纲已展示（{page_count} 页），等待用户确认或修改",
        )

        # 附加完整大纲数据到 events payload 供前端渲染可编辑卡片
        for event in result.events:
            if hasattr(event, "payload") and isinstance(event.payload, dict):
                event.payload["outline"] = outline_data
                event.payload["document_type"] = document_type

        return result
