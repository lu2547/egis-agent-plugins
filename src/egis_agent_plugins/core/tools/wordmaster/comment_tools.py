"""Word Master 修订与注释工具

docx_accept_changes — 接受所有修订标记
docx_add_comment    — 向 unpacked 文档添加注释
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter

from ._base import WordMasterBaseTool


class DocxAcceptChangesTool(WordMasterBaseTool):
    """接受 Word 文档中的所有修订标记

    使用 LibreOffice 接受所有 tracked changes，生成干净的文档。
    """

    name = "docx_accept_changes"
    description = (
        "接受 Word 文档中的所有修订标记（tracked changes），"
        "生成一份干净的文档。需要 LibreOffice。"
    )
    parameters = [
        ToolParameter(
            name="input_file",
            type="string",
            description="输入 .docx 文件路径。",
            required=True,
        ),
        ToolParameter(
            name="output_file",
            type="string",
            description="输出 .docx 文件路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在接受修订标记…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        input_file = args.get("input_file", "").strip()
        output_file = args.get("output_file", "").strip()

        if not input_file:
            return self._make_result(tool_call, -1, "", "input_file is required", None)
        if not output_file:
            return self._make_result(tool_call, -1, "", "output_file is required", None)

        rc, out, err = self._run_script("accept_changes.py", [input_file, output_file])
        return self._make_result(tool_call, rc, out, err, {
            "input_file": input_file,
            "output_file": output_file,
        })


class DocxAddCommentTool(WordMasterBaseTool):
    """向 unpacked 文档添加注释

    在 unpacked/ 目录中创建注释 XML 基础结构。
    之后需手动在 document.xml 中添加 commentRangeStart/End 标记。
    """

    name = "docx_add_comment"
    description = (
        "向 unpacked Word 文档添加注释。"
        "在 unpacked/ 目录中创建注释 XML 结构。"
        "调用后需在 document.xml 中手动添加 "
        "commentRangeStart/commentRangeEnd 标记。"
        "支持回复（--parent）和自定义作者（--author）。"
    )
    parameters = [
        ToolParameter(
            name="unpacked_dir",
            type="string",
            description="unpacked 目录路径。",
            required=True,
        ),
        ToolParameter(
            name="comment_id",
            type="string",
            description="注释 ID（整数）。",
            required=True,
        ),
        ToolParameter(
            name="text",
            type="string",
            description="注释文本内容（需预转义 XML 实体）。",
            required=True,
        ),
        ToolParameter(
            name="parent_id",
            type="string",
            description="可选：父注释 ID，用于创建回复。",
            required=False,
        ),
        ToolParameter(
            name="author",
            type="string",
            description="可选：注释作者名称（默认 'AI Assistant'）。",
            required=False,
        ),
    ]
    thinking_hint = "正在添加注释…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> "AgentToolResult":
        args = tool_call.arguments or {}
        unpacked_dir = args.get("unpacked_dir", "").strip()
        comment_id = args.get("comment_id", "").strip()
        text = args.get("text", "").strip()

        if not unpacked_dir:
            return self._make_result(tool_call, -1, "", "unpacked_dir is required", None)
        if not comment_id:
            return self._make_result(tool_call, -1, "", "comment_id is required", None)
        if not text:
            return self._make_result(tool_call, -1, "", "text is required", None)

        script_args = [unpacked_dir, comment_id, text]
        parent_id = args.get("parent_id", "").strip()
        if parent_id:
            script_args += ["--parent", parent_id]
        author = args.get("author", "").strip()
        if author:
            script_args += ["--author", author]

        rc, out, err = self._run_script("comment.py", script_args)
        return self._make_result(tool_call, rc, out, err, {
            "unpacked_dir": unpacked_dir,
            "comment_id": comment_id,
        })
