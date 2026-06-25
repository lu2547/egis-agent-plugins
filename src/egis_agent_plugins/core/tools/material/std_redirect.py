"""标化路径跳转工具（A2UI 版）

std_redirect — 当用户选择「标化材料制作」时，通过 A2UI 协议展示跳转报表平台的卡片。
包含一个跳转按钮，点击后打开报表平台 URL。

前端通过 A2UI 渲染跳转卡片。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult

logger = logging.getLogger(__name__)

# 报表平台占位 URL，后续替换为真实地址
_DEFAULT_REPORT_URL = os.getenv(
    "MATERIAL_STD_REPORT_URL",
    "https://report.example.com/material-maker",
)


def _build_a2ui_payload(message: str, report_url: str) -> dict[str, Any]:
    """构建 A2UI 组件树：跳转报表平台卡片。"""
    return {
        "event": "beginRendering",
        "version": "1.0",
        "style": "default",
        "rootComponentId": "root-001",
        "components": [
            {
                "id": "root-001",
                "component": {
                    "Column": {
                        "width": 100,
                        "backgroundColor": "#F0F7FF",
                        "padding": [20, 20, 20, 20],
                        "gap": 12,
                        "borderRadius": "big",
                        "children": {"explicitList": [
                            "icon-row", "title-text", "msg-text", "divider-01",
                            "url-text", "redirect-btn",
                        ]},
                    }
                },
            },
            {
                "id": "icon-row",
                "component": {
                    "Row": {
                        "gap": 8,
                        "alignment": "middle",
                        "children": {"explicitList": ["icon-tag", "title-text-2"]},
                    }
                },
            },
            {
                "id": "icon-tag",
                "component": {
                    "Tag": {
                        "text": {"literalString": "标化"},
                        "color": "#D4760A",
                        "backgroundColor": "#FFF4E0",
                    }
                },
            },
            {
                "id": "title-text-2",
                "component": {
                    "Text": {
                        "text": {"literalString": "标化材料制作"},
                        "color": "#1e2432",
                        "fontSize": "16px",
                        "bold": True,
                    }
                },
            },
            {
                "id": "title-text",
                "component": {
                    "Text": {
                        "text": {"literalString": "已为您准备好标化制作环境"},
                        "color": "#1e2432",
                        "fontSize": "14px",
                        "bold": True,
                    }
                },
            },
            {
                "id": "msg-text",
                "component": {
                    "Text": {
                        "text": {"literalString": message},
                        "color": "#666666",
                        "fontSize": "13px",
                    }
                },
            },
            {
                "id": "divider-01",
                "component": {
                    "Divider": {"borderColor": "#D8E4F0"}
                },
            },
            {
                "id": "url-text",
                "component": {
                    "Text": {
                        "text": {"literalString": f"目标地址：{report_url}"},
                        "color": "#999999",
                        "fontSize": "11px",
                    }
                },
            },
            {
                "id": "redirect-btn",
                "component": {
                    "Button": {
                        "text": {"literalString": "前往制作"},
                        "type": {"literalString": "primary"},
                        "width": 100,
                        "url": {"literalString": report_url},
                    }
                },
            },
        ],
        "data": {},
    }


class StdRedirectTool(AgentTool):
    """标化路径跳转工具（A2UI 版）

    当用户选择「标化材料制作」时调用，展示跳转报表平台的卡片。
    调用后应紧跟 final_answer 说明跳转信息。
    """

    name = "std_redirect"
    description = (
        "展示跳转平台的卡片按钮。\n"
        "当用户选择「标化材料制作」时调用此工具，"
        "展示一个包含跳转按钮的卡片，用户点击后前往制作。\n"
        "调用后应紧跟 final_answer 告知用户已准备好跳转。"
    )
    parameters = [
        ToolParameter(
            name="message",
            type="string",
            description="展示给用户的说明信息，默认为「标化材料制作已准备好，请点击下方按钮前往制作」",
            required=False,
        ),
    ]
    thinking_hint = "正在准备标化材料跳转…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        message = (args.get("message") or (
            "标化材料制作已准备好！请点击下方按钮前往制作，"
            "在那里您可以基于固定模板快速完成材料制作。"
        )).strip()

        report_url = _DEFAULT_REPORT_URL

        logger.info("[StdRedirect] Showing A2UI redirect card → %s", report_url)

        payload = _build_a2ui_payload(message, report_url)

        return AgentToolResult.a2ui_result(
            tool_call.id,
            payload,
            llm_digest=f"已展示跳转卡片，用户可前往制作：{report_url}",
        )
