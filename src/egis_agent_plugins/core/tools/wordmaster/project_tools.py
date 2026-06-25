"""Word Master 项目管理工具

docx_project_init — 初始化 Word 文档项目目录结构
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

from ._base import PROJECTS_DIR

logger = logging.getLogger(__name__)

# 项目子目录
_PROJECT_SUBDIRS = ["sources", "scripts", "output", "unpacked", "exports"]


def _sanitize_id(raw: str, max_len: int = 8) -> str:
    """移除特殊字符并截断，用于目录名安全拼接。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:max_len] or "anon"


class DocxProjectInitTool(AgentTool):
    """初始化 Word 文档项目目录

    在 PROJECTS_DIR/YYYYMMDD/ 下创建标准项目结构：
    sources/、scripts/、output/、unpacked/、exports/。
    """

    name = "docx_project_init"
    description = (
        "初始化新的 Word 文档项目目录，包含标准结构"
        "（sources/、scripts/、output/、unpacked/、exports/）。"
        "返回创建的项目路径。"
        "【提示】初始化后请立即 read_skill('wordmaster') 获取企业级样式模板后再编写 JS 代码。"
    )
    parameters = [
        ToolParameter(
            name="project_name",
            type="string",
            description="项目名称（建议不含空格）。示例：'quarterly_report'",
            required=True,
        ),
    ]
    thinking_hint = "正在初始化 Word 文档项目…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_name = args.get("project_name", "").strip()
        if not project_name:
            return AgentToolResult.error_result(tool_call.id, "project_name is required")

        # 从 context 提取 session/user ID 用于多用户隔离
        ctx = context or {}
        sid = _sanitize_id(str(ctx.get("session_id", "")))
        uid = _sanitize_id(str(ctx.get("user_id", "")))

        date_str = datetime.now().strftime("%Y%m%d")
        dir_name = f"{project_name}_{sid}_{uid}"

        if PROJECTS_DIR:
            project_path = PROJECTS_DIR / date_str / dir_name
        else:
            project_path = Path(date_str) / dir_name

        # 创建目录结构
        try:
            for sub in _PROJECT_SUBDIRS:
                (project_path / sub).mkdir(parents=True, exist_ok=True)
            proj_path_str = str(project_path.resolve())
            logger.info("[DocxProjectInit] Created project: %s", proj_path_str)

            proj_content = f"Project created: {proj_path_str}"
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=proj_content,
                is_error=False,
                metadata={
                    "project_name": project_name,
                    "project_path": proj_path_str,
                    "state_delta": {"wordmaster_state.project_path": proj_path_str},
                },
                events=[],
            )

            # 双层返回
            digest = FrontendDigest(
                tool_name="docx_project_init",
                display_type=ToolDisplayType.FILE,
                minimal=MinimalView(title=project_name, summary="项目目录已创建完成", icon="wordmaster", status="success"),
                detailed=DetailedView(title="项目初始化", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"{project_name} 目录已创建"),
                ]),
            )
            apply_dual_layer(result, digest, proj_content)

            return result
        except Exception as e:
            logger.error("[DocxProjectInit] Failed: %s", e)
            return AgentToolResult.error_result(tool_call.id, f"Failed to create project: {e}")
