"""制作方式选择工具（A2UI 版）

select_method — 通过 A2UI 协议渲染选择卡片，让用户在「标化材料制作」和「AI 制作」之间选择。
LLM 在对话开始时必须首先调用此工具，然后通过 final_answer(is_blocking=True) 等待用户选择。

前端通过 A2UI 渲染两张选择卡片，每张卡片包含标题、描述和选择按钮。
"""

from __future__ import annotations

import logging
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult

logger = logging.getLogger(__name__)


def _build_a2ui_payload(title: str, description: str) -> dict[str, Any]:
    """构建 A2UI 组件树：两张选择卡片（标化 / AI 制作）。"""
    return {
        "event": "beginRendering",
        "version": "1.0",
        "style": "default",
        "rootComponentId": "root-001",
        "components": [
            # ── 根容器 ──
            {
                "id": "root-001",
                "component": {
                    "Column": {
                        "width": 100,
                        "backgroundColor": "#F7F9FC",
                        "padding": [16, 16, 16, 16],
                        "gap": 12,
                        "borderRadius": "big",
                        "children": {"explicitList": [
                            "title-text", "subtitle-text", "divider-01",
                            "card-main",
                        ]},
                    }
                },
            },
            # ── 标题 ──
            {
                "id": "title-text",
                "component": {
                    "Text": {
                        "text": {"literalString": title},
                        "color": "#1e2432",
                        "fontSize": "16px",
                        "bold": True,
                    }
                },
            },
            # ── 副标题 ──
            {
                "id": "subtitle-text",
                "component": {
                    "Text": {
                        "text": {"literalString": description},
                        "color": "#666666",
                        "fontSize": "13px",
                    }
                },
            },
            # ── 分割线 ──
            {
                "id": "divider-01",
                "component": {
                    "Divider": {"borderColor": "#E8ECF2"}
                },
            },
            # ── 外层卡片：两种方式左右并排 ──
            {
                "id": "card-main",
                "component": {
                    "Card": {
                        "backgroundColor": "#FFFFFF",
                        "borderRadius": "middle",
                        "padding": [16, 18, 16, 18],
                        "children": {"explicitList": ["main-row"]},
                    }
                },
            },
            {
                "id": "main-row",
                "component": {
                    "Row": {
                        "gap": 18,
                        "alignment": "top",
                        "children": {"explicitList": ["std-col", "mid-line", "ai-col"]},
                    }
                },
            },
            {
                "id": "std-col",
                "component": {
                    "Column": {
                        "flex": 1,
                        "gap": 6,
                        "children": {"explicitList": [
                            "std-title-row", "std-desc", "std-features", "std-btn",
                        ]},
                    }
                },
            },
            {
                "id": "mid-line",
                "component": {
                    "Line": {
                        "backgroundColor": "#EEF1F6",
                        "minWidth": 1,
                        "minHeight": 150,
                    }
                },
            },
            {
                "id": "std-title-row",
                "component": {
                    "Row": {
                        "distribution": "spaceBetween",
                        "alignment": "middle",
                        "children": {"explicitList": ["std-title", "std-tag"]},
                    }
                },
            },
            {
                "id": "std-title",
                "component": {
                    "Text": {
                        "text": {"literalString": "标化材料制作"},
                        "color": "#1e2432",
                        "fontSize": "15px",
                        "bold": True,
                    }
                },
            },
            {
                "id": "std-tag",
                "component": {
                    "Tag": {
                        "text": {"literalString": "快速"},
                        "color": "#D4760A",
                        "backgroundColor": "#FFF4E0",
                    }
                },
            },
            {
                "id": "std-desc",
                "component": {
                    "Text": {
                        "text": {"literalString": "基于固定模板 + AI 智能填充，适合有标准格式的报表类材料"},
                        "color": "#666666",
                        "fontSize": "13px",
                    }
                },
            },
            {
                "id": "std-features",
                "component": {
                    "Text": {
                        "text": {"literalString": "固定格式模板 · AI 智能填充数据 · 快速生成报表"},
                        "color": "#999999",
                        "fontSize": "12px",
                    }
                },
            },
            {
                "id": "std-btn",
                "component": {
                    "Button": {
                        "text": {"literalString": "选择标化材料制作"},
                        "type": {"literalString": "primary"},
                        "width": 100,
                        "action": {"type": "sendMessage", "message": "我选择标化材料制作"},
                    }
                },
            },
            # ── AI 制作列（同在 card-main 内）──
            {
                "id": "ai-col",
                "component": {
                    "Column": {
                        "flex": 1,
                        "gap": 6,
                        "children": {"explicitList": [
                            "ai-title-row", "ai-desc", "ai-features", "ai-btn",
                        ]},
                    }
                },
            },
            {
                "id": "ai-title-row",
                "component": {
                    "Row": {
                        "distribution": "spaceBetween",
                        "alignment": "middle",
                        "children": {"explicitList": ["ai-title", "ai-tag"]},
                    }
                },
            },
            {
                "id": "ai-title",
                "component": {
                    "Text": {
                        "text": {"literalString": "AI 制作"},
                        "color": "#1e2432",
                        "fontSize": "15px",
                        "bold": True,
                    }
                },
            },
            {
                "id": "ai-tag",
                "component": {
                    "Tag": {
                        "text": {"literalString": "灵活"},
                        "color": "#3B6CDB",
                        "backgroundColor": "#E8F0FE",
                    }
                },
            },
            {
                "id": "ai-desc",
                "component": {
                    "Text": {
                        "text": {"literalString": "AI 全程参与：智能列大纲 → 可编辑调整 → AI 生成 PPT/Word"},
                        "color": "#666666",
                        "fontSize": "13px",
                    }
                },
            },
            {
                "id": "ai-features",
                "component": {
                    "Text": {
                        "text": {"literalString": "AI 智能生成大纲 · 可编辑可调序 · PPT / Word 多格式输出"},
                        "color": "#999999",
                        "fontSize": "12px",
                    }
                },
            },
            {
                "id": "ai-btn",
                "component": {
                    "Button": {
                        "text": {"literalString": "选择 AI 制作"},
                        "type": {"literalString": "primary"},
                        "width": 100,
                        "action": {"type": "sendMessage", "message": "我选择AI制作"},
                    }
                },
            },
        ],
        "data": {},
    }


class SelectMethodTool(AgentTool):
    """制作方式选择工具（A2UI 版）

    在对话开始时调用，弹出选择卡片让用户选择制作方式。
    调用后应紧跟 final_answer(is_blocking=True) 等待用户选择。
    """

    name = "select_method"
    description = (
        "弹出制作方式选择卡片，让用户选择：\n"
        "1. 标化材料制作 — 基于固定模板+AI制作，适合有固定格式的报表材料\n"
        "2. AI 制作 — AI列大纲+AI编排+AI制作，适合需要定制化内容的 PPT/Word 材料\n"
        "调用此工具后，必须紧跟 final_answer(is_blocking=true) 请用户选择。"
    )
    parameters = [
        ToolParameter(
            name="title",
            type="string",
            description="选择卡片的标题，默认为「请选择材料制作方式」",
            required=False,
        ),
        ToolParameter(
            name="description",
            type="string",
            description="选择卡片的描述文本，简要说明两种方式的区别",
            required=False,
        ),
    ]
    thinking_hint = "正在展示制作方式选择…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        title = (args.get("title") or "请选择材料制作方式").strip()
        description = (args.get("description") or "我们提供两种材料制作方式，请根据您的需要选择").strip()

        logger.info("[SelectMethod] Presenting A2UI method selection card")

        payload = _build_a2ui_payload(title, description)

        return AgentToolResult.a2ui_result(
            tool_call.id,
            payload,
            llm_digest="已展示制作方式选择卡片，等待用户选择",
        )
