"""项目管理工具

封装 project_manager.py 的 init / import-sources / validate 子命令，
对应 ppt-master 流程 Step 2。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import ToolParameter
from ark_agentic.core.types import AgentToolResult

from ._base import PptMasterBaseTool, PROJECTS_DIR


def _sanitize_id(value: str, max_len: int = 16) -> str:
    """将 session/user id 清洗成可用于目录名的片段。

    保留字母数字与 `-_`，其余替换为 `-`，裁剪到指定长度。
    空值返回空串，由调用方决定降级策略。
    """
    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    cleaned = cleaned.strip("-_")
    return cleaned[:max_len]


def _resolve_ids(context: dict[str, Any] | None) -> tuple[str, str]:
    """从 ark_agentic 注入的 context 里取 session_id / user_id。

    - session_id：runner 已直接展平在 ctx 里
    - user_id：考虑多个可能的携带位置，都取不到时降级为 `anon`
    """
    ctx = context or {}
    sid = _sanitize_id(str(ctx.get("session_id") or ""), max_len=12) or "nosid"

    uid_raw: Any = ctx.get("user_id")
    if not uid_raw:
        runtime = ctx.get("runtime") or {}
        run = runtime.get("run") if isinstance(runtime, dict) else None
        if isinstance(run, dict):
            uid_raw = run.get("user_id")
    uid = _sanitize_id(str(uid_raw or ""), max_len=16) or "anon"
    return sid, uid


class ProjectInitTool(PptMasterBaseTool):
    """初始化 PPT 项目

    在 projects/ 目录下创建标准项目结构（sources/、svg_output/、images/、notes/ 等）。
    对应 ppt-master 流程 Step 2。
    """

    name = "ppt_project_init"
    description = (
        "初始化新的 PPT 项目目录，包含标准结构"
        "（sources/、svg_output/、images/、notes/、templates/）。"
        "在 Step 2 源内容就绪后使用。"
        "返回创建的项目路径。"
    )
    parameters = [
        ToolParameter(
            name="project_name",
            type="string",
            description="项目名称（建议不含空格）。示例：'my_quarterly_report'",
            required=True,
        ),
        ToolParameter(
            name="format",
            type="string",
            description="画布格式：'ppt169'（16:9，默认）、'ppt43'、'xhs'、'story' 等。",
            required=False,
        ),
        ToolParameter(
            name="projects_dir",
            type="string",
            description="项目基础目录，默认为 ppt-master/projects/。",
            required=False,
        ),
    ]
    thinking_hint = "正在初始化 PPT 项目…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_name = args.get("project_name", "").strip()
        if not project_name:
            return AgentToolResult.error_result(tool_call.id, "project_name is required")

        # 从调用 context 里取 session/user，用于隔离多会话/多用户
        sid, uid = _resolve_ids(context)
        # 日期分层父目录：PROJECTS_DIR / YYYYMMDD
        date_str = datetime.now().strftime("%Y%m%d")
        # 最终目录名：{name}_{sid}_{uid}
        dir_name = f"{project_name}_{sid}_{uid}"

        projects_dir = args.get("projects_dir", "").strip()
        if not projects_dir and PROJECTS_DIR:
            projects_dir = str(PROJECTS_DIR)
        if projects_dir:
            base_dir = str(Path(projects_dir) / date_str)
        else:
            base_dir = date_str  # project_manager 会解析为 CWD 相对路径

        script_args = ["init", dir_name, "--dir", base_dir]
        fmt = args.get("format", "").strip()
        if fmt:
            script_args += ["--format", fmt]

        rc, out, err = self._run_script("project_manager.py", script_args)

        # 从 stdout 提取完整项目路径
        import re
        proj_path = ""
        m = re.search(r"(?:\[OK\] )?Project (?:created|initialized|already exists) \(?reusing\)?: (.+)", out)
        if m:
            proj_path = m.group(1).strip()

        extra_meta: dict[str, Any] = {"project_name": project_name}
        if proj_path:
            extra_meta["project_path"] = proj_path
            extra_meta["state_delta"] = {"pptmaster_state.project_path": proj_path}

        return self._make_result(tool_call, rc, out, err, extra_meta)


class ProjectImportSourcesTool(PptMasterBaseTool):
    """导入源文件到项目

    将补充素材移入项目的 sources/ 目录归档。
    企业问答默认不使用此工具导入 PDF/DOCX；主内容应由 RAG 整理后通过
    ppt_write_file 写入 sources/source_data.md。
    """

    name = "ppt_import_sources"
    description = (
        "将补充素材导入项目的 sources/ 目录。"
        "企业问答场景下，不要用它导入 PDF/DOCX/PPTX/URL 原文件；"
        "主内容应先由 RAG 整理成 Markdown，再用 ppt_write_file 写入 "
        "<project_path>/sources/source_data.md。"
        "重要：始终使用 move=true，遵循 ppt-master 规范。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
        ToolParameter(
            name="source_files",
            type="array",
            description="要导入的源文件绝对路径列表。",
            required=True,
            items={"type": "string"},
        ),
        ToolParameter(
            name="move",
            type="boolean",
            description="true（默认）为移动文件，false 为复制文件。",
            required=False,
        ),
    ]
    thinking_hint = "正在导入源文件到项目…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        source_files = args.get("source_files", [])

        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")
        if not source_files:
            return AgentToolResult.error_result(tool_call.id, "source_files is required and must not be empty")

        script_args = ["import-sources", project_path] + [str(f) for f in source_files]
        move = args.get("move", True)
        script_args += ["--move" if move else "--copy"]

        rc, out, err = self._run_script("project_manager.py", script_args)
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path})


class ProjectValidateTool(PptMasterBaseTool):
    """验证项目结构

    检查项目目录结构是否符合 ppt-master 规范，验证 SVG 文件的 viewBox 等。
    可在任何阶段调用用于诊断。
    """

    name = "ppt_project_validate"
    description = (
        "验证项目目录结构和 SVG 文件。"
        "检查必要的子目录、SVG viewBox 合规性等。"
        "可在任何阶段用于诊断问题。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="要验证的项目目录绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在验证项目结构…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        rc, out, err = self._run_script("project_manager.py", ["validate", project_path])
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path})
