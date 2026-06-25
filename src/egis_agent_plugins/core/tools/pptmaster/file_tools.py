"""文件写入工具

LLM 生成的文本内容（design_spec.md、spec_lock.md、SVG、notes/total.md 等）
通过此工具写入项目磁盘，完成 pipeline 中 LLM → 文件系统 的交接。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, CustomToolEvent, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)
from ._base import (
    PROJECTS_DIR,
    assert_within_allowed_roots,
    coerce_path_under_state_project,
    resolve_projects_relative,
)

import logging
logger = logging.getLogger(__name__)

_PROJECTS_DIR = os.getenv("PPT_MASTER_PROJECTS_DIR", "")


def _resolve_file_path(file_path: str) -> str:
    """将 file_path 转为绝对路径。相对路径基于 PPT_MASTER_PROJECTS_DIR 解析，按 YYYYMMDD 分层。"""
    p = Path(file_path)
    if p.is_absolute():
        return str(p)
    if _PROJECTS_DIR:
        return str(resolve_projects_relative(file_path, Path(_PROJECTS_DIR)))
    return str(p)


class WriteFileTool(AgentTool):
    """将文本内容写入指定文件路径。

    用于 pptmaster pipeline 中 LLM 生成的所有文件：
    - design_spec.md（Strategist 阶段）
    - spec_lock.md（Strategist 阶段）
    - svg_output/<name>.svg（Executor 阶段，逐页写入）
    - notes/total.md（Executor 阶段）
    - images/image_prompts.md（Image_Generator 阶段）
    """

    name = "ppt_write_file"
    description = (
        "将文本内容写入磁盘文件。"
        "用于保存 LLM 生成的文件到项目目录："
        "design_spec.md、spec_lock.md、SVG 页面（svg_output/*.svg）、"
        "notes/total.md、images/image_prompts.md 等。"
        "父目录自动创建，已存在文件会被覆盖。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description=(
                "要写入的文件绝对路径。"
                "示例："
                "'/path/to/project/design_spec.md'、"
                "'/path/to/project/spec_lock.md'、"
                "'/path/to/project/svg_output/slide_01.svg'、"
                "'/path/to/project/notes/total.md'"
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

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        raw_file_path = args.get("file_path", "").strip()
        print(f"[WriteFile] raw file_path={repr(raw_file_path)}", flush=True)

        # 以 session state 中的 project_path 为权威校正 LLM 传入的路径。
        # 背景：第二轮起 LLM 经常自己拼路径，会丢失 YYYYMMDD/ 日期段或
        # _sid_uid 后缀，使 SVG/notes 落入错误目录。只要 state 中有 project_path，
        # 都以它为准纠正，不再相信 LLM 自己组装的项目路径。
        pptmaster = (context or {}).get("pptmaster_state", {})
        state_pp = pptmaster.get("project_path", "") if isinstance(pptmaster, dict) else ""

        if state_pp and raw_file_path:
            file_path, warn = coerce_path_under_state_project(
                raw_file_path,
                state_pp,
                Path(_PROJECTS_DIR) if _PROJECTS_DIR else None,
            )
            if warn:
                logger.warning("[WriteFile] %s", warn)
                print(f"[WriteFile] WARN {warn}", flush=True)
        else:
            # 无 state（不太可能，但保留保底）：保持原逻辑
            _p = Path(raw_file_path)
            if _p.is_absolute():
                file_path = str(_p)
            elif raw_file_path:
                file_path = _resolve_file_path(raw_file_path)
            else:
                file_path = ""

        content = args.get("content", "")

        if not file_path:
            logger.warning("[WriteFile] file_path is empty — tool_call arguments may have been truncated by LLM output limit")
            return AgentToolResult.error_result(
                tool_call.id,
                "file_path is required. "
                "This error usually means the tool_call JSON was truncated due to output token limit. "
                "Please retry: first call ppt_write_file with ONLY the file_path and content (no other text), "
                "and if content is very long, split it into smaller writes using append_mode."
            )
        # 额外护栏：若校正后的路径恰好是 PROJECTS_DIR 本身或其上层，说明
        # tool_call 严重截断或项目名被后续逻辑全剥空了，拒绝写入。
        if PROJECTS_DIR is not None:
            try:
                fp_resolved = Path(file_path).resolve()
                pd_resolved = PROJECTS_DIR.resolve()
                if fp_resolved == pd_resolved or pd_resolved.is_relative_to(fp_resolved):
                    logger.warning("[WriteFile] resolved path %r equals or is parent of PROJECTS_DIR — likely truncated tool_call", file_path)
                    return AgentToolResult.error_result(
                        tool_call.id,
                        f"file_path resolved to {file_path!r} which is the PROJECTS_DIR itself or its parent. "
                        "This usually means the tool_call arguments were truncated. "
                        "Please retry with a complete file path under the project directory."
                    )
            except (OSError, ValueError):
                pass
        if not content:
            logger.warning("[WriteFile] content is empty for %s — tool_call arguments may have been truncated", file_path)
            return AgentToolResult.error_result(
                tool_call.id,
                f"content is required for {file_path}. "
                "This error usually means the content was truncated due to output token limit. "
                "Please retry writing this file with shorter content, or split into multiple writes."
            )

        try:
            path = Path(file_path)
            # 路径沙箱校验：配置了 PROJECTS_DIR 时，所有写入必须在其内
            try:
                path = assert_within_allowed_roots(path, [PROJECTS_DIR])
            except PermissionError as exc:
                logger.warning("[WriteFile] 拒绝写入项目目录外文件: %s", exc)
                return AgentToolResult.error_result(
                    tool_call.id,
                    f"拒绝写入项目目录外文件: {file_path}",
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            size = len(content.encode("utf-8"))
            logger.info("[WriteFile] Written %d bytes to %s", size, file_path)
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=f"File written successfully: {file_path} ({size} bytes)",
                is_error=False,
                metadata={"file_path": file_path, "size_bytes": size},
                events=[],
            )
            digest = FrontendDigest(
                tool_name="ppt_write_file",
                display_type=ToolDisplayType.FILE,
                minimal=MinimalView(title=Path(file_path).name, summary=f"文件已写入，共 {size//1024} KB" if size >= 1024 else f"文件已写入，共 {size} 字节", icon="pptmaster", status="success"),
                detailed=DetailedView(title="文件写入", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"{Path(file_path).name} | {size}B"),
                ]),
            )
            apply_dual_layer(result, digest, f"[PPTMaster] 文件已写入: {Path(file_path).name} ({size}B)")

            # 写入 SVG 页面时，向前端推送实时预览事件（直接推 SVG 全文，前端渲染）。
            # SVG 是纯文本，executor 阶段每张仅几 KB；payload 走 SSE custom 事件，
            # 不进 LLM 上下文。失败不影响写文件主流程。
            try:
                if file_path.lower().endswith(".svg"):
                    self._emit_svg_preview(result, state_pp, file_path, content)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[WriteFile] svg_preview emit failed: %s", exc)

            return result
        except Exception as e:
            logger.error("[WriteFile] Failed to write %s: %s", file_path, e)
            return AgentToolResult.error_result(tool_call.id, f"Failed to write file: {e}")

    # 已写入 SVG 计数，用于文件名无前导数字时回退推断页码（按 state_project 分桶）
    _svg_seq: dict[str, int] = {}

    def _emit_svg_preview(
        self, result: AgentToolResult, abs_project: str, file_path: str, content: str
    ) -> None:
        """为刚写入的 SVG 页面追加一个 svg_preview custom 事件（直接携带 SVG 全文）。

        payload = {"page": int, "file_name": str, "svg": str}
        前端直接渲染 svg 文本，无需任何下载接口/URL。
        """
        # 页码：优先取文件名开头数字（01_xxx.svg → 1），否则按已写入序号回退
        name = Path(file_path).name
        m = re.match(r"^0*(\d+)", name)
        if m:
            page = int(m.group(1))
        else:
            key = abs_project or ""
            self._svg_seq[key] = self._svg_seq.get(key, 0) + 1
            page = self._svg_seq[key]

        result.events.append(
            CustomToolEvent(
                custom_type="svg_preview",
                payload={"page": page, "file_name": name, "svg": content},
            )
        )
