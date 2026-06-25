"""Word Master 文档编辑工具（Unpack / Pack / Validate 三步流）

docx_unpack   — 解包 .docx 为 XML 目录
docx_pack     — 打包 XML 目录回 .docx
docx_validate — 验证 .docx 完整性
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter

from ._base import WordMasterBaseTool


class DocxUnpackTool(WordMasterBaseTool):
    """解包 .docx 为 XML 目录

    将 .docx（ZIP 格式）解压为可编辑的 XML 文件结构。
    自动 pretty-print、合并相邻 runs、转换 smart quotes。
    对应编辑流程 Step 1。
    """

    name = "docx_unpack"
    description = (
        "将 .docx 文件解包为可编辑的 XML 目录结构。"
        "解包后可用 docx_read_file 读取 XML，"
        "用 docx_write_file 编辑 XML，再用 docx_pack 重新打包。"
        "使用 --merge-runs false 可跳过 run 合并。"
    )
    parameters = [
        ToolParameter(
            name="input_file",
            type="string",
            description="要解包的 .docx 文件路径。",
            required=True,
        ),
        ToolParameter(
            name="output_dir",
            type="string",
            description="解包输出目录路径。示例：'<project_path>/unpacked/'",
            required=True,
        ),
        ToolParameter(
            name="merge_runs",
            type="string",
            description="是否合并相邻 runs：'true'（默认）或 'false'。",
            required=False,
        ),
    ]
    thinking_hint = "正在解包 .docx 文件…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        raw_input = args.get("input_file", "").strip()
        output_dir = args.get("output_dir", "").strip()

        if not raw_input:
            return self._make_result(tool_call, -1, "", "input_file is required", None)
        if not output_dir:
            return self._make_result(tool_call, -1, "", "output_dir is required", None)

        # 解析输入文件路径：支持 output/↔exports/ 回落
        input_file = str(self._resolve_file_path(raw_input, context))

        script_args = [input_file, output_dir]
        merge_runs = args.get("merge_runs", "").strip().lower()
        if merge_runs == "false":
            script_args += ["--merge-runs", "false"]

        rc, out, err = self._run_script("office/unpack.py", script_args)
        return self._make_result(tool_call, rc, out, err, {
            "input_file": input_file,
            "output_dir": output_dir,
        })


class DocxPackTool(WordMasterBaseTool):
    """打包 XML 目录回 .docx

    将编辑后的 XML 目录重新打包为 .docx 文件。
    自动验证并修复常见问题（durableId、空白保留等）。
    对应编辑流程 Step 3。
    """

    name = "docx_pack"
    description = (
        "将编辑后的 XML 目录打包回 .docx 文件。"
        "自动验证修复常见问题。"
        "必须在 docx_unpack 和 XML 编辑之后调用。"
        "建议传入 --original 参数以保留原文件的 ZIP 结构和嵌入资源。"
    )
    parameters = [
        ToolParameter(
            name="input_dir",
            type="string",
            description="unpacked XML 目录路径。",
            required=True,
        ),
        ToolParameter(
            name="output_file",
            type="string",
            description="输出 .docx 文件路径。",
            required=True,
        ),
        ToolParameter(
            name="original_file",
            type="string",
            description="可选：原始 .docx 文件路径，用于保留 ZIP 结构和嵌入资源。",
            required=False,
        ),
        ToolParameter(
            name="validate",
            type="string",
            description="是否验证：'true'（默认）或 'false'。",
            required=False,
        ),
    ]
    thinking_hint = "正在打包 .docx 文件…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        input_dir = args.get("input_dir", "").strip()
        output_file = args.get("output_file", "").strip()

        if not input_dir:
            return self._make_result(tool_call, -1, "", "input_dir is required", None)
        if not output_file:
            return self._make_result(tool_call, -1, "", "output_file is required", None)

        script_args = [input_dir, output_file]
        original = args.get("original_file", "").strip()
        if original:
            script_args += ["--original", original]
        validate = args.get("validate", "").strip().lower()
        if validate == "false":
            script_args += ["--validate", "false"]

        rc, out, err = self._run_script("office/pack.py", script_args)
        return self._make_result(tool_call, rc, out, err, {
            "input_dir": input_dir,
            "output_file": output_file,
        })


class DocxValidateTool(WordMasterBaseTool):
    """验证 .docx 文件完整性

    检查 .docx 文件的 XML 结构、schema 合规性等。
    可在创建或编辑后调用，用于诊断问题。
    """

    name = "docx_validate"
    description = (
        "验证 .docx 文件的完整性和 schema 合规性。"
        "在 docx_generate 创建或 docx_pack 打包后调用，"
        "检查 XML 结构是否正确。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description="要验证的 .docx 文件路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在验证 .docx 文件…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        raw_path = args.get("file_path", "").strip()

        if not raw_path:
            return self._make_result(tool_call, -1, "", "file_path is required", None)

        # 解析路径：支持 state project_path 纠偏 + output/↔exports/ 回落
        resolved = self._resolve_file_path(raw_path, context)
        file_path = str(resolved)

        rc, out, err = self._run_script("office/validate.py", [file_path])
        return self._make_result(tool_call, rc, out, err, {"file_path": file_path})
