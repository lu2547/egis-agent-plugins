"""docgen_source_upload_card — 文件上传卡片（A2UI）

展示文件上传区域，用户选择文件后前端通过 sendMessage 回传文件路径。
"""

from __future__ import annotations

import logging
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult

logger = logging.getLogger(__name__)


def _build_a2ui_payload(
    project_id: str,
    accepted_types: list[str],
    title: str,
    description: str,
) -> dict[str, Any]:
    """构建 A2UI 组件树：文件上传卡片。"""
    types_text = ", ".join(accepted_types) if accepted_types else ".pdf, .doc, .docx, .png, .jpg"

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
                        "backgroundColor": "#F7F9FC",
                        "padding": [16, 16, 16, 16],
                        "gap": 12,
                        "borderRadius": "big",
                        "children": {"explicitList": [
                            "title-text", "desc-text", "divider-01",
                            "upload-area", "types-text",
                        ]},
                    }
                },
            },
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
            {
                "id": "desc-text",
                "component": {
                    "Text": {
                        "text": {"literalString": description},
                        "color": "#666666",
                        "fontSize": "13px",
                    }
                },
            },
            {
                "id": "divider-01",
                "component": {
                    "Divider": {"borderColor": "#E8ECF2"}
                },
            },
            {
                "id": "upload-area",
                "component": {
                    "FileUpload": {
                        "project_id": project_id,
                        "accepted_types": accepted_types,
                        "action": {
                            "type": "sendMessage",
                            "message_template": "我已上传文件: {file_name}，路径: {file_path}",
                        },
                        "placeholder": "点击或拖拽文件到此处上传",
                        "max_size_mb": 50,
                    }
                },
            },
            {
                "id": "types-text",
                "component": {
                    "Text": {
                        "text": {"literalString": f"支持格式: {types_text}"},
                        "color": "#999999",
                        "fontSize": "11px",
                    }
                },
            },
        ],
        "data": {
            "project_id": project_id,
            "accepted_types": accepted_types,
        },
    }


class DocgenSourceUploadCardTool(AgentTool):
    """文件上传卡片（A2UI）

    展示文件上传区域，用户选择文件后前端通过 sendMessage 回传文件路径。
    调用后应紧跟 final_answer(is_blocking=true) 等待用户上传。
    """

    name = "docgen_source_upload_card"
    description = (
        "展示文件上传卡片，让用户上传招标材料或其他文档文件。\n"
        "调用后必须紧跟 final_answer(is_blocking=true) 等待用户上传文件。\n"
        "用户上传后，前端会发送文件路径信息。"
    )
    parameters = [
        ToolParameter(
            name="project_id",
            type="string",
            description="项目 ID（从 docgen_project_init 获取）",
            required=True,
        ),
        ToolParameter(
            name="accepted_types",
            type="string",
            description="允许的文件类型，逗号分隔。默认: '.pdf,.doc,.docx,.png,.jpg'",
            required=False,
        ),
        ToolParameter(
            name="title",
            type="string",
            description="卡片标题。默认: '请上传招标材料'",
            required=False,
        ),
        ToolParameter(
            name="description",
            type="string",
            description="卡片描述文本",
            required=False,
        ),
    ]
    thinking_hint = "正在展示文件上传卡片…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_id = args.get("project_id", "").strip()
        accepted_types_str = args.get("accepted_types", ".pdf,.doc,.docx,.png,.jpg").strip()
        title = args.get("title", "请上传招标材料").strip()
        description = args.get(
            "description",
            "请上传招标文件，支持 PDF、Word、图片等格式。上传后系统将自动解析为 Markdown。",
        ).strip()

        accepted_types = [t.strip() for t in accepted_types_str.split(",") if t.strip()]

        logger.info("[DocgenUploadCard] project_id=%s, types=%s", project_id, accepted_types)

        payload = _build_a2ui_payload(
            project_id=project_id,
            accepted_types=accepted_types,
            title=title,
            description=description,
        )

        return AgentToolResult.a2ui_result(
            tool_call.id,
            payload,
            llm_digest="已展示文件上传卡片，等待用户上传文件",
        )
