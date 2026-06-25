"""docgen_project_init — 初始化 DocGen 项目目录

创建标准项目目录结构和 manifest.json，返回 project_id 和 project_path。
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
from egis_agent_plugins.core.tools.docgen._project_model import ProjectStore, append_event

logger = logging.getLogger(__name__)


class DocgenProjectInitTool(AgentTool):
    """初始化 DocGen 项目

    在 projects_dir/YYYYMMDD/ 下创建标准项目结构:
    sources/uploads, sources/parsed, templates, drafts, output, exports, cache/。
    """

    name = "docgen_project_init"
    description = (
        "初始化 DocGen 项目目录，创建标准目录结构和 manifest.json。"
        "返回 project_id 和 project_path。"
        "所有 docgen workflow 都必须先调用此工具创建项目。"
    )
    parameters = [
        ToolParameter(
            name="project_name",
            type="string",
            description="项目名称（建议不含空格）。示例: 'tender_doc'",
            required=True,
        ),
        ToolParameter(
            name="workflow_id",
            type="string",
            description="Workflow 标识。示例: 'docgen.word_standard.tender'",
            required=True,
        ),
        ToolParameter(
            name="document_kind",
            type="string",
            description="文档类型: 'word' 或 'ppt'。默认 'word'",
            required=False,
        ),
    ]
    thinking_hint = "正在初始化 DocGen 项目…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_name = args.get("project_name", "").strip()
        workflow_id = args.get("workflow_id", "").strip()
        document_kind = args.get("document_kind", "word").strip()

        if not project_name:
            return AgentToolResult.error_result(tool_call.id, "project_name is required")
        if not workflow_id:
            return AgentToolResult.error_result(tool_call.id, "workflow_id is required")

        ctx = context or {}
        store = ProjectStore()

        try:
            project_path, manifest = store.init_project(
                project_name,
                workflow=workflow_id,
                document_kind=document_kind,
                session_id=str(ctx.get("session_id", "")),
                user_id=str(ctx.get("user_id", "")),
            )
        except Exception as e:
            logger.error("[DocgenProjectInit] Failed: %s", e)
            return AgentToolResult.error_result(tool_call.id, f"Failed to create project: {e}")

        proj_path_str = str(project_path)
        logger.info("[DocgenProjectInit] Created project: %s", proj_path_str)

        content = (
            f"项目已创建:\n"
            f"  project_id: {manifest.project_id}\n"
            f"  project_path: {proj_path_str}\n"
            f"  workflow: {workflow_id}\n"
            f"  document_kind: {document_kind}"
        )

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=content,
            is_error=False,
            metadata={
                "project_id": manifest.project_id,
                "project_path": proj_path_str,
                "state_delta": {
                    "docgen_state.project_id": manifest.project_id,
                    "docgen_state.project_path": proj_path_str,
                },
            },
            events=[],
        )

        digest = FrontendDigest(
            tool_name="docgen_project_init",
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(
                title=project_name,
                summary="项目目录已创建完成",
                icon="docgen",
                status="success",
            ),
            detailed=DetailedView(
                title="项目初始化",
                sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"{project_name} 项目已创建"),
                    ViewSection(heading="路径", content_type="text", data=proj_path_str),
                ],
            ),
        )
        apply_dual_layer(result, digest, content)

        return result
