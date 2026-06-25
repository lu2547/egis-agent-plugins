"""docgen_word_standard_open_editor — 打开 Word 编辑器

推送 docx-editor 所需的 frontend payload，让用户在 eigenpal/docx-editor 中
查看、编辑和下载生成的 Word 文档。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult

from egis_agent_plugins.core.service.download.token_handler import encode_download_token
from egis_agent_plugins.core.tools.docgen._project_model import ProjectStore, append_event

logger = logging.getLogger(__name__)

# docx-editor 服务地址
DOCX_EDITOR_URL = os.getenv("DOCGEN_DOCX_EDITOR_URL", "/docgen-editor")


def _build_a2ui_payload(
    project_id: str,
    docx_url: str,
    title: str,
) -> dict[str, Any]:
    """构建 A2UI 组件树：Word 编辑器卡片。"""
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
                            "editor-frame",
                        ]},
                    }
                },
            },
            {
                "id": "title-text",
                "component": {
                    "Text": {
                        "text": {"literalString": f"Word 编辑器 — {title}"},
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
                        "text": {"literalString": "您可以在下方编辑器中查看和修改文档内容，完成后点击保存。"},
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
                "id": "editor-frame",
                "component": {
                    "DocgenEditor": {
                        "project_id": project_id,
                        "docx_url": docx_url,
                        "editor_url": DOCX_EDITOR_URL,
                        "title": title,
                    }
                },
            },
        ],
        "data": {
            "project_id": project_id,
            "docx_url": docx_url,
            "editor_url": DOCX_EDITOR_URL,
        },
    }


class DocgenWordStandardOpenEditorTool(AgentTool):
    """打开 Word 编辑器卡片

    推送 docx-editor 所需的 payload，让用户在编辑器中查看和编辑文档。
    调用后应紧跟 final_answer(is_blocking=true) 等待用户编辑。
    """

    name = "docgen_word_standard_open_editor"
    description = (
        "打开 Word 编辑器卡片，让用户在 eigenpal/docx-editor 中查看和编辑生成的文档。\n"
        "调用后必须紧跟 final_answer(is_blocking=true) 等待用户编辑完成。\n"
        "用户编辑完成后，前端会发送保存确认消息。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="word_artifact_key",
            type="string",
            description="Word 文档的 artifact 键名。默认: 'draft_word'",
            required=False,
        ),
    ]
    thinking_hint = "正在准备 Word 编辑器…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        word_key = args.get("word_artifact_key", "draft_word").strip()

        if not project_path_str:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project_path = Path(project_path_str)
        store = ProjectStore()
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        word_meta = manifest.get_artifact(word_key)
        if word_meta is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Word artifact '{word_key}' not found. Generate the document first."
            )

        docx_abs_path = project_path / word_meta.path
        if word_meta.file_format != "docx":
            return AgentToolResult.error_result(
                tool_call.id,
                f"Word editor requires a .docx artifact, got {word_meta.file_format}: {word_meta.path}",
            )
        if not docx_abs_path.exists() or docx_abs_path.stat().st_size == 0:
            return AgentToolResult.error_result(
                tool_call.id,
                f"Word artifact file not found or empty: {word_meta.path}",
            )

        token = encode_download_token(str(project_path.resolve()), word_meta.path)
        docx_url = f"/api/download/{token}"

        title = word_meta.metadata.get("title", manifest.project_name)

        project_id = manifest.project_id

        append_event(
            project_path,
            "editor_opened",
            step=self.name,
            artifact_id=word_key,
        )

        logger.info("[DocgenOpenEditor] Opening editor for %s", docx_abs_path)

        payload = _build_a2ui_payload(
            project_id=project_id,
            docx_url=docx_url,
            title=title,
        )

        return AgentToolResult.a2ui_result(
            tool_call.id,
            payload,
            llm_digest=f"已展示 Word 编辑器卡片，文档: {title}，等待用户编辑",
        )
