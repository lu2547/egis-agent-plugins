"""布局模板工具

负责将 PPT_MASTER_SKILL_DIR/templates/layouts/<layout_name>/ 下的模板文件
复制到项目 templates/<layout_name>/ 目录，对应 ppt-master 流程 Step 3。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)
from ._base import PptMasterBaseTool, coerce_path_under_state_project, resolve_projects_relative

import logging

logger = logging.getLogger(__name__)

_SKILL_DIR = os.getenv("PPT_MASTER_SKILL_DIR", "")
_PROJECTS_DIR = os.getenv("PPT_MASTER_PROJECTS_DIR", "")


def _resolve_project_path(project_path: str) -> str:
    p = Path(project_path)
    if p.is_absolute():
        return str(p)
    if _PROJECTS_DIR:
        return str(resolve_projects_relative(project_path, Path(_PROJECTS_DIR)))
    return str(p)


class CopyLayoutTool(AgentTool):
    """将指定布局模板复制到项目 templates/ 目录。

    从 PPT_MASTER_SKILL_DIR/templates/layouts/<layout_name>/ 读取全部文件
    （design_spec.md、*.svg、*.png、*.jpg 等），复制到
    <project_path>/templates/<layout_name>/ 目录。

    等价于原版 ppt-master 的:
        cp ${SKILL_DIR}/templates/layouts/<name>/*.svg <project_path>/templates/
        cp ${SKILL_DIR}/templates/layouts/<name>/design_spec.md <project_path>/templates/
    """

    name = "ppt_copy_layout"
    description = (
        "将布局模板从全局库复制到项目的 templates/ 目录。"
        "在 Step 3 用户选择模板或设置了 PPT_MASTER_DEFAULT_LAYOUT 时使用。"
        "复制模板目录下全部文件（design_spec.md、*.svg、*.png、*.jpg）。"
        "先用 ppt_list_layouts 查看可用选项。"
    )
    parameters = [
        ToolParameter(
            name="layout_name",
            type="string",
            description=(
                "布局模板目录名，如 'pingan_style'、'mckinsey'、"
                "'google_style'。必须匹配 "
                "PPT_MASTER_SKILL_DIR/templates/layouts/ 下的子目录。"
            ),
            required=True,
        ),
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在复制布局模板到项目目录…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        layout_name = (args.get("layout_name") or "").strip()
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        pptmaster = (context or {}).get("pptmaster_state", {})
        state_pp = pptmaster.get("project_path", "") if isinstance(pptmaster, dict) else ""
        raw_pp = (args.get("project_path") or "").strip()
        if state_pp:
            project_path = state_pp
            if raw_pp:
                # 记录不一致，便于排障
                corrected, warn = coerce_path_under_state_project(
                    raw_pp,
                    state_pp,
                    Path(_PROJECTS_DIR) if _PROJECTS_DIR else None,
                )
                if warn:
                    logger.warning("[CopyLayout] %s", warn)
        else:
            project_path = _resolve_project_path(raw_pp)

        if not layout_name:
            return AgentToolResult.error_result(tool_call.id, "layout_name is required")
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")
        if not _SKILL_DIR:
            return AgentToolResult.error_result(
                tool_call.id, "PPT_MASTER_SKILL_DIR environment variable is not set"
            )

        src_dir = Path(_SKILL_DIR) / "templates" / "layouts" / layout_name
        if not src_dir.exists():
            available = _list_available_layouts()
            return AgentToolResult.error_result(
                tool_call.id,
                f"Layout '{layout_name}' not found at {src_dir}. "
                f"Available layouts: {', '.join(available)}",
            )

        dst_dir = Path(project_path) / "templates" / layout_name
        dst_dir.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        errors: list[str] = []
        for src_file in src_dir.iterdir():
            if src_file.is_file():
                dst_file = dst_dir / src_file.name
                try:
                    shutil.copy2(src_file, dst_file)
                    copied.append(src_file.name)
                    logger.debug("[CopyLayout] Copied %s -> %s", src_file, dst_file)
                except Exception as exc:
                    errors.append(f"{src_file.name}: {exc}")

        if errors:
            return AgentToolResult.error_result(
                tool_call.id,
                f"Partial failure copying layout '{layout_name}':\n" + "\n".join(errors),
            )

        summary = (
            f"Layout '{layout_name}' copied successfully.\n"
            f"Destination: {dst_dir}\n"
            f"Files copied ({len(copied)}): {', '.join(sorted(copied))}"
        )
        logger.info("[CopyLayout] %s", summary)
        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=summary,
            is_error=False,
            metadata={
                "layout_name": layout_name,
                "dst_dir": str(dst_dir),
                "copied_files": sorted(copied),
            },
            events=[],
        )
        digest = FrontendDigest(
            tool_name="ppt_copy_layout",
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(title=f"布局 {layout_name}", summary=f"已复制该布局模板（含 {len(copied)} 个文件）", icon="pptmaster", status="success"),
            detailed=DetailedView(title="布局模板复制", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"布局 {layout_name} | 复制 {len(copied)} 个文件"),
            ]),
        )
        apply_dual_layer(result, digest, summary)
        return result


class ListLayoutsTool(AgentTool):
    """列出所有可用的布局模板。

    读取 PPT_MASTER_SKILL_DIR/templates/layouts/layouts_index.json，
    返回可用模板名称及描述，供 Step 3 模板推荐流程使用。
    """

    name = "ppt_list_layouts"
    description = (
        "列出全局模板库中所有可用布局模板。"
        "读取 layouts_index.json 并返回模板名称与风格描述。"
        "在 Step 3 向用户展示模板选项时使用。"
    )
    parameters = []
    thinking_hint = "正在读取可用布局模板列表…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        if not _SKILL_DIR:
            return AgentToolResult.error_result(
                tool_call.id, "PPT_MASTER_SKILL_DIR environment variable is not set"
            )

        index_path = Path(_SKILL_DIR) / "templates" / "layouts" / "layouts_index.json"
        if not index_path.exists():
            # 回退：直接列出子目录名
            layouts_dir = Path(_SKILL_DIR) / "templates" / "layouts"
            available = _list_available_layouts()
            content = "layouts_index.json not found. Available layout directories:\n" + "\n".join(
                f"- {name}" for name in available
            )
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=content,
                is_error=False,
                metadata={"layouts": available},
                events=[],
            )
            digest = FrontendDigest(
                tool_name="ppt_list_layouts",
                display_type=ToolDisplayType.DATA,
                minimal=MinimalView(title="可用布局", summary=f"当前提供 {len(available)} 种布局可选", icon="pptmaster", status="success"),
                detailed=DetailedView(title="可用布局模板", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"当前 {len(available)} 种布局可选"),
                ]),
            )
            apply_dual_layer(result, digest, content)
            return result

        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            content = f"Available layouts ({index_path}):\n\n" + json.dumps(data, ensure_ascii=False, indent=2)
            layout_names = [item.get("name", "?") for item in data] if isinstance(data, list) else list(data.keys()) if isinstance(data, dict) else []
            result = AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=content,
                is_error=False,
                metadata={"index_path": str(index_path)},
                events=[],
            )
            digest = FrontendDigest(
                tool_name="ppt_list_layouts",
                display_type=ToolDisplayType.DATA,
                minimal=MinimalView(title="可用布局", summary=f"当前提供 {len(layout_names)} 种布局可选", icon="pptmaster", status="success"),
                detailed=DetailedView(title="可用布局模板", sections=[
                    ViewSection(heading="摘要", content_type="text", data=f"当前 {len(layout_names)} 种布局可选"),
                ]),
            )
            apply_dual_layer(result, digest, content)
            return result
        except Exception as exc:
            return AgentToolResult.error_result(tool_call.id, f"Failed to read layouts_index.json: {exc}")


def _list_available_layouts() -> list[str]:
    """列出 layouts/ 下所有子目录名称（即可用模板名）"""
    if not _SKILL_DIR:
        return []
    layouts_dir = Path(_SKILL_DIR) / "templates" / "layouts"
    if not layouts_dir.exists():
        return []
    return sorted(p.name for p in layouts_dir.iterdir() if p.is_dir())


class RegisterTemplateTool(PptMasterBaseTool):
    """注册/刷新布局模板索引

    调用 register_template.py，读取指定模板的 design_spec.md，
    更新 layouts_index.json 和 README.md 索引表。
    新增模板后调用，或使用 --rebuild-all 重建全部索引。
    """

    name = "ppt_register_template"
    description = (
        "注册或刷新布局模板索引。"
        "读取 design_spec.md 并更新 layouts_index.json 和 README.md。"
        "新增模板后调用，或不传 template_id 使用 rebuild-all 模式重建全部索引。"
    )
    parameters = [
        ToolParameter(
            name="template_id",
            type="string",
            description="模板目录名（如 'pingan_style'）。留空则 --rebuild-all。",
            required=False,
        ),
        ToolParameter(
            name="dry_run",
            type="boolean",
            description="仅显示将要写入的内容，不实际修改文件。默认 false。",
            required=False,
        ),
    ]
    thinking_hint = "正在注册布局模板索引…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        template_id = (args.get("template_id") or "").strip()
        dry_run = bool(args.get("dry_run", False))

        script_args: list[str] = []
        if template_id:
            script_args.append(template_id)
        else:
            script_args.append("--rebuild-all")
        if dry_run:
            script_args.append("--dry-run")

        rc, out, err = self._run_script("register_template.py", script_args)
        return self._make_result(
            tool_call, rc, out, err,
            {"template_id": template_id or "(rebuild-all)", "dry_run": dry_run},
        )
