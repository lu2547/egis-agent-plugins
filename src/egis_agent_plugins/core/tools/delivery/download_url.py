"""文件下载链接生成工具

通用工具，适用于所有需要向用户交付可下载文件的场景：
PPT、Word、PDF、Excel 等。基于 API_HOST / API_PORT 环境变量
拼接 FastAPI 下载接口 URL，供 Agent 在生成完文件后调用。

路径参数经 itsdangerous 签名编码为不透明 token，用户看不到目录结构。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

# 延迟导入，避免循环依赖（router 和 tools 在不同层）
_encode_download_token = None
_encode_project_token = None


def _get_encode_fn():
    global _encode_download_token
    if _encode_download_token is None:
        from egis_agent_plugins.core.service.download.token_handler import encode_download_token
        _encode_download_token = encode_download_token
    return _encode_download_token


def _get_encode_project_fn():
    global _encode_project_token
    if _encode_project_token is None:
        from egis_agent_plugins.core.service.download.token_handler import encode_project_token
        _encode_project_token = encode_project_token
    return _encode_project_token


# 下载接口支持的文件类型（与 router/download.py 白名单对齐）
_SUPPORTED_SUFFIXES = {
    ".pptx", ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".md", ".txt", ".csv", ".json", ".html", ".svg", ".png", ".jpg",
}


class CreateDownloadUrlTool(AgentTool):
    """生成文件下载链接

    在生成任意文件（PPT/Word/PDF 等）后调用，基于 API_HOST / API_PORT
    环境变量构造可点击的下载 URL，返回给用户。

    下载接口路由：GET /api/download/{token}
    token 由 itsdangerous 签发，内含加密的 project_name + file_rel。
    """

    name = "create_download_url"
    description = (
        "为已生成的文件（PPT、PDF、Word 等）生成可点击的下载链接。"
        "在文件成功创建后调用。"
        "提供 project_path 和文件在项目内的相对路径。"
        "返回基于 API 服务器地址（API_HOST / API_PORT）的下载链接。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=True,
        ),
        ToolParameter(
            name="file_path",
            type="string",
            description=(
                "文件在项目目录内的相对路径。"
                "示例：'exports/final_20260421.pptx' 或 'output/report.pdf'。"
                "不填则自动查找 exports/ 下最新的支持文件。"
            ),
            required=False,
        ),
    ]
    thinking_hint = "正在生成文件下载链接…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        if not project_path_str:
            # 回退：尝试从 session state 中获取 project_path（兼容 PPT / Word 等多技能）
            ctx = context or {}
            for state_ns in ("pptmaster_state", "wordmaster_state"):
                ns = ctx.get(state_ns, {})
                if isinstance(ns, dict):
                    pp = (ns.get("project_path") or "").strip()
                    if pp:
                        project_path_str = pp
                        break
        if not project_path_str:
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content="Error: project_path is required",
                is_error=True,
            )

        project_path = Path(project_path_str)
        project_name = project_path.name
        abs_project = str(project_path.resolve())

        # 解析文件相对路径
        file_rel = args.get("file_path", "").strip()
        if not file_rel:
            # 自动查找 exports/ 下最新的支持文件
            exports_dir = project_path / "exports"
            if exports_dir.is_dir():
                candidates = sorted(
                    [p for p in exports_dir.iterdir() if p.suffix.lower() in _SUPPORTED_SUFFIXES],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    file_rel = f"exports/{candidates[0].name}"

        if not file_rel:
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=(
                    "Error: Could not determine the file path. "
                    "Please provide file_path explicitly."
                ),
                is_error=True,
            )

        # 签发 token（路径信息加密，用户不可见；v2 直接存绝对路径）
        token = _get_encode_fn()(abs_project, file_rel)
        project_token = _get_encode_project_fn()(abs_project)

        # 构造下载 URL
        api_host = os.getenv("API_HOST", "localhost")
        api_port = os.getenv("API_PORT", "8081")
        base_url = f"http://{api_host}:{api_port}"
        download_url = f"{base_url}/api/download/{token}"
        list_url = f"{base_url}/api/download/list/{project_token}"

        filename = Path(file_rel).name
        content = (
            f"📥 文件已生成，点击下载：\n"
            f"{download_url}\n\n"
            f"📂 查看全部输出文件：\n"
            f"{list_url}"
        )
        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=content,
            is_error=False,
            metadata={
                "project_name": project_name,
                "filename": filename,
                "file_path": file_rel,
                "download_url": download_url,
                "list_url": list_url,
            },
        )
        digest = FrontendDigest(
            tool_name="create_download_url",
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(title="下载链接", summary=f"{filename} 已生成", icon="download", status="success"),
            detailed=DetailedView(title="文件下载", sections=[
                ViewSection(heading="文件信息", content_type="key_value", data={"filename": filename, "project": project_name}),
                ViewSection(heading="下载链接", content_type="text", data=download_url),
                ViewSection(heading="文件列表", content_type="text", data=list_url),
            ]),
        )
        apply_dual_layer(result, digest, f"[下载] {filename} 链接已生成")
        return result
