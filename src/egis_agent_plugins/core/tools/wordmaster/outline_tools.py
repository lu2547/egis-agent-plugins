"""Word Master 模板化文档生成工具

docx_generate_from_outline — 接收结构化大纲（标题+章节数组），
内部加载 scripts/templates/outline_generate.js 模板，填充数据后执行 node 生成 .docx。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)

from ._base import SCRIPTS_DIR, _strip_grpc_noise

logger = logging.getLogger(__name__)

# 模板文件路径
_TEMPLATE_PATH = SCRIPTS_DIR / "templates" / "outline_generate.js"


def _try_json_loads(text: str) -> Any:
    """安全解析 JSON，失败返回 None。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


class DocxGenerateFromOutlineTool(AgentTool):
    """从结构化大纲生成 Word 文档（推荐方式）

    LLM 传入标题和章节数组，工具内部：
    1. 加载 scripts/templates/outline_generate.js 模板
    2. 填充数据占位符
    3. 执行 node 生成 .docx
    4. 移到 exports/ 并验证
    """

    name = "docx_generate_from_outline"
    description = (
        "【推荐】从结构化大纲直接生成企业级 Word 文档（.docx）。"
        "只需传入文档标题和章节结构数组，自动套用平安养老险企业级样式模板。"
        "无需手写 JS 代码。适合报告、分析文档、方案等标准文档。"
        "使用前必须先调用 docx_project_init 初始化项目。"
    )
    parameters = [
        ToolParameter(
            name="title",
            type="string",
            description="文档封面大标题。示例：'平安养老保险企业年金市场分析报告'",
            required=True,
        ),
        ToolParameter(
            name="sections",
            type="array",
            description=(
                '章节结构数组。每个元素格式：'
                '{"heading": "一、章节标题", "level": 1, "content": "段落文本", '
                '"bullets": ["要点1", "要点2"], '
                '"tables": [{"headers": ["列名1","列名2"], "rows": [["值1","值2"]]}], '
                '"subsections": [...]}。'
                'level: 1=一级标题 2=二级标题 3=三级标题。'
                'content: 段落文本，多段用换行分隔，支持**加粗**。'
                'bullets: 项目符号列表。'
                '【重要】表格数据必须放入 tables 字段（结构化 headers+rows），'
                '严禁将 Markdown 表格语法（| 列 | 列 |）写入 content，否则会渲染为纯文本。'
            ),
            required=True,
        ),
        ToolParameter(
            name="output_filename",
            type="string",
            description="输出文件名，默认 'doc.docx'",
            required=False,
        ),
    ]
    thinking_hint = "正在从大纲生成 Word 文档…"

    @staticmethod
    def _parse_sections(raw: Any) -> list | None:
        """解析 sections 参数，兼容多种输入格式。"""
        # 已经是 list 则直接返回
        if isinstance(raw, list):
            return raw if len(raw) > 0 else None

        # 字符串需要 JSON 解析
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None

            # 去掉 markdown 代码围栏
            if text.startswith("```"):
                lines = text.split("\n")
                # 去掉首尾 ``` 行
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()

            # 尝试直接解析
            parsed = _try_json_loads(text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed

            # 容错：替换中文引号后重试
            sanitized = text.replace('\u201c', '"').replace('\u201d', '"')
            sanitized = sanitized.replace('\u2018', "'").replace('\u2019', "'")
            parsed = _try_json_loads(sanitized)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed

            # 容错：提取 [ ... ] 子串后重试（处理前后有垃圾字符）
            first_bracket = text.find('[')
            last_bracket = text.rfind(']')
            if first_bracket >= 0 and last_bracket > first_bracket:
                substring = text[first_bracket:last_bracket + 1]
                parsed = _try_json_loads(substring)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed

            logger.warning(
                "[DocxOutline] sections 解析失败，类型=%s，前200字符: %s",
                type(raw).__name__, text[:200],
            )
            return None

        # 其他类型（dict 等）不支持
        logger.warning("[DocxOutline] sections 不支持的类型: %s", type(raw).__name__)
        return None

    async def execute(self, tool_call, context: dict[str, Any] | None = None) -> AgentToolResult:
        args = tool_call.arguments or {}

        # ── 详细记录原始入参，方便排查 LLM 传参问题 ──
        logger.info(
            "[DocxOutline] raw args: keys=%s, sections type=%s",
            list(args.keys()),
            type(args.get("sections")).__name__ if "sections" in args else "(missing)",
        )
        if "_raw" in args:
            logger.info("[DocxOutline] _raw 流转，前200字符: %s", str(args["_raw"])[:200])

        # 处理 _raw 降级
        if "_raw" in args and len(args) == 1:
            try:
                parsed = json.loads(args["_raw"])
                if isinstance(parsed, dict):
                    args = parsed
                    logger.info("[DocxOutline] _raw 解析成功: keys=%s", list(args.keys()))
            except (json.JSONDecodeError, TypeError):
                logger.warning("[DocxOutline] _raw JSON解析失败")

        title = (args.get("title") or "").strip()
        sections_raw = args.get("sections", "")
        output_filename = (args.get("output_filename") or "doc.docx").strip()

        # 记录解析前的 sections 类型和内容摘要
        logger.info(
            "[DocxOutline] sections_raw: type=%s, len=%s, preview=%s",
            type(sections_raw).__name__,
            len(sections_raw) if isinstance(sections_raw, (str, list)) else "N/A",
            str(sections_raw)[:300],
        )

        if not title:
            return AgentToolResult.error_result(tool_call.id, "title is required")
        if not sections_raw:
            return AgentToolResult.error_result(tool_call.id, "sections is required")

        # 解析 sections（兼容 list/string，容错中文引号、markdown 围栏、前后垃圾字符）
        sections = self._parse_sections(sections_raw)
        if sections is None:
            # 拼接错误信息，包含原始类型和前 100 字符，方便 LLM 定位问题
            raw_preview = str(sections_raw)[:100] if sections_raw else "(empty)"
            return AgentToolResult.error_result(
                tool_call.id,
                f"sections 解析失败。"
                f"sections 必须是 JSON 数组，请直接传数组而非字符串。"
                f"\n接收到的类型: {type(sections_raw).__name__}"
                f"\n前100字符: {raw_preview}"
            )

        # 解析 project_path（state 优先 → args 兜底）
        ctx = context or {}
        wm_state = ctx.get("wordmaster_state", {})
        project_path = ""
        if isinstance(wm_state, dict):
            project_path = (wm_state.get("project_path", "") or "").strip()
        if not project_path:
            project_path = (args.get("project_path", "") or "").strip()
        if not project_path:
            return AgentToolResult.error_result(
                tool_call.id,
                "project_path 未找到，请先调用 docx_project_init 初始化项目"
            )

        project = Path(project_path)
        scripts_dir = project / "scripts"
        exports_dir = project / "exports"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        exports_dir.mkdir(parents=True, exist_ok=True)

        # 加载模板
        if not _TEMPLATE_PATH.exists():
            return AgentToolResult.error_result(
                tool_call.id,
                f"模板文件不存在: {_TEMPLATE_PATH}"
            )
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")

        # 填充占位符
        js_code = template.replace(
            "__DOC_TITLE__", json.dumps(title, ensure_ascii=False)
        ).replace(
            "__OUTPUT_FILENAME__", json.dumps(output_filename, ensure_ascii=False)
        ).replace(
            "__SECTIONS__", json.dumps(sections, ensure_ascii=False, indent=2)
        )

        # 写入脚本
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        script_file = scripts_dir / f"generate_outline_{ts}.js"
        script_file.write_text(js_code, encoding="utf-8")
        logger.info("[DocxOutline] Wrote script: %s (%d bytes)", script_file, len(js_code))

        # 执行 node
        node_env = os.environ.copy()
        use_shell = (os.name == "nt")
        try:
            global_modules = subprocess.run(
                ["npm", "root", "-g"], capture_output=True, text=True, shell=use_shell,
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
                capture_output=True, text=True,
                shell=use_shell, timeout=120, env=node_env,
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
            error_msg = (result.stderr or result.stdout or "Unknown error")[:500]
            logger.error("[DocxOutline] node failed (exit %d): %s", result.returncode, error_msg)
            return AgentToolResult(
                tool_call_id=tool_call.id,
                result_type=ToolResultType.TEXT,
                content=f"[Error] node 执行失败 (exit {result.returncode}):\n{error_msg}",
                is_error=True,
                metadata={"returncode": result.returncode},
                events=[],
            )

        # 查找生成的文件
        generated = scripts_dir / output_filename
        if not generated.exists():
            alt = project / output_filename
            if alt.exists():
                generated = alt
            else:
                return AgentToolResult.error_result(
                    tool_call.id,
                    f"生成文件未找到: {output_filename}。搜索路径: {scripts_dir}, {project}"
                )

        # 移到 exports/
        export_path = exports_dir / output_filename
        if export_path.exists():
            export_path.unlink()
        shutil.move(str(generated), str(export_path))
        logger.info("[DocxOutline] Exported: %s", export_path)

        # 验证
        validate_script = SCRIPTS_DIR / "office" / "validate.py"
        validate_msg = ""
        if validate_script.exists():
            try:
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
            f"文档生成成功: {export_path}\n"
            f"大小: {file_size} bytes\n"
            f"章节数: {len(sections)}"
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
            tool_name="docx_generate_from_outline",
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(title=output_filename, summary=f"已生成含 {len(sections)} 个章节的文档", icon="wordmaster", status="success"),
            detailed=DetailedView(title="大纲文档生成", sections=[
                ViewSection(heading="摘要", content_type="text", data=f"{output_filename} | {len(sections)}章节 | {file_size}B"),
            ]),
        )
        apply_dual_layer(result, digest, f"[WordMaster] 大纲文档生成成功: {output_filename} ({len(sections)}章节, {file_size}B)")
        return result
