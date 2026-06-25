"""文档转 Markdown 工具

将各种格式的源文件（PDF / DOCX / PPTX / Web URL）转换为 Markdown，
作为 PPT Master 流程 Step 1 的工具化封装。
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.tools.base import ToolParameter
from ark_agentic.core.types import AgentToolResult

from ._base import PptMasterBaseTool


class PdfToMdTool(PptMasterBaseTool):
    """PDF 转 Markdown

    使用 PyMuPDF 提取 PDF 文本内容，输出同目录下的 .md 文件。
    对应 ppt-master 流程 Step 1。
    """

    name = "pdf_to_md"
    description = (
        "将 PDF 文件转换为 Markdown 格式。"
        "输出写到同目录下的 <输入文件名>.md。"
        "在 Step 1 用户提供 PDF 源文件用于 PPT 生成时使用。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description="要转换的 PDF 文件绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在将 PDF 转换为 Markdown…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return AgentToolResult.error_result(tool_call.id, "file_path is required")

        rc, out, err = self._run_script("source_to_md/pdf_to_md.py", [file_path])
        return self._make_result(tool_call, rc, out, err, {"file_path": file_path})


class DocToMdTool(PptMasterBaseTool):
    """文档转 Markdown（DOCX / HTML / EPUB / LaTeX 等）

    支持多种格式，优先使用纯 Python（mammoth / markdownify），
    回退 pandoc 处理 .doc/.odt/.rtf/.tex/.rst/.org/.typ 等。
    对应 ppt-master 流程 Step 1。
    """

    name = "doc_to_md"
    description = (
        "将文档（DOCX、HTML、EPUB、LaTeX、RST 等）转换为 Markdown。"
        "输出写到 <输入文件名>.md。"
        "在 Step 1 用户提供 Word/Office 文档源文件时使用。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description="要转换的文档文件绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在将文档转换为 Markdown…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return AgentToolResult.error_result(tool_call.id, "file_path is required")

        rc, out, err = self._run_script("source_to_md/doc_to_md.py", [file_path])
        return self._make_result(tool_call, rc, out, err, {"file_path": file_path})


class PptToMdTool(PptMasterBaseTool):
    """PowerPoint 转 Markdown

    从 PPTX 文件提取幻灯片文本、表格、备注和嵌入图片。
    对应 ppt-master 流程 Step 1。
    """

    name = "ppt_to_md"
    description = (
        "将 PowerPoint 文件（PPTX/PPTM/PPSX）转换为 Markdown。"
        "提取幻灯片文本、表格、演讲备注和嵌入图片。"
        "在 Step 1 用户提供现有 PowerPoint 文件时使用。"
    )
    parameters = [
        ToolParameter(
            name="file_path",
            type="string",
            description="要转换的 PowerPoint 文件绝对路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在将 PowerPoint 转换为 Markdown…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return AgentToolResult.error_result(tool_call.id, "file_path is required")

        rc, out, err = self._run_script("source_to_md/ppt_to_md.py", [file_path])
        return self._make_result(tool_call, rc, out, err, {"file_path": file_path})


class WebToMdTool(PptMasterBaseTool):
    """网页转 Markdown

    抓取网页内容并转为 Markdown。支持微信公众号等高防护站点（需安装 curl_cffi）。
    对应 ppt-master 流程 Step 1。
    """

    name = "web_to_md"
    description = (
        "抓取网页并转换为 Markdown。"
        "安装 curl_cffi 后支持微信公众号和高防护站点。"
        "在 Step 1 用户提供 URL 作为源素材时使用。"
    )
    parameters = [
        ToolParameter(
            name="url",
            type="string",
            description="要抓取并转换为 Markdown 的 URL。",
            required=True,
        ),
        ToolParameter(
            name="output_path",
            type="string",
            description="可选：输出文件路径。不填则输出到标准输出。",
            required=False,
        ),
    ]
    thinking_hint = "正在抓取网页并转换为 Markdown…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        url = args.get("url", "").strip()
        if not url:
            return AgentToolResult.error_result(tool_call.id, "url is required")

        script_args = [url]
        output_path = args.get("output_path", "").strip()
        if output_path:
            script_args += ["-o", output_path]

        rc, out, err = self._run_script("source_to_md/web_to_md.py", script_args, timeout=120)
        return self._make_result(tool_call, rc, out, err, {"url": url})
