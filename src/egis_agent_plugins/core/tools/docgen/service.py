"""DocGen 公共 Service 层 — 封装原子能力供 Flow 和原子工具共同复用

本模块是 `_project_model`、`source/parse_mineru`、`word_standard/build_template`
等内部模块的公共接口层。Agent 层的 Flow（如 tender_template / tender_fill）
应通过本模块调用公共能力，不要直接 import `_` 前缀的内部函数。

能力清单:
- 项目管理: ProjectStore, ArtifactMeta, write_artifact, read_artifact, append_event
- 文档解析: parse_document_to_markdown()
- 模板生成: extract_sections_from_markdown(), build_tender_template()
- 文档转换: template_to_markdown()
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

# ── Re-export: 项目模型层 ──────────────────────────────────────────────────

from egis_agent_plugins.core.tools.docgen._project_model import (
    ProjectStore,
    ArtifactMeta,
    append_event,
    write_artifact,
    read_artifact,
)

logger = logging.getLogger(__name__)


def get_state_value(context: dict[str, Any], dotted_key: str, default: Any = "") -> Any:
    """Read state from either flattened keys or nested runtime state.

    Some runtimes store state_delta keys such as ``docgen_state.project_path`` as
    nested ``{"docgen_state": {"project_path": ...}}``. DocGen flows accept both
    shapes so artifact writes never fall back to ``Path("")``.
    """
    if dotted_key in context:
        return context.get(dotted_key, default)

    cur: Any = context
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def create_relative_download_url(project_path: Path, file_rel: str) -> str:
    """Create a relative signed download URL for a project artifact."""
    from egis_agent_plugins.core.service.download.token_handler import encode_download_token

    token = encode_download_token(str(project_path.resolve()), file_rel)
    return f"/api/download/{token}"


# ── 文档解析 ───────────────────────────────────────────────────────────────


def parse_document_to_markdown(
    input_file: Path,
    project_path: Path,
) -> tuple[str, str]:
    """将上传文档解析为 Markdown（三级回退：MinerU Python API → CLI → 简单提取）

    Args:
        input_file: 待解析文件路径
        project_path: 项目根目录（用于 MinerU 缓存输出）

    Returns:
        (md_content, parse_method) — Markdown 内容 + 解析方法标识
    """
    from egis_agent_plugins.core.tools.docgen.source.parse_mineru import (
        _try_mineru_python_api,
        _try_mineru_cli,
        _fallback_simple_extract,
        MINERU_BACKEND,
    )

    mineru_cache = project_path / "cache" / "mineru"
    mineru_cache.mkdir(parents=True, exist_ok=True)

    md_content = _try_mineru_python_api(input_file, mineru_cache, MINERU_BACKEND)
    method = "mineru"

    if md_content is None:
        md_content = _try_mineru_cli(input_file, mineru_cache, MINERU_BACKEND)
        if md_content is not None:
            method = "mineru_cli"

    if md_content is None:
        md_content = _fallback_simple_extract(input_file)
        method = "fallback"

    return md_content, method


# ── 章节提取 & 模板生成 ──────────────────────────────────────────────────


def extract_sections_from_markdown(markdown: str) -> list[dict[str, Any]]:
    """从 Markdown 中提取章节结构

    Args:
        markdown: Markdown 文本

    Returns:
        章节列表，每个元素包含 level / title / content
    """
    from egis_agent_plugins.core.tools.docgen.word_standard.build_template import (
        _extract_sections,
    )
    return _extract_sections(markdown)


def build_tender_template(
    sections: list[dict[str, Any]],
    source_artifact_key: str = "tender_markdown",
) -> dict[str, Any]:
    """基于招标章节列表构建投标模板 JSON

    Args:
        sections: extract_sections_from_markdown() 返回的章节列表
        source_artifact_key: 来源 artifact 键名

    Returns:
        投标模板 dict（可 json.dumps 后保存）
    """
    template: dict[str, Any] = {
        "document_title": sections[0]["title"] if sections else "投标材料",
        "sections": [],
        "metadata": {
            "source_artifact": source_artifact_key,
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

    return template


# ── 模板 → Markdown 转换 ──────────────────────────────────────────────────


def template_to_markdown(
    template_data: dict[str, Any],
    title: str | None = None,
) -> str:
    """将模板/填充数据转为 Markdown 文本

    Args:
        template_data: 模板 dict（含 sections 数组）
        title: 文档标题，默认取 template_data["document_title"]

    Returns:
        Markdown 文本
    """
    doc_title = title or template_data.get("document_title", "投标材料")
    lines = [f"# {doc_title}", ""]

    for sec in template_data.get("sections", []):
        level = sec.get("level", 1)
        sec_title = sec.get("title", "")
        content = sec.get("content", "") or sec.get("rag_result", "") or ""

        prefix = "#" * min(level + 1, 6)
        lines.append(f"{prefix} {sec_title}")
        lines.append("")
        if content:
            lines.append(content)
            lines.append("")

    return "\n".join(lines)


# ── 文件保存辅助 ───────────────────────────────────────────────────────────


def save_upload_file(
    source_path: Path,
    project_path: Path,
) -> Path:
    """将上传文件复制到项目的 sources/uploads/ 目录

    Args:
        source_path: 原始上传文件路径
        project_path: 项目根目录

    Returns:
        目标路径（绝对路径）
    """
    dest_dir = project_path / "sources" / "uploads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source_path.name
    shutil.copy2(str(source_path), str(dest))
    return dest


def save_markdown(
    content: str,
    filename: str,
    project_path: Path,
    subdir: str = "sources/parsed",
) -> Path:
    """将 Markdown 内容保存到项目指定子目录

    Args:
        content: Markdown 文本
        filename: 文件名（不含路径）
        project_path: 项目根目录
        subdir: 子目录（相对项目根），默认 sources/parsed

    Returns:
        保存路径（绝对路径）
    """
    target_dir = project_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_text(content, encoding="utf-8")
    return target


def save_json_artifact(
    data: dict[str, Any],
    filename: str,
    project_path: Path,
    subdir: str = "templates",
) -> Path:
    """将 JSON 数据保存到项目指定子目录

    Args:
        data: JSON 数据
        filename: 文件名
        project_path: 项目根目录
        subdir: 子目录

    Returns:
        保存路径（绝对路径）
    """
    target_dir = project_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def save_draft_markdown(
    content: str,
    filename: str,
    project_path: Path,
) -> Path:
    """保存草稿 Markdown 到 drafts/ 目录"""
    return save_markdown(content, filename, project_path, subdir="drafts")
