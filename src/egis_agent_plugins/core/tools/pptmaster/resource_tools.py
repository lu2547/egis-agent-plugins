"""资源读取与图标搜索工具

ppt_read_file  — 读取 skill 静态资源（references/、templates/）或项目运行时文件（spec_lock.md 等）
ppt_search_icons — 搜索 templates/icons/ 下的图标库
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)
from ._base import PROJECTS_DIR, SKILL_DIR, assert_within_allowed_roots

import logging

logger = logging.getLogger(__name__)

_SKILL_DIR = os.getenv("PPT_MASTER_SKILL_DIR", "")
_PROJECTS_DIR = os.getenv("PPT_MASTER_PROJECTS_DIR", "")

# 允许读取的文件扩展名白名单（防止读取二进制文件）
_READABLE_SUFFIXES = {".md", ".txt", ".json", ".svg", ".html", ".css", ".yaml", ".yml"}

# 文件大小上限（防止超大文件撑爆 context）
_MAX_FILE_SIZE = 128 * 1024  # 128 KB
# 超大文件未指定行范围时的默认截断行数（避免直接报错打断 Agent 流程）
_OVERSIZE_DEFAULT_LINES = 800

# Base64 data URI 正则：匹配 SVG/HTML 内嵌的 base64 图片/音频/视频
# LLM 无法解读 base64 二进制，但这些数据可达数百 KB，需替换为占位符
_BASE64_DATA_URI_RE = re.compile(
    r"data:(image|audio|video|application)/[\w.+-]+;base64,"
    r"[A-Za-z0-9+/\s]+=*"
)


def _strip_base64_data_uris(text: str) -> str:
    """将 base64 data URI 替换为大小占位符，保留文件结构语义。

    例：data:image/png;base64,AAAA...（180KB）
      → [BASE64_IMAGE:180KB]
    """
    def _replacer(m: re.Match) -> str:
        media_type = m.group(1)  # image / audio / video / application
        size_kb = len(m.group(0)) // 1024
        tag = media_type.upper()  # IMAGE / AUDIO / VIDEO / APPLICATION
        return f"[BASE64_{tag}:{size_kb}KB]"

    return _BASE64_DATA_URI_RE.sub(_replacer, text)


def _resolve_path(file_path: str) -> Path:
    """解析文件路径。

    支持三种形式：
    1. 绝对路径 → 直接使用
    2. `references/<file>` / `templates/<file>` → 相对于 PPT_MASTER_SKILL_DIR
    3. 其他相对路径 → 相对于 PPT_MASTER_PROJECTS_DIR（项目文件）
    """
    p = Path(file_path)
    if p.is_absolute():
        return p

    # skill 资源目录（references/ templates/）
    if _SKILL_DIR and (
        file_path.startswith("references/")
        or file_path.startswith("templates/")
    ):
        return Path(_SKILL_DIR) / file_path

    # 项目文件：相对路径基于 PROJECTS_DIR，按 YYYYMMDD 分层解析
    if _PROJECTS_DIR:
        from ._base import resolve_projects_relative
        return resolve_projects_relative(file_path, Path(_PROJECTS_DIR))

    return p


class ReadFileTool(AgentTool):
    """读取文件内容并返回给 LLM。

    用途：
    - 读取 skill 静态资源：references/strategist.md、references/executor-base.md、
      templates/design_spec_reference.md、templates/spec_lock_reference.md 等
    - 读取项目运行时文件：<project_path>/spec_lock.md（Executor 每页必读）、
      <project_path>/design_spec.md、<project_path>/templates/<layout>/design_spec.md 等

    路径规则：
    - 绝对路径：直接读取
    - references/<file> 或 templates/<file>：相对于 PPT_MASTER_SKILL_DIR
    - 其他相对路径：相对于 PPT_MASTER_PROJECTS_DIR
    """

    name = "ppt_read_file"
    description = (
        "读取文件并返回内容。"
        "用于读取 skill 参考文档（references/*.md）、"
        "模板（templates/*.md）或项目运行时文件"
        "（spec_lock.md、design_spec.md、SVG 页面、notes/total.md 等）。"
        "路径规则：绝对路径直接使用；"
        "'references/' 和 'templates/' 前缀 → PPT_MASTER_SKILL_DIR；"
        "其他相对路径 → PPT_MASTER_PROJECTS_DIR。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description=(
                "要读取的文件路径。示例："
                "'references/strategist.md'、"
                "'templates/design_spec_reference.md'、"
                "'/abs/path/to/project/spec_lock.md'"
            ),
            required=True,
        ),
        ToolParameter(
            name="start_line",
            type="integer",
            description="可选：起始行号（从 1 开始，含）。",
            required=False,
        ),
        ToolParameter(
            name="end_line",
            type="integer",
            description="可选：结束行号（从 1 开始，含）。",
            required=False,
        ),
    ]
    thinking_hint = "正在读取文件…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        raw_path = (args.get("file_path") or "").strip()
        if not raw_path:
            return AgentToolResult.error_result(tool_call.id, "file_path is required")

        resolved = _resolve_path(raw_path)

        # 相对路径兜底：从 session state 的 pptmaster_state.project_path 解析
        if not resolved.exists() and not Path(raw_path).is_absolute():
            pptmaster = (context or {}).get("pptmaster_state", {})
            ppt_path = pptmaster.get("project_path", "")
            if ppt_path:
                alt = Path(ppt_path) / raw_path
                if alt.exists():
                    resolved = alt
        if not resolved.exists():
            return AgentToolResult.error_result(
                tool_call.id,
                f"File not found: {resolved} (resolved from '{raw_path}')"
            )

        # 路径沙箱校验：允许读取 SKILL_DIR 或 PROJECTS_DIR 内的文件（根据配置）
        try:
            resolved = assert_within_allowed_roots(resolved, [SKILL_DIR, PROJECTS_DIR])
        except PermissionError as exc:
            logger.warning("[ReadFile] 拒绝读取沙箱外文件: %s", exc)
            return AgentToolResult.error_result(
                tool_call.id,
                f"拒绝读取沙箱外文件: {raw_path}",
            )

        if resolved.suffix.lower() not in _READABLE_SUFFIXES:
            return AgentToolResult.error_result(
                tool_call.id,
                f"File type '{resolved.suffix}' is not in the readable whitelist: {sorted(_READABLE_SUFFIXES)}"
            )

        file_size = resolved.stat().st_size
        oversize_truncated = False
        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return AgentToolResult.error_result(tool_call.id, f"Failed to read file: {exc}")

        # SVG/HTML 文件：脱敏 base64 data URI（LLM 无法解读，但单张图可达 100KB+）
        if resolved.suffix.lower() in (".svg", ".html"):
            text = _strip_base64_data_uris(text)

        # 按行范围裁剪
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        # 超大文件且未显式指定行范围：自动截断前 _OVERSIZE_DEFAULT_LINES 行，避免打断 Agent
        if (
            file_size > _MAX_FILE_SIZE
            and start_line is None
            and end_line is None
        ):
            end_line = min(_OVERSIZE_DEFAULT_LINES, total_lines)
            oversize_truncated = True
            logger.info(
                "[ReadFile] 文件超过 %dB（实际 %dB），自动读取前 %d/%d 行；如需更多内容请指定 start_line/end_line",
                _MAX_FILE_SIZE, file_size, end_line, total_lines,
            )

        if start_line is not None or end_line is not None:
            s = max(0, (start_line or 1) - 1)
            e = (end_line or total_lines)
            lines = lines[s:e]
            text = "".join(lines)
            range_info = f" (lines {s+1}–{min(e, total_lines)} of {total_lines})"
            if oversize_truncated:
                range_info += f" [auto-truncated: file is {file_size}B > {_MAX_FILE_SIZE}B; pass start_line/end_line to read more]"
        else:
            range_info = f" ({total_lines} lines)"

        logger.debug("[ReadFile] Read %s%s", resolved, range_info)
        output = f"=== {resolved}{range_info} ===\n{text}"
        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=output,
            is_error=False,
            metadata={
                "file_path": str(resolved),
                "total_lines": total_lines,
                "size_bytes": file_size,
            },
            events=[],
        )
        digest = FrontendDigest(
            tool_name="ppt_read_file",
            display_type=ToolDisplayType.DATA,
            minimal=MinimalView(title=resolved.name, summary=f"读取了该文件 {total_lines} 行内容", icon="pptmaster", status="success"),
            detailed=DetailedView(title="文件读取", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{resolved.name} | {total_lines}行 | {file_size}B"),
            ]),
        )
        apply_dual_layer(result, digest, output)
        return result


class SearchIconsTool(AgentTool):
    """搜索图标库，返回匹配的图标名称列表。

    搜索 PPT_MASTER_SKILL_DIR/templates/icons/ 下三个图标库：
    - chunk/         — 主推，风格简洁
    - tabler-filled/ — 填充风格
    - tabler-outline/ — 线框风格

    支持关键词模糊匹配（不区分大小写）。
    """

    name = "ppt_search_icons"
    description = (
        "在图标库中搜索匹配关键词的图标。"
        "搜索范围涵盖三个图标库：chunk/、tabler-filled/、tabler-outline/。"
        "返回格式为 '<图标库>/<图标名>'，配合 SVG 中 {{icon:库/名称}} 占位语法使用。"
        "示例：搜索 'chart' 找到所有图表相关图标。"
    )
    parameters = [
        ToolParameter(
            name="keyword",
            type="string",
            description="搜索关键词（不区分大小写）。示例：'chart'、'home'、'user'。",
            required=True,
        ),
        ToolParameter(
            name="library",
            type="string",
            description=(
                "可选：限定搜索的图标库。"
                "可选值：'chunk'、'tabler-filled'、'tabler-outline'。"
                "不填则搜索全部三个。"
            ),
            required=False,
        ),
        ToolParameter(
            name="max_results",
            type="integer",
            description="最大返回结果数（默认 30）。",
            required=False,
        ),
    ]
    thinking_hint = "正在搜索图标库…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        keyword = (args.get("keyword") or "").strip().lower()
        library_filter = (args.get("library") or "").strip()
        max_results = int(args.get("max_results") or 30)

        if not keyword:
            return AgentToolResult.error_result(tool_call.id, "keyword is required")
        if not _SKILL_DIR:
            return AgentToolResult.error_result(
                tool_call.id, "PPT_MASTER_SKILL_DIR environment variable is not set"
            )

        icons_dir = Path(_SKILL_DIR) / "templates" / "icons"
        if not icons_dir.exists():
            return AgentToolResult.error_result(
                tool_call.id, f"Icons directory not found: {icons_dir}"
            )

        libraries = ["chunk", "tabler-filled", "tabler-outline"]
        if library_filter:
            if library_filter not in libraries:
                return AgentToolResult.error_result(
                    tool_call.id,
                    f"Invalid library '{library_filter}'. Must be one of: {libraries}"
                )
            libraries = [library_filter]

        matches: list[str] = []
        counts: dict[str, int] = {}
        for lib in libraries:
            lib_dir = icons_dir / lib
            if not lib_dir.exists():
                continue
            lib_matches: list[str] = []
            for svg_file in lib_dir.iterdir():
                if svg_file.suffix == ".svg" and keyword in svg_file.stem.lower():
                    lib_matches.append(f"{lib}/{svg_file.stem}")
            lib_matches.sort()
            counts[lib] = len(lib_matches)
            matches.extend(lib_matches)

        total = len(matches)
        matches = matches[:max_results]

        if not matches:
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=f"No icons found matching '{keyword}' in {libraries}.",
                is_error=False,
                metadata={"keyword": keyword, "total": 0},
                events=[],
            )
            digest = FrontendDigest(
                tool_name="ppt_search_icons",
                display_type=ToolDisplayType.SEARCH,
                minimal=MinimalView(title=f"'{keyword}'", summary="没有找到匹配的图标", icon="pptmaster", status="info"),
                detailed=DetailedView(title="图标搜索结果", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"'{keyword}' 未找到匹配图标"),
                ]),
            )
            apply_dual_layer(result, digest, f"[PPTMaster] 图标搜索 '{keyword}': 无结果")
            return result

        lines = [f"Icons matching '{keyword}' ({total} total, showing {len(matches)}):"]
        for lib in libraries:
            lib_icons = [m for m in matches if m.startswith(lib + "/")]
            if lib_icons:
                lines.append(f"\n### {lib} ({counts.get(lib, 0)} matches):")
                lines.extend(f"  {{{{icon:{name}}}}}" for name in lib_icons)

        output = "\n".join(lines)
        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=output,
            is_error=False,
            metadata={
                "keyword": keyword,
                "total": total,
                "returned": len(matches),
                "counts_by_library": counts,
            },
            events=[],
        )
        digest = FrontendDigest(
            tool_name="ppt_search_icons",
            display_type=ToolDisplayType.SEARCH,
            minimal=MinimalView(title=f"'{keyword}'", summary=f"找到 {total} 个匹配的图标", icon="pptmaster", status="success"),
            detailed=DetailedView(title="图标搜索结果", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"'{keyword}' 命中 {total} 个图标"),
            ]),
        )
        apply_dual_layer(result, digest, output)
        return result
