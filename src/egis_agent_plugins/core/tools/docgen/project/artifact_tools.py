"""docgen artifact 管理工具

- docgen_project_write_artifact: 写入 artifact 文件并注册到 manifest
- docgen_project_read_artifact:  读取 artifact 内容
- docgen_project_list_artifacts: 列出项目下的 artifacts
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.tools.docgen._project_model import (
    ProjectStore, ArtifactMeta, append_event,
)
from egis_agent_plugins.core.tools.docgen._project_model.artifacts import (
    write_artifact as _write_artifact_file,
    read_artifact as _read_artifact_file,
    list_artifacts as _list_artifacts,
)

logger = logging.getLogger(__name__)


class DocgenWriteArtifactTool(AgentTool):
    """写入 artifact 到项目目录并注册到 manifest"""

    name = "docgen_project_write_artifact"
    description = (
        "将内容写入项目目录并注册为 artifact。"
        "支持直接写入文本/二进制内容，或从源文件复制。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="artifact_key",
            type="string",
            description="artifact 在 manifest 中的键名。示例: 'tender_markdown'",
            required=True,
        ),
        ToolParameter(
            name="kind",
            type="string",
            description="artifact 类型: upload / parsed / template / word_draft / word_final",
            required=True,
        ),
        ToolParameter(
            name="file_format",
            type="string",
            description="文件格式: pdf / docx / md / json / txt",
            required=True,
        ),
        ToolParameter(
            name="path",
            type="string",
            description="相对于项目根目录的文件路径。示例: 'sources/parsed/tender.md'",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="文件文本内容（与 source_file 二选一）",
            required=False,
        ),
        ToolParameter(
            name="source_file",
            type="string",
            description="源文件路径，复制到项目目录（与 content 二选一）",
            required=False,
        ),
        ToolParameter(
            name="metadata",
            type="string",
            description="附加元数据 JSON 字符串。示例: '{\"title\": \"招标文件\"}'",
            required=False,
        ),
    ]
    thinking_hint = "正在写入 artifact…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        artifact_key = args.get("artifact_key", "").strip()
        kind = args.get("kind", "").strip()
        file_format = args.get("file_format", "").strip()
        path = args.get("path", "").strip()
        content = args.get("content")
        source_file = args.get("source_file")
        metadata_str = args.get("metadata", "{}")

        if not project_path_str or not artifact_key or not kind or not file_format or not path:
            return AgentToolResult.error_result(
                tool_call.id, "project_path, artifact_key, kind, file_format, path are required"
            )

        project_path = Path(project_path_str)
        store = ProjectStore()

        # 解析 metadata
        metadata: dict[str, Any] = {}
        if metadata_str:
            try:
                metadata = json.loads(metadata_str)
            except json.JSONDecodeError:
                pass

        meta = ArtifactMeta(
            artifact_id=artifact_key,
            kind=kind,
            file_format=file_format,
            path=path,
            created_by=self.name,
            metadata=metadata,
        )

        try:
            artifact_file = _write_artifact_file(
                project_path, meta,
                content=content,
                source_file=source_file if source_file else None,
            )
        except Exception as e:
            return AgentToolResult.error_result(tool_call.id, f"Failed to write artifact: {e}")

        # 注册到 manifest
        store.register_artifact(project_path, artifact_key, meta)

        # 记录事件
        append_event(
            project_path,
            "artifact_written",
            step=self.name,
            artifact_id=artifact_key,
            artifact_kind=kind,
        )

        msg = f"Artifact '{artifact_key}' written to {artifact_file}"
        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=msg,
            is_error=False,
            metadata={
                "artifact_id": artifact_key,
                "artifact_path": str(artifact_file),
                "state_delta": {
                    f"docgen_state.artifacts.{artifact_key}": str(artifact_file),
                },
            },
            events=[],
        )


class DocgenReadArtifactTool(AgentTool):
    """读取 artifact 文件内容"""

    name = "docgen_project_read_artifact"
    description = "读取项目中指定 artifact 的文件内容（文本格式）。"
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="artifact_key",
            type="string",
            description="artifact 键名",
            required=True,
        ),
    ]
    thinking_hint = "正在读取 artifact…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        artifact_key = args.get("artifact_key", "").strip()

        if not project_path_str or not artifact_key:
            return AgentToolResult.error_result(
                tool_call.id, "project_path and artifact_key are required"
            )

        project_path = Path(project_path_str)
        store = ProjectStore()
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        meta = manifest.get_artifact(artifact_key)
        if meta is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Artifact '{artifact_key}' not found in manifest"
            )

        content = _read_artifact_file(project_path, meta, as_text=True)
        if content is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Artifact file not found: {meta.path}"
            )

        # 截断超大内容
        max_chars = 50000
        if isinstance(content, str) and len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... [truncated, total {len(content)} chars]"

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=f"Artifact: {artifact_key} ({meta.kind}, {meta.file_format})\nPath: {meta.path}\n\n{content}",
            is_error=False,
            metadata={"artifact_id": artifact_key},
            events=[],
        )


class DocgenListArtifactsTool(AgentTool):
    """列出项目下的 artifacts"""

    name = "docgen_project_list_artifacts"
    description = "列出项目中已注册的所有 artifacts 及其状态。"
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
    ]
    thinking_hint = "正在列出 artifacts…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()

        if not project_path_str:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project_path = Path(project_path_str)
        store = ProjectStore()
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        artifacts = manifest.artifacts
        if not artifacts:
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content="No artifacts registered yet.",
                is_error=False,
                metadata={},
                events=[],
            )

        lines = [f"Project artifacts ({len(artifacts)}):"]
        for key, meta in artifacts.items():
            if meta is None:
                lines.append(f"  {key}: (empty)")
            else:
                lines.append(
                    f"  {key}: {meta.kind} ({meta.file_format}) → {meta.path}"
                )

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content="\n".join(lines),
            is_error=False,
            metadata={"artifact_count": len(artifacts)},
            events=[],
        )
