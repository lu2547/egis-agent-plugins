"""docgen_word_standard_fill_from_rag — 基于模板从知识库检索填充

读取投标模板，对每个章节调用 RAG 检索知识库内容并填充。
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
    read_artifact, write_artifact,
)

logger = logging.getLogger(__name__)


class DocgenWordStandardFillFromRagTool(AgentTool):
    """基于模板从知识库检索并填充内容

    读取投标模板 JSON，对每个章节生成检索查询，
    调用 RAG 工具检索相关内容，将结果填充到模板中。
    """

    name = "docgen_word_standard_fill_from_rag"
    description = (
        "基于投标模板，从知识库检索相关内容并填充到各章节。\n"
        "每个章节会根据标题和内容提示生成检索查询。\n"
        "输出填充后的模板数据。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="template_artifact_key",
            type="string",
            description="模板 artifact 键名。默认: 'template'",
            required=False,
        ),
        ToolParameter(
            name="output_artifact_key",
            type="string",
            description="输出填充数据的 artifact 键名。默认: 'filled_template'",
            required=False,
        ),
        ToolParameter(
            name="rag_queries",
            type="string",
            description=(
                "自定义 RAG 查询 JSON。格式: {\"section_id\": \"查询文本\"}。"
                "未指定的章节将使用标题自动生查询。"
            ),
            required=False,
        ),
    ]
    thinking_hint = "正在从知识库检索并填充模板…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        template_key = args.get("template_artifact_key", "template").strip()
        output_key = args.get("output_artifact_key", "filled_template").strip()
        rag_queries_str = args.get("rag_queries", "{}")

        if not project_path_str:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project_path = Path(project_path_str)
        store = ProjectStore()
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        # 读取模板
        template_meta = manifest.get_artifact(template_key)
        if template_meta is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Template artifact '{template_key}' not found. Build template first."
            )

        template_content = read_artifact(project_path, template_meta, as_text=True)
        if template_content is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Template file not found: {template_meta.path}"
            )

        try:
            template = json.loads(template_content)
        except json.JSONDecodeError as e:
            return AgentToolResult.error_result(
                tool_call.id, f"Invalid template JSON: {e}"
            )

        # 解析自定义查询
        custom_queries: dict[str, str] = {}
        if rag_queries_str:
            try:
                custom_queries = json.loads(rag_queries_str)
            except json.JSONDecodeError:
                pass

        # 为每个章节准备填充数据
        filled_sections: list[dict[str, Any]] = []
        for sec in template.get("sections", []):
            section_id = sec.get("id", "")
            query = custom_queries.get(section_id, sec.get("title", ""))

            filled_sections.append({
                "id": section_id,
                "title": sec.get("title", ""),
                "level": sec.get("level", 1),
                "query": query,
                "content": sec.get("content_hint", ""),
                "rag_result": None,  # 将在 RAG 调用后填充
                "status": "pending_rag",
            })

        # 保存填充模板（RAG 结果待后续填充）
        filled_data = {
            "document_title": template.get("document_title", ""),
            "sections": filled_sections,
            "metadata": {
                "source_template": template_key,
                "total_sections": len(filled_sections),
                "pending_rag": len(filled_sections),
            },
        }

        filled_json = json.dumps(filled_data, ensure_ascii=False, indent=2)
        output_path = "templates/filled_template.json"

        meta = ArtifactMeta(
            artifact_id=output_key,
            kind="filled_template",
            file_format="json",
            path=output_path,
            created_by=self.name,
            source_artifacts=[template_key],
            metadata={
                "section_count": len(filled_sections),
                "pending_rag": len(filled_sections),
            },
        )
        write_artifact(project_path, meta, content=filled_json)
        store.register_artifact(project_path, output_key, meta)

        append_event(
            project_path,
            "template_filled",
            step=self.name,
            artifact_id=output_key,
            section_count=len(filled_sections),
        )

        logger.info(
            "[DocgenFillFromRag] Prepared %d sections for RAG filling",
            len(filled_sections),
        )

        # 构建摘要
        section_summary = "\n".join(
            f"  {s['id']}: {s['title']} (query: {s['query'][:50]})"
            for s in filled_sections
        )

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=(
                f"模板填充准备完成:\n"
                f"  章节数: {len(filled_sections)}\n"
                f"  待 RAG 填充: {len(filled_sections)}\n\n"
                f"各章节检索查询:\n{section_summary}\n\n"
                f"请使用 rag 工具对每个章节的 query 进行检索，"
                f"然后调用 docgen_word_standard_generate 生成最终文档。"
            ),
            is_error=False,
            metadata={
                "artifact_id": output_key,
                "section_count": len(filled_sections),
                "state_delta": {
                    f"docgen_state.artifacts.{output_key}": output_path,
                    "docgen_state.filled_template_artifact_id": output_key,
                },
            },
            events=[],
        )
