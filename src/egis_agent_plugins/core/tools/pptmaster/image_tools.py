"""图像工具

封装 analyze_images.py，对应 ppt-master 流程 Step 4（图像分析）。
文生图能力已取消：PPT 产出以 SVG 矢量图形 + 用户提供素材为主。
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter
from ark_agentic.core.types import AgentToolResult

from ._base import PptMasterBaseTool


class AnalyzeImagesTool(PptMasterBaseTool):
    """分析图片尺寸和布局建议

    分析 images/ 目录中所有图片的尺寸和宽高比，
    输出 PPT 布局建议和 image_analysis.csv。
    在 Strategist 阶段（Step 4）生成 design_spec 前调用。
    """

    name = "ppt_analyze_images"
    description = (
        "分析文件夹中所有图片的宽度、高度和宽高比。"
        "输出 PPT 布局建议。"
        "如果用户提供了图片，在写 design_spec.md 之前运行此工具。"
        "重要：切勿直接读取/打开图片文件 —— 使用此工具代替。"
    )
    parameters = [
        ToolParameter(
            name="images_dir",
            type="string",
            description="图片目录的绝对路径（如 <project_path>/images）。",
            required=True,
        ),
        ToolParameter(
            name="canvas",
            type="string",
            description="画布格式，用于布局计算：'ppt169'（默认）、'ppt43' 等。",
            required=False,
        ),
    ]
    thinking_hint = "正在分析图片尺寸和布局建议…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        images_dir = args.get("images_dir", "").strip()
        if not images_dir:
            return AgentToolResult.error_result(tool_call.id, "images_dir is required")

        script_args = [images_dir]
        canvas = args.get("canvas", "").strip()
        if canvas:
            script_args += ["--canvas", canvas]

        rc, out, err = self._run_script("analyze_images.py", script_args)
        return self._make_result(tool_call, rc, out, err, {"images_dir": images_dir})
