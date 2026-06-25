"""Word Master 文件读写工具

docx_write_file — 将 LLM 生成的内容写入项目目录
docx_read_file  — 读取项目文件或 unpacked XML
"""

from __future__ import annotations

import json
import logging
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

from ._base import PROJECTS_DIR, WordMasterBaseTool, coerce_path_under_state_project

logger = logging.getLogger(__name__)

_PROJECTS_DIR = os.getenv("WORD_MASTER_PROJECTS_DIR", "")

# 允许读取的文件扩展名白名单
_READABLE_SUFFIXES = {
    ".md", ".txt", ".json", ".xml", ".html", ".css",
    ".yaml", ".yml", ".js", ".rels",
    ".docx", ".pptx",
}

# 文件大小上限
_MAX_FILE_SIZE = 128 * 1024  # 128 KB
# 超大文件未指定行范围时的默认截断行数（避免直接报错打断 Agent 流程）
_OVERSIZE_DEFAULT_LINES = 800


# ─── DocxWriteFileTool ────────────────────────────────────────────────────────


class DocxWriteFileTool(AgentTool):
    """将文本内容写入指定文件路径。

    用于 Word Master pipeline 中 LLM 生成的所有文件：
    - JavaScript 生成脚本（scripts/generate_*.js）
    - unpacked XML 编辑（unpacked/word/document.xml 等）
    - Markdown 内容（sources/content.md）
    """

    name = "docx_write_file"
    description = (
        "将文本内容写入磁盘文件。"
        "用于保存 LLM 生成的文件到项目目录："
        "JS 脚本（scripts/*.js）、XML 编辑（unpacked/word/*.xml）、"
        "Markdown 内容（sources/*.md）等。"
        "父目录自动创建，已存在文件会被覆盖。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description=(
                "要写入的文件路径（绝对或相对于项目目录）。"
                "示例：'scripts/generate.js'、"
                "'/abs/path/to/project/unpacked/word/document.xml'"
            ),
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="要写入文件的完整文本内容。",
            required=True,
        ),
    ]
    thinking_hint = "正在写入文件…"

    @staticmethod
    def _parse_raw_args(raw: str) -> dict[str, Any]:
        """LLM 输出的 arguments JSON 解析失败时，尝试修复并提取 file_path 和 content。

        常见原因：
        1. JS 代码中含未转义的换行/双引号导致整体 JSON 解析失败
        2. 输出被截断（max_tokens 不足）导致 JSON 不完整
        3. 双重转义（_raw 嵌套）
        """
        # 处理双重 _raw 嵌套：{"_raw": "{\"file_path\": ..."}
        unwrapped = raw
        for _ in range(3):  # 最多剥 3 层
            try:
                parsed = json.loads(unwrapped)
                if isinstance(parsed, dict):
                    if "_raw" in parsed and len(parsed) == 1:
                        unwrapped = parsed["_raw"]
                        continue
                    return parsed
            except json.JSONDecodeError:
                break

        # 尝试反转义（处理 \\\"→\" 等情况）
        work = unwrapped.replace('\\"', '"').replace('\\\\', '\\\\')
        try:
            parsed = json.loads(work)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # 用正则提取 file_path 和 content（允许截断/无闭合引号）
        result: dict[str, Any] = {}

        # 提取 file_path：允许没有闭合引号（截断场景）
        for key in ("file_path", "path", "filepath"):
            # 先尝试完整匹配（有闭合引号）
            m = re.search(rf'["\\"]{key}["\\"\s]*:\s*["\\"]([ ^"\\\\]+)["\\"\s,}}]', work)
            if not m:
                # 允许截断：没有闭合引号
                m = re.search(rf'["\\"]{key}["\\"\s]*:\s*["\\"]([^"\\\\ ]+)', work)
            if not m:
                # 超宽松匹配：处理转义引号
                m = re.search(rf'{key}.*?:\s*\\?"([^"\\\\]+)', work)
            if m:
                result["file_path"] = m.group(1).rstrip('/\\\\ ')
                break

        # 提取 content
        content_match = re.search(r'["\\"](content|file_content)["\\"\s]*:\s*["\\"]', work)
        if content_match:
            start = content_match.end()
            tail = work[start:]
            tail = tail.rstrip()
            if tail.endswith("}"):
                tail = tail[:-1].rstrip()
            if tail.endswith('"'):
                tail = tail[:-1]
            # 反转义常见的 JSON 转义序列
            tail = tail.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"')
            result["content"] = tail

        if result:
            return result

        # 实在解析不了，返回原始
        return {"_raw": raw}

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        logger.info("[DocxWriteFile] raw args keys=%s", list(args.keys()))

        # ── 处理 _raw 降级：LLM 输出的 JSON 解析失败时 ark-agentic 会把原始字符串塞入 _raw ──
        if "_raw" in args and len(args) == 1:
            args = self._parse_raw_args(args["_raw"])
            logger.info("[DocxWriteFile] parsed _raw -> keys=%s", list(args.keys()))

        # 兼容 LLM 可能使用的参数名变体
        raw_file_path = (
            args.get("file_path")
            or args.get("path")
            or args.get("filepath")
            or args.get("filename")
            or ""
        ).strip()
        content = args.get("content") or args.get("file_content") or ""

        if not raw_file_path:
            # 尝试从 user:wordmaster_state 推导默认路径
            wm_state = (context or {}).get("user:wordmaster_state") or {}
            state_pp = wm_state.get("project_path", "") if isinstance(wm_state, dict) else ""
            if state_pp and content:
                # 根据 content 内容推断文件名
                if "require('docx')" in content or "require(\"docx\")" in content:
                    raw_file_path = "scripts/generate.js"
                elif content.strip().startswith("<"):
                    raw_file_path = "unpacked/word/document.xml"
                else:
                    raw_file_path = "scripts/generate.js"
                logger.warning(
                    "[DocxWriteFile] file_path missing, auto-inferred: %s",
                    raw_file_path,
                )
            elif state_pp and not content:
                # file_path 和 content 都丢失 — 很可能是 tool_call 参数 JSON 损坏/截断
                return AgentToolResult.error_result(
                    tool_call.id,
                    "file_path 和 content 都无法解析。\n"
                    "可能原因：JS 代码中的特殊字符导致 JSON 损坏。\n"
                    "请重试，并确保：\n"
                    "1. 所有中文文本使用反引号 ` 包裹\n"
                    "2. content 中的双引号必须转义为 \\\"\n"
                    "3. content 中的换行符必须转义为 \\n"
                )
            else:
                logger.warning("[DocxWriteFile] file_path missing! args=%s",
                               {k: v[:80] if isinstance(v, str) else v for k, v in args.items()})
                return AgentToolResult.error_result(
                    tool_call.id,
                    f"file_path is required. Received args: {list(args.keys())}"
                )
        if content is None:
            return AgentToolResult.error_result(tool_call.id, "content is required")

        # 以 session state 中的 project_path 为权威校正路径
        wm_state = (context or {}).get("user:wordmaster_state") or {}
        state_pp = wm_state.get("project_path", "") if isinstance(wm_state, dict) else ""

        if state_pp and raw_file_path:
            file_path, warn = coerce_path_under_state_project(
                raw_file_path,
                state_pp,
                Path(_PROJECTS_DIR) if _PROJECTS_DIR else None,
            )
            if warn:
                logger.warning("[DocxWriteFile] %s", warn)
        else:
            p = Path(raw_file_path)
            if p.is_absolute():
                file_path = str(p)
            elif state_pp:
                file_path = str(Path(state_pp) / p)
            else:
                file_path = raw_file_path

        if not file_path:
            return AgentToolResult.error_result(tool_call.id, "Could not resolve file_path")

        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
            logger.info("[DocxWriteFile] Written %d bytes to %s", size, file_path)
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=f"File written successfully: {file_path} ({size} bytes)",
                is_error=False,
                metadata={"file_path": file_path, "size_bytes": size},
                events=[],
            )
            digest = FrontendDigest(
                tool_name="docx_write_file",
                display_type=ToolDisplayType.FILE,
                minimal=MinimalView(title=Path(file_path).name, summary=f"文件已写入，共 {size//1024} KB" if size >= 1024 else f"文件已写入，共 {size} 字节", icon="wordmaster", status="success"),
                detailed=DetailedView(title="文件写入", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"{Path(file_path).name} | {size}B"),
                ]),
            )
            apply_dual_layer(result, digest, f"[WordMaster] 文件已写入: {Path(file_path).name} ({size}B)")
            return result
        except Exception as e:
            logger.error("[DocxWriteFile] Failed to write %s: %s", file_path, e)
            return AgentToolResult.error_result(tool_call.id, f"Failed to write file: {e}")


# ─── DocxReadFileTool ─────────────────────────────────────────────────────────


class DocxReadFileTool(AgentTool):
    """读取项目文件或 unpacked XML 内容。

    支持读取：
    - 项目文件：sources/*.md、scripts/*.js
    - unpacked XML：unpacked/word/document.xml 等
    - 任意文本文件（在白名单扩展名内）
    """

    name = "docx_read_file"
    description = (
        "读取文件并返回内容。"
        "用于读取项目内文件：Markdown（sources/）、"
        "JS 脚本（scripts/）、XML（unpacked/word/）等。"
        "路径规则：绝对路径直接使用；"
        "相对路径基于 wordmaster_state.project_path 解析。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description=(
                "要读取的文件路径。示例："
                "'unpacked/word/document.xml'、"
                "'sources/content.md'、"
                "'/abs/path/to/project/output/doc.docx'"
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

        # 解析路径：支持 state project_path + output/↔exports/ 回落
        p = Path(raw_path)
        if p.is_absolute():
            wm_state = (context or {}).get("user:wordmaster_state") or (context or {}).get("wordmaster_state") or {}
            state_pp = wm_state.get("project_path", "") if isinstance(wm_state, dict) else ""
            if state_pp:
                coerced, warn = coerce_path_under_state_project(
                    raw_path, state_pp, Path(_PROJECTS_DIR) if _PROJECTS_DIR else None,
                )
                if warn:
                    logger.warning("[DocxReadFile] %s", warn)
                resolved = Path(coerced)
            else:
                resolved = p
        else:
            wm_state = (context or {}).get("user:wordmaster_state") or (context or {}).get("wordmaster_state") or {}
            project_path = wm_state.get("project_path", "") if isinstance(wm_state, dict) else ""
            if project_path:
                resolved = Path(project_path) / p
            else:
                resolved = p

        # 文件不存在时：output/ ↔ exports/ 互换
        if not resolved.exists():
            resolved = WordMasterBaseTool._try_output_exports_fallback(resolved)

        if not resolved.exists():
            return AgentToolResult.error_result(
                tool_call.id,
                f"File not found: {resolved} (resolved from '{raw_path}')"
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
                "[DocxReadFile] 文件超过 %dB（实际 %dB），自动读取前 %d/%d 行；如需更多内容请指定 start_line/end_line",
                _MAX_FILE_SIZE, file_size, end_line, total_lines,
            )

        if start_line is not None or end_line is not None:
            s = max(0, (start_line or 1) - 1)
            e = (end_line or total_lines)
            lines = lines[s:e]
            text = "".join(lines)
            range_info = f" (lines {s+1}-{min(e, total_lines)} of {total_lines})"
            if oversize_truncated:
                range_info += f" [auto-truncated: file is {file_size}B > {_MAX_FILE_SIZE}B; pass start_line/end_line to read more]"
        else:
            range_info = f" ({total_lines} lines)"

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
            tool_name="docx_read_file",
            display_type=ToolDisplayType.DATA,
            minimal=MinimalView(title=resolved.name, summary=f"读取了该文件 {total_lines} 行内容", icon="wordmaster", status="success"),
            detailed=DetailedView(title="文件读取", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{resolved.name} | {total_lines}行 | {file_size}B"),
            ]),
        )
        apply_dual_layer(result, digest, output)
        return result
