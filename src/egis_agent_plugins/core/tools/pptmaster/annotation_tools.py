"""SVG 标注工具

封装 check_annotations.py 和 svg_editor/server.py，
提供标注扫描和可视化编辑能力：

- CheckAnnotationsTool: 扫描 SVG 中的 data-edit-target / data-edit-annotation 属性
- SvgEditorTool: 启动 Flask SVG 编辑器，返回 URL 供 Studio iframe 嵌入
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
from typing import Any

from ark_agentic.core.tools.base import ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from ._base import PptMasterBaseTool, SCRIPTS_DIR

logger = logging.getLogger(__name__)

# 全局持有 svg_editor 子进程引用，避免重复启动
_svg_editor_process: subprocess.Popen | None = None
_svg_editor_port: int | None = None
_svg_editor_project: str | None = None


def _is_port_in_use(port: int) -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


class CheckAnnotationsTool(PptMasterBaseTool):
    """扫描 SVG 文件中的编辑标注

    扫描项目 svg_output/ 下所有 SVG 的 data-edit-target / data-edit-annotation 属性，
    输出待修改元素清单。Agent 用此工具发现 SVG 中需要人工/AI 编辑的标注点。
    """

    name = "ppt_check_annotations"
    description = (
        "扫描 SVG 文件中的编辑标注（data-edit-target / data-edit-annotation）。"
        "输入项目目录或单个 SVG 文件路径，返回待修改元素清单。"
        "在 SVG 生成后调用，以发现需要后续编辑的标注点。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径（扫描 svg_output/ 下所有 SVG），或单个 .svg 文件路径。",
            required=True,
        ),
    ]
    thinking_hint = "正在扫描 SVG 标注…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        raw_path = args.get("project_path", "").strip()
        if not raw_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        # 优先从 session state 取权威项目路径
        resolved = self._project_path_from(args, context) or raw_path

        rc, out, err = self._run_script("check_annotations.py", [resolved])
        return self._make_result(tool_call, rc, out, err, {"project_path": resolved})


class SvgEditorTool(PptMasterBaseTool):
    """启动 SVG 编辑器（Flask Web 服务）

    在后台启动 svg_editor/server.py，返回可访问的 URL。
    Studio 前端可通过 iframe 嵌入该 URL 实现可视化编辑。
    同一项目重复调用会复用已有实例。
    """

    name = "ppt_svg_editor"
    description = (
        "启动 SVG 可视化编辑器。"
        "在后台启动 Flask 服务，返回编辑器 URL。"
        "Studio 前端通过 iframe 嵌入该 URL。"
        "同一项目重复调用会复用已有实例。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径（包含 svg_output/ 子目录）。",
            required=True,
        ),
        ToolParameter(
            name="port",
            type="integer",
            description="监听端口，默认 5050。",
            required=False,
        ),
    ]
    thinking_hint = "正在启动 SVG 编辑器…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        global _svg_editor_process, _svg_editor_port, _svg_editor_project

        args = tool_call.arguments or {}
        raw_path = args.get("project_path", "").strip()
        if not raw_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        resolved = self._project_path_from(args, context) or raw_path
        port = int(args.get("port", 5050))

        # 复用已有实例：相同项目 + 相同端口 + 进程仍存活
        if (
            _svg_editor_process is not None
            and _svg_editor_process.poll() is None
            and _svg_editor_project == resolved
            and _svg_editor_port == port
        ):
            url = f"http://127.0.0.1:{port}"
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=json.dumps(
                    {"url": url, "project_path": resolved, "message": "SVG 编辑器已在运行", "reused": True},
                    ensure_ascii=False,
                ),
                is_error=False,
                metadata={"url": url, "project_path": resolved, "reused": True},
                events=[],
            )

        # 端口冲突检查
        if _is_port_in_use(port):
            return AgentToolResult.error_result(
                tool_call.id,
                f"端口 {port} 已被占用。请指定其他端口，或先关闭占用进程。",
            )

        # 关闭旧实例
        if _svg_editor_process is not None and _svg_editor_process.poll() is None:
            logger.info("[PptMaster] 关闭旧 SVG 编辑器 (port=%s)", _svg_editor_port)
            _svg_editor_process.terminate()
            try:
                _svg_editor_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _svg_editor_process.kill()

        # 启动新实例
        script_path = SCRIPTS_DIR / "svg_editor" / "server.py"
        if not script_path.exists():
            return AgentToolResult.error_result(
                tool_call.id,
                f"脚本不存在：{script_path}",
            )

        cmd = [
            sys.executable, str(script_path),
            resolved,
            "--port", str(port),
            "--no-browser",
            "--live",
            "--timeout", "0",
        ]

        child_env = {**os.environ, "GRPC_VERBOSITY": "ERROR", "GRPC_ENABLE_FORK_SUPPORT": "0"}

        logger.info("[PptMaster] 启动 SVG 编辑器: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(SCRIPTS_DIR),
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as exc:
            return AgentToolResult.error_result(
                tool_call.id,
                f"启动 SVG 编辑器失败：{exc}",
            )

        # 等待短暂时间确认进程没有立即退出
        import time
        time.sleep(1.0)
        if proc.poll() is not None:
            _, stderr = proc.communicate(timeout=3)
            return AgentToolResult.error_result(
                tool_call.id,
                f"SVG 编辑器启动后立即退出 (exit {proc.returncode})：{stderr.decode(errors='replace')}",
            )

        _svg_editor_process = proc
        _svg_editor_port = port
        _svg_editor_project = resolved

        url = f"http://127.0.0.1:{port}"
        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=json.dumps(
                {"url": url, "project_path": resolved, "message": "SVG 编辑器已启动", "mode": "live", "port": port, "pid": proc.pid},
                ensure_ascii=False,
            ),
            is_error=False,
            metadata={"url": url, "project_path": resolved, "port": port, "pid": proc.pid},
            events=[],
        )
