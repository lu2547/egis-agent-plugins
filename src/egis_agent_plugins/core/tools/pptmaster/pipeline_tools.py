"""Pipeline 处理与导出工具

封装 total_md_split.py / finalize_svg.py / svg_to_pptx.py /
svg_quality_checker.py / update_spec.py，
对应 ppt-master 流程 Step 7 及辅助质检工具。
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter
from ark_agentic.core.types import AgentToolResult

from ._base import PptMasterBaseTool


class TotalMdSplitTool(PptMasterBaseTool):
    """拆分演讲备注

    将 notes/total.md 按幻灯片拆分为多个独立 notes 文件。
    对应 ppt-master 流程 Step 7.1，必须在 finalize_svg 之前运行。
    """

    name = "ppt_split_notes"
    description = (
        "将 total.md 演讲备注文件拆分为每页独立的备注文件。"
        "必须在 Step 7.1 即 finalize_svg 之前运行。"
        "输入：<project_path>/notes/total.md（由 Executor 在 Step 6 生成）。"
        "输出：notes/ 下与 SVG 文件名匹配的独立 .md 文件。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在拆分演讲备注…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        rc, out, err = self._run_script("total_md_split.py", [project_path])
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path})


class FinalizeSvgTool(PptMasterBaseTool):
    """SVG 后处理

    对 svg_output/ 中的 SVG 执行完整后处理（图标嵌入、图片裁剪嵌入、
    文本展平、圆角矩形转路径），输出到 svg_final/。
    对应 ppt-master 流程 Step 7.2。
    """

    name = "ppt_finalize_svg"
    description = (
        "对所有 SVG 文件进行后处理：嵌入图标、裁剪/嵌入图片、"
        "文本展平、圆角矩形转路径。"
        "从 svg_output/ 读取，输出到 svg_final/。"
        "必须在 Step 7.2 即 svg_to_pptx 之前运行。"
        "切勿用 'cp' 命令代替此工具。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
        ToolParameter(
            name="only",
            type="string",
            description="可选：仅运行部分步骤，空格分隔。"
                        "可选值：embed-icons、crop-images、fix-aspect、embed-images、"
                        "flatten-text、fix-rounded。不填则运行全部。",
            required=False,
        ),
    ]
    thinking_hint = "正在执行 SVG 后处理…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        script_args = [project_path]
        only = args.get("only", "").strip()
        if only:
            script_args += ["--only"] + only.split()

        rc, out, err = self._run_script("finalize_svg.py", script_args, timeout=600)
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path})


class SvgToPptxTool(PptMasterBaseTool):
    """导出 PPTX

    将 svg_final/ 中的 SVG 文件转换为 PPTX，嵌入演讲备注。
    对应 ppt-master 流程 Step 7.3，必须在 finalize_svg 之后运行。
    """

    name = "ppt_export_pptx"
    description = (
        "将 SVG 文件导出为 PPTX，嵌入演讲备注。"
        "必须在 Step 7.3 即 ppt_finalize_svg 之后运行。"
        "始终使用 stage='final'（从 svg_final/ 读取）。"
        "输出：exports/<project_name>_<timestamp>.pptx"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
        ToolParameter(
            name="stage",
            type="string",
            description="源文件阶段：'final'（默认，读取 svg_final/）或 'output'（读取 svg_output/）。"
                        "除调试外始终用 'final'。",
            required=False,
        ),
    ]
    thinking_hint = "正在导出 PPTX 文件…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        stage = args.get("stage", "final").strip() or "final"
        script_args = [project_path, "-s", stage]

        rc, out, err = self._run_script("svg_to_pptx.py", script_args, timeout=300)
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path, "stage": stage})


class SvgQualityCheckTool(PptMasterBaseTool):
    """SVG 质量检查

    检查 SVG 文件是否符合项目规范（viewBox、颜色、字体等）。
    可对单个文件或整个目录运行，输出违规项列表。
    """

    name = "ppt_svg_quality_check"
    description = (
        "检查 SVG 文件是否符合技术规范"
        "（viewBox、颜色、字体、spec_lock 偏离等）。"
        "接受单个 SVG 文件路径或目录。"
        "用于导出前诊断渲染问题。"
    )
    parameters = [
        ToolParameter(
            name="target",
            type="string",
            description="SVG 文件或包含 SVG 文件的目录的绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在检查 SVG 质量…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        target = args.get("target", "").strip()
        if not target:
            return AgentToolResult.error_result(tool_call.id, "target is required")

        rc, out, err = self._run_script("svg_quality_checker.py", [target])
        return self._make_result(tool_call, rc, out, err, {"target": target})


class UpdateSpecTool(PptMasterBaseTool):
    """传播 spec_lock 变更到 SVG

    当 spec_lock.md 中的颜色或字体发生变更时，
    批量更新 svg_output/ 中所有 SVG 文件的对应值。
    仅支持 colors.* 和 typography.font_family 的批量传播。
    """

    name = "ppt_update_spec"
    description = (
        "将 spec_lock.md 中的颜色或字体变更批量传播到 svg_output/ 中所有 SVG 文件。"
        "支持的 key：'primary=#RRGGBB'、'colors.text=#111111'、"
        "'typography.font_family=\"Inter\", Arial, sans-serif'。"
        "仅 colors.* 和 typography.font_family 支持批量传播。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
        ToolParameter(
            name="change",
            type="string",
            description="要传播的 key=value 变更。"
                        "示例：'primary=#0066AA'、'colors.text=#111111'、"
                        "'typography.font_family=\"Inter\", Arial'",
            required=True,
        ),
    ]
    thinking_hint = "正在传播 spec_lock 变更到 SVG…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        # state 优先：LLM 跨轮重拼项目路径易丢日期/sid_uid 后缀
        project_path = self._project_path_from(args, context)
        change = args.get("change", "").strip()

        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")
        if not change:
            return AgentToolResult.error_result(tool_call.id, "change is required")

        rc, out, err = self._run_script("update_spec.py", [project_path, change])
        return self._make_result(tool_call, rc, out, err, {"project_path": project_path, "change": change})
