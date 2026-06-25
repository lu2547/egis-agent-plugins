"""Word Master 格式转换工具

docx_convert — 通过 LibreOffice (soffice.py) 进行文档格式转换
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter

from ._base import WordMasterBaseTool


class DocxConvertTool(WordMasterBaseTool):
    """通过 LibreOffice 进行文档格式转换

    支持 .doc → .docx、.docx → .pdf 等常见格式转换。
    底层调用 scripts/office/soffice.py 脚本。
    """

    name = "docx_convert"
    description = (
        "通过 LibreOffice 转换文档格式。"
        "常见用例：.doc → .docx、.docx → .pdf。"
        "传入 input_file 和目标格式 (format)。"
        "输出文件将写入 input_file 所在目录。"
    )
    parameters = [
        ToolParameter(
            name="input_file",
            type="string",
            description="输入文件路径（如 document.doc）。",
            required=True,
        ),
        ToolParameter(
            name="format",
            type="string",
            description="目标格式，如 docx、pdf、html、txt 等。",
            required=True,
        ),
        ToolParameter(
            name="output_dir",
            type="string",
            description=(
                "可选：输出目录。默认与 input_file 同目录。"
            ),
            required=False,
        ),
    ]
    thinking_hint = "正在转换文档格式…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        input_file = args.get("input_file", "").strip()
        fmt = args.get("format", "").strip()

        if not input_file:
            return self._make_result(tool_call, -1, "", "input_file is required", None)
        if not fmt:
            return self._make_result(tool_call, -1, "", "format is required", None)

        script_args = ["--headless", "--convert-to", fmt]

        output_dir = args.get("output_dir", "").strip()
        if output_dir:
            script_args += ["--outdir", output_dir]

        script_args.append(input_file)

        rc, out, err = self._run_script("office/soffice.py", script_args)
        return self._make_result(tool_call, rc, out, err, {
            "input_file": input_file,
            "format": fmt,
            "output_dir": output_dir or "(same as input)",
        })
