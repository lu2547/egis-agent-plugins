"""docgen_source_parse_mineru — 调用 MinerU 解析文档为 Markdown

将上传的 PDF/Word/图片等文件通过 MinerU 解析为 Markdown 格式。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.tools.docgen._project_model import (
    ProjectStore, ArtifactMeta, append_event,
)

logger = logging.getLogger(__name__)

# MinerU 后端: cpu / cuda
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "cpu")


def _try_mineru_python_api(
    input_file: Path,
    output_dir: Path,
    backend: str = "cpu",
) -> str | None:
    """尝试通过 Python API 调用 MinerU

    Returns:
        Markdown 内容，失败返回 None
    """
    try:
        from mineru.cli.common import aio_do_parse

        import asyncio

        async def _parse():
            results = await aio_do_parse(
                str(input_file),
                output_dir=str(output_dir),
                backend=backend,
            )
            return results

        results = asyncio.get_event_loop().run_until_complete(_parse())

        # 查找输出的 markdown 文件
        md_files = list(output_dir.glob("**/*.md"))
        if md_files:
            md_content = md_files[0].read_text(encoding="utf-8")
            logger.info("[MinerU Python API] Parsed %d chars from %s", len(md_content), input_file.name)
            return md_content

        return None
    except ImportError:
        logger.info("[MinerU] Python API not available, will try CLI")
        return None
    except Exception as e:
        logger.warning("[MinerU Python API] Failed: %s", e)
        return None


def _try_mineru_cli(
    input_file: Path,
    output_dir: Path,
    backend: str = "cpu",
) -> str | None:
    """尝试通过 CLI 调用 MinerU

    Returns:
        Markdown 内容，失败返回 None
    """
    try:
        cmd = [
            "mineru",
            "-p", str(input_file),
            "-o", str(output_dir),
            "-b", backend,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning("[MinerU CLI] Failed: %s", result.stderr[:500])
            return None

        # 查找输出的 markdown 文件
        md_files = list(output_dir.glob("**/*.md"))
        if md_files:
            md_content = md_files[0].read_text(encoding="utf-8")
            return md_content

        return None
    except FileNotFoundError:
        logger.info("[MinerU] CLI not available")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("[MinerU CLI] Timeout after 300s")
        return None
    except Exception as e:
        logger.warning("[MinerU CLI] Failed: %s", e)
        return None


def _fallback_simple_extract(input_file: Path) -> str:
    """简单的文本提取回退方案

    对 docx 使用 python-docx，对 txt/md 直接读取。
    """
    suffix = input_file.suffix.lower()

    if suffix in (".txt", ".md"):
        return input_file.read_text(encoding="utf-8")

    if suffix == ".docx":
        try:
            from docx import Document
            doc = Document(str(input_file))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)
        except ImportError:
            return f"[无法解析 {input_file.name}: python-docx 未安装]"
        except Exception as e:
            return f"[解析失败 {input_file.name}: {e}]"

    return f"[无法解析 {input_file.name}: 不支持的文件类型 {suffix}]"


class DocgenSourceParseMineruTool(AgentTool):
    """调用 MinerU 解析文档为 Markdown

    优先使用 MinerU Python API，回退 CLI，最终回退简单文本提取。
    """

    name = "docgen_source_parse_mineru"
    description = (
        "调用 MinerU 将上传的文档（PDF/Word/图片）解析为 Markdown 格式。\n"
        "解析结果保存为 artifact，供后续模板生成使用。\n"
        "这是一个耗时操作，可能需要几分钟。"
    )
    parameters = [
        ToolParameter(
            name="project_path",
            type="string",
            description="项目根目录路径",
            required=True,
        ),
        ToolParameter(
            name="upload_artifact_key",
            type="string",
            description="上传文件的 artifact 键名。默认: 'upload'",
            required=False,
        ),
        ToolParameter(
            name="output_artifact_key",
            type="string",
            description="输出 Markdown 的 artifact 键名。默认: 'tender_markdown'",
            required=False,
        ),
    ]
    thinking_hint = "正在解析文档（可能需要几分钟）…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        project_path_str = args.get("project_path", "").strip()
        upload_key = args.get("upload_artifact_key", "upload").strip()
        output_key = args.get("output_artifact_key", "tender_markdown").strip()

        if not project_path_str:
            return AgentToolResult.error_result(tool_call.id, "project_path is required")

        project_path = Path(project_path_str)
        store = ProjectStore()

        # 读取 upload artifact
        manifest = store.get_project(project_path)
        if manifest is None:
            return AgentToolResult.error_result(tool_call.id, f"Project not found: {project_path_str}")

        upload_meta = manifest.get_artifact(upload_key)
        if upload_meta is None:
            return AgentToolResult.error_result(
                tool_call.id, f"Upload artifact '{upload_key}' not found. Upload a file first."
            )

        input_file = project_path / upload_meta.path
        if not input_file.exists():
            return AgentToolResult.error_result(
                tool_call.id, f"Upload file not found: {input_file}"
            )

        # 解析输出目录
        parsed_dir = project_path / "sources" / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        mineru_cache = project_path / "cache" / "mineru"
        mineru_cache.mkdir(parents=True, exist_ok=True)

        # 尝试 MinerU Python API
        md_content = _try_mineru_python_api(input_file, mineru_cache, MINERU_BACKEND)

        # 回退 CLI
        if md_content is None:
            md_content = _try_mineru_cli(input_file, mineru_cache, MINERU_BACKEND)

        # 回退简单提取
        parse_method = "mineru"
        if md_content is None:
            md_content = _fallback_simple_extract(input_file)
            parse_method = "fallback"
            logger.warning("[DocgenParseMineru] MinerU unavailable, used fallback extraction")

        # 保存 Markdown
        md_filename = input_file.stem + ".md"
        md_path = parsed_dir / md_filename
        md_path.write_text(md_content, encoding="utf-8")

        relative_path = str(md_path.relative_to(project_path))

        # 注册 artifact
        output_meta = ArtifactMeta(
            artifact_id=output_key,
            kind="parsed",
            file_format="md",
            path=relative_path,
            created_by=self.name,
            source_artifacts=[upload_key],
            metadata={
                "parse_method": parse_method,
                "char_count": len(md_content),
                "source_file": input_file.name,
            },
        )
        store.register_artifact(project_path, output_key, output_meta)

        append_event(
            project_path,
            "document_parsed",
            step=self.name,
            artifact_id=output_key,
            parse_method=parse_method,
            char_count=len(md_content),
        )

        # 内容预览（截断）
        preview = md_content[:2000]
        if len(md_content) > 2000:
            preview += f"\n\n... [truncated, total {len(md_content)} chars]"

        logger.info(
            "[DocgenParseMineru] Parsed %s → %s (%d chars, method=%s)",
            input_file.name, relative_path, len(md_content), parse_method,
        )

        return AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=(
                f"文档解析完成:\n"
                f"  文件: {input_file.name}\n"
                f"  解析方式: {parse_method}\n"
                f"  输出: {relative_path}\n"
                f"  字符数: {len(md_content)}\n\n"
                f"--- 内容预览 ---\n{preview}"
            ),
            is_error=False,
            metadata={
                "artifact_id": output_key,
                "artifact_path": str(md_path),
                "parse_method": parse_method,
                "state_delta": {
                    f"docgen_state.artifacts.{output_key}": str(md_path),
                    "docgen_state.tender_markdown_artifact_id": output_key,
                },
            },
            events=[],
        )
