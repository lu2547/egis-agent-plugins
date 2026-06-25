"""docgen_word_standard_build_template — 基于招标材料生成投标模板

读取已解析的招标材料 Markdown，提取文档结构，生成投标材料模板。
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


class DocgenWordStandardBuildTemplateTool(AgentTool):
    """基于招标材料生成投标材料模板

    读取已解析的 Markdown，提取文档大纲结构，生成投标模板 JSON。
    模板包含章节标题、要求、格式规范等信息，供后续 RAG 填充使用。
    """

    name = "docgen_word_standard_build_template"
    description = (
        "基于已解析的招标材料 Markdown 生成投标材料模板。\n"
        "模板包含章节结构、每个章节的填充要求。\n"
        "输出模板 JSON 保存为 artifact。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="tender_markdown_artifact_key",
            type="string",
            description="招标材料 Markdown 的 artifact 键名。默认: 'tender_markdown'",
            required=False,
        ),
        ToolParameter(
            name="output_artifact_key",
            type="string",
            description="输出模板的 artifact 键名。默认: 'template'",
            required=False,
        ),
    ]
    thinking_hint = "正在分析招标材料并生成投标模板…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        md_key = args.get("tender_markdown_artifact_key", "tender_markdown").strip()
        output_key = args.get("output_artifact_key", "template").strip()

        if not project_path_str:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project_path = Path(project_path_str)
        store = ProjectStore()
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        # 读取招标材料 Markdown
        md_meta = manifest.get_artifact(md_key)
        if md_meta is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Markdown artifact '{md_key}' not found. Parse the document first."
            )

        md_content = read_artifact(project_path, md_meta, as_text=True)
        if md_content is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Markdown file not found: {md_meta.path}"
            )

        # 提取文档大纲结构
        sections = _extract_sections(md_content)

        # 生成投标模板
        template = {
            "document_title": sections[0]["title"] if sections else "投标材料",
            "sections": [],
            "metadata": {
                "source_artifact": md_key,
                "total_sections": len(sections),
            },
        }

        for i, sec in enumerate(sections, 1):
            template["sections"].append({
                "id": f"section_{i}",
                "level": sec["level"],
                "title": sec["title"],
                "content_hint": sec.get("content", "")[:200],
                "fill_strategy": "rag_search",
                "required": True,
            })

        template_json = json.dumps(template, ensure_ascii=False, indent=2)

        # 保存模板
        template_path = f"templates/tender_template.json"
        meta = ArtifactMeta(
            artifact_id=output_key,
            kind="template",
            file_format="json",
            path=template_path,
            created_by=self.name,
            source_artifacts=[md_key],
            metadata={"section_count": len(sections)},
        )
        write_artifact(project_path, meta, content=template_json)
        store.register_artifact(project_path, output_key, meta)

        append_event(
            project_path,
            "template_generated",
            step=self.name,
            artifact_id=output_key,
            section_count=len(sections),
        )

        logger.info(
            "[DocgenBuildTemplate] Generated template with %d sections", len(sections),
        )

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=(
                f"投标模板已生成:\n"
                f"  模板路径: {template_path}\n"
                f"  章节数: {len(sections)}\n\n"
                f"模板结构:\n{_format_template_summary(template)}"
            ),
            is_error=False,
            metadata={
                "artifact_id": output_key,
                "section_count": len(sections),
                "state_delta": {
                    f"docgen_state.artifacts.{output_key}": template_path,
                    "docgen_state.template_artifact_id": output_key,
                },
            },
            events=[],
        )


def _extract_sections(markdown: str) -> list[dict[str, Any]]:
    """从 Markdown 中提取章节结构"""
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None

    for line in markdown.split("\n"):
        stripped = line.strip()

        # 检测标题行
        if stripped.startswith("#"):
            level = 0
            for ch in stripped:
                if ch == "#":
                    level += 1
                else:
                    break
            title = stripped[level:].strip()

            if current_section:
                sections.append(current_section)

            current_section = {
                "level": level,
                "title": title,
                "content": "",
            }
        elif current_section and stripped:
            if current_section["content"]:
                current_section["content"] += "\n"
            current_section["content"] += stripped

    if current_section:
        sections.append(current_section)

    # 如果没有检测到标题，把整个文档作为一个章节
    if not sections and markdown.strip():
        sections.append({
            "level": 1,
            "title": "文档内容",
            "content": markdown.strip()[:500],
        })

    return sections


def _format_template_summary(template: dict[str, Any]) -> str:
    """格式化模板摘要"""
    lines = []
    for sec in template.get("sections", []):
        indent = "  " * (sec["level"] - 1)
        lines.append(f"{indent}{sec['id']}: {sec['title']}")
    return "\n".join(lines)
