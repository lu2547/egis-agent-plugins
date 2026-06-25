"""Word Master 文档生成工具

docx_generate — 接收 LLM 生成的 docx-js JavaScript 代码，通过 node 执行生成 .docx 文件
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

from ._base import WordMasterBaseTool, SCRIPTS_DIR, _strip_grpc_noise

logger = logging.getLogger(__name__)


class DocxGenerateTool(AgentTool):
    """执行 docx-js JavaScript 脚本生成 .docx 文件

    工作流程：
    1. 接收 LLM 生成的 JavaScript 代码（使用 docx-js 库）
    2. 写入 <project_path>/scripts/generate_<timestamp>.js
    3. 通过 subprocess 调用 node 执行脚本
    4. 将生成的 .docx 移到 <project_path>/exports/
    5. 调用 validate.py 验证
    6. 返回文件路径

    依赖：node + npm install -g docx
    """

    name = "docx_generate"
    description = (
        "执行 docx-js JavaScript 代码生成 .docx 文件。"
        "【重要】调用此工具前必须先 read_skill('wordmaster') 获取企业级样式模板，"
        "严格按照平安养老险企业级样式（微软雅黑、小四正文、小二一级标题、目录、页眉页脚）生成代码。"
        "LLM 应使用 docx-js 库编写完整的 Node.js 脚本，"
        "包含 Document 构造、Packer 导出和 fs.writeFileSync 写入。"
        "工具会自动执行脚本、验证输出并移到 exports/ 目录。"
        "依赖：环境需安装 node 和 npm install -g docx。"
    )
    parameters = [
        ToolParameter(
            name="js_code",
            type="string",
            description=(
                "完整的 Node.js 脚本代码，使用 docx-js 生成 .docx 文件。"
                "脚本必须使用 fs.writeFileSync 将 buffer 写入磁盘。"
                "输出文件名在脚本中指定（如 'doc.docx'）。"
            ),
            required=True,
        ),
        ToolParameter(
            name="output_filename",
            type="string",
            description=(
                "JS 脚本中 fs.writeFileSync 写入的文件名。"
                "工具用此名在脚本执行目录中查找生成的文件。"
                "示例：'doc.docx'、'report.docx'"
            ),
            required=True,
        ),
        ToolParameter(
            name="project_path",
            type="string",
            description="项目目录的绝对路径。",
            required=False,
        ),
    ]
    thinking_hint = "正在生成 Word 文档…"

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}
        js_code = args.get("js_code", "").strip()
        output_filename = args.get("output_filename", "doc.docx").strip()

        if not js_code:
            return AgentToolResult.error_result(tool_call.id, "js_code is required")

        # 解析 project_path
        ctx = context or {}
        wm_state = ctx.get("wordmaster_state", {})
        project_path = ""
        if isinstance(wm_state, dict):
            project_path = (wm_state.get("project_path", "") or "").strip()
        if not project_path:
            project_path = (args.get("project_path", "") or "").strip()
        if not project_path:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project = Path(project_path)
        scripts_dir = project / "scripts"
        exports_dir = project / "exports"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        exports_dir.mkdir(parents=True, exist_ok=True)

        # 写入 JS 脚本
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        script_file = scripts_dir / f"generate_{ts}.js"
        script_file.write_text(js_code, encoding="utf-8")
        logger.info("[DocxGenerate] Wrote script: %s", script_file)

        # 执行 node（注入 NODE_PATH 以找到全局安装的 docx 模块）
        node_env = os.environ.copy()
        use_shell = (os.name == "nt")  # Windows 上 npm/node 可能是 .cmd，需要 shell=True
        try:
            global_modules = subprocess.run(
                ["npm", "root", "-g"], capture_output=True, text=True,
                shell=use_shell,
            ).stdout.strip()
        except (FileNotFoundError, OSError):
            global_modules = ""
        if global_modules:
            existing = node_env.get("NODE_PATH", "")
            sep = ";" if os.name == "nt" else ":"
            node_env["NODE_PATH"] = f"{global_modules}{sep}{existing}" if existing else global_modules

        try:
            result = subprocess.run(
                ["node", str(script_file)],
                cwd=str(scripts_dir),
                capture_output=True,
                text=True,
                shell=use_shell,
                timeout=120,
                env=node_env,
            )
        except FileNotFoundError:
            return AgentToolResult.error_result(
                tool_call.id,
                "node not found. Please install Node.js and run: npm install -g docx"
            )
        except subprocess.TimeoutExpired:
            return AgentToolResult.error_result(
                tool_call.id, "Script timed out after 120s"
            )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            logger.error("[DocxGenerate] node failed (exit %d): %s", result.returncode, error_msg)
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=f"[Error] node execution failed (exit {result.returncode}):\n{error_msg}",
                is_error=True,
                metadata={"returncode": result.returncode},
                events=[],
            )

        # 查找生成的文件
        generated = scripts_dir / output_filename
        if not generated.exists():
            # 也在 project 根目录找
            alt = project / output_filename
            if alt.exists():
                generated = alt
            else:
                return AgentToolResult.error_result(
                    tool_call.id,
                    f"Generated file not found: {output_filename}. "
                    f"Searched in: {scripts_dir}, {project}"
                )

        # 移到 exports/
        export_path = exports_dir / output_filename
        shutil.move(str(generated), str(export_path))
        logger.info("[DocxGenerate] Moved to exports: %s", export_path)

        # 验证
        validate_script = SCRIPTS_DIR / "office" / "validate.py"
        validate_msg = ""
        if validate_script.exists():
            try:
                import sys
                validate_env = {
                    **os.environ,
                    "GRPC_VERBOSITY": "ERROR",
                    "GRPC_ENABLE_FORK_SUPPORT": "0",
                }
                vr = subprocess.run(
                    [sys.executable, str(validate_script), str(export_path)],
                    capture_output=True, text=True, timeout=60,
                    env=validate_env,
                )
                clean_err = _strip_grpc_noise(vr.stderr)
                if vr.returncode != 0:
                    validate_msg = f"\n\n[Validation Warning]\n{clean_err or vr.stdout}"
                else:
                    validate_msg = "\n\nValidation: PASSED"
            except Exception as ve:
                validate_msg = f"\n\n[Validation skipped: {ve}]"

        file_size = export_path.stat().st_size
        content = (
            f"Document generated successfully: {export_path}\n"
            f"Size: {file_size} bytes"
            f"{validate_msg}"
        )

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=content,
            is_error=False,
            metadata={
                "file_path": str(export_path),
                "size_bytes": file_size,
                "project_path": project_path,
            },
            events=[],
        )
        digest = FrontendDigest(
            tool_name="docx_generate",
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(title=output_filename, summary=f"文档已生成，共 {file_size//1024} KB" if file_size >= 1024 else f"文档已生成，共 {file_size} 字节", icon="wordmaster", status="success"),
            detailed=DetailedView(title="文档生成", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{output_filename} | {file_size}B"),
            ]),
        )
        apply_dual_layer(result, digest, f"[WordMaster] 文档生成成功: {output_filename} ({file_size}B)")
        return result
