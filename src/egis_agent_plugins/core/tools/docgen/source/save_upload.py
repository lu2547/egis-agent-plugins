"""docgen_source_save_upload — 保存上传文件到项目

将用户上传的文件复制到项目 sources/uploads/ 目录并注册为 artifact。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.tools.docgen._project_model import (
    ProjectStore, ArtifactMeta, append_event,
)

logger = logging.getLogger(__name__)


class DocgenSourceSaveUploadTool(AgentTool):
    """保存上传文件到项目目录

    将文件复制到 project/sources/uploads/ 并注册为 artifact。
    """

    name = "docgen_source_save_upload"
    description = (
        "将用户上传的文件保存到项目目录并注册为 artifact。"
        "通常在用户上传文件后调用。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="file_path",
            type="string",
            description="上传文件的源路径（绝对路径）",
            required=True,
        ),
        ToolParameter(
            name="artifact_key",
            type="string",
            description="artifact 键名。默认: 'upload'",
            required=False,
        ),
    ]
    thinking_hint = "正在保存上传文件…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        file_path_str = args.get("file_path", "").strip()
        artifact_key = args.get("artifact_key", "upload").strip()

        if not project_path_str or not file_path_str:
            return AgentToolResult.error_result(
                tool_call.id, "project_path and file_path are required"
            )

        project_path = Path(project_path_str)
        source_file = Path(file_path_str)

        if not source_file.exists():
            return AgentToolResult.error_result(
                tool_call.id, f"Source file not found: {file_path_str}"
            )

        # 确定目标路径
        dest_dir = project_path / "sources" / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / source_file.name

        try:
            shutil.copy2(str(source_file), str(dest_file))
        except Exception as e:
            return AgentToolResult.error_result(
                tool_call.id, f"Failed to copy file: {e}"
            )

        # 推断 file_format
        suffix = source_file.suffix.lower().lstrip(".")
        file_format = suffix if suffix else "unknown"

        # 注册 artifact
        relative_path = str(dest_file.relative_to(project_path))
        meta = ArtifactMeta(
            artifact_id=artifact_key,
            kind="upload",
            file_format=file_format,
            path=relative_path,
            created_by=self.name,
            metadata={"original_name": source_file.name},
        )

        store = ProjectStore()
        store.register_artifact(project_path, artifact_key, meta)

        append_event(
            project_path,
            "file_uploaded",
            step=self.name,
            artifact_id=artifact_key,
            file_name=source_file.name,
        )

        dest_str = str(dest_file)
        logger.info("[DocgenSaveUpload] Saved %s → %s", source_file.name, dest_str)

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=(
                f"文件已保存:\n"
                f"  artifact_key: {artifact_key}\n"
                f"  path: {relative_path}\n"
                f"  format: {file_format}\n"
                f"  size: {dest_file.stat().st_size} bytes"
            ),
            is_error=False,
            metadata={
                "artifact_id": artifact_key,
                "artifact_path": dest_str,
                "state_delta": {
                    f"docgen_state.artifacts.{artifact_key}": dest_str,
                    "docgen_state.upload_artifact_id": artifact_key,
                },
            },
            events=[],
        )
