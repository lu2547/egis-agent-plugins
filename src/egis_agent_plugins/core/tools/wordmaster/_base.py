"""Word Master 工具基类

所有 wordmaster Tool 均继承此基类，提供统一的 subprocess 调用封装。
脚本目录通过环境变量 WORD_MASTER_SCRIPTS_DIR 配置。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from ark_agentic.core.tools.base import AgentTool
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest,
    MinimalView,
    DetailedView,
    ViewSection,
    ToolDisplayType,
    apply_dual_layer,
)

# 复用 pptmaster 的路径校正 & 日期分层解析
from egis_agent_plugins.core.tools.pptmaster._base import (
    coerce_path_under_state_project,
    resolve_projects_relative,
)

logger = logging.getLogger(__name__)

# 脚本目录：优先从环境变量读取，回退到本文件旁边的 scripts/
_ENV_SCRIPTS_DIR = os.getenv("WORD_MASTER_SCRIPTS_DIR", "")
SCRIPTS_DIR: Path = (
    Path(_ENV_SCRIPTS_DIR)
    if _ENV_SCRIPTS_DIR
    else Path(__file__).resolve().parent / "scripts"
)

# 项目基础目录（多用户/日期分层）
# 优先读 WORD_MASTER_PROJECTS_DIR，未配置则回退 PPT_MASTER_PROJECTS_DIR，
# 保证 Word 和 PPT 默认共享同一个 projects 目录。
_ENV_PROJECTS_DIR = (
    os.getenv("WORD_MASTER_PROJECTS_DIR", "")
    or os.getenv("PPT_MASTER_PROJECTS_DIR", "")
)
PROJECTS_DIR: Path | None = Path(_ENV_PROJECTS_DIR) if _ENV_PROJECTS_DIR else None

# 项目内已知子目录白名单
_KNOWN_PROJECT_SUBDIRS = frozenset({
    "sources", "scripts", "output", "unpacked", "exports",
})

# gRPC C++ core 在 fork 后会输出噪声日志，需要过滤
_GRPC_NOISE_RE = re.compile(
    r"^[IWED]\d{4} [\d:.]+\s+\d+ \w+\.cc:\d+\].*$",
    re.MULTILINE,
)


def _strip_grpc_noise(stderr: str) -> str:
    """移除 gRPC C++ core 的 fork 噪声日志行"""
    if not stderr:
        return stderr
    cleaned = _GRPC_NOISE_RE.sub("", stderr).strip()
    return cleaned


class WordMasterBaseTool(AgentTool):
    """Word Master 工具基类

    提供 _run_script() 方法，通过 subprocess 调用 wordmaster scripts/ 下的 Python 脚本。
    子类只需实现 name、description、parameters 和 execute()。
    """

    name: str = "_wordmaster_base"
    description: str = "Word Master base tool (abstract)"

    @classmethod
    def _project_path_from(
        cls,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> str:
        """解析项目路径：state 优先，args 兜底。"""
        ctx = context or {}
        wm_state = ctx.get("wordmaster_state", {})
        state_pp = ""
        if isinstance(wm_state, dict):
            state_pp = (wm_state.get("project_path", "") or "").strip()

        raw_pp = ""
        if isinstance(args, dict):
            raw_pp = (args.get("project_path") or "").strip()

        if state_pp:
            if raw_pp:
                _, warn = coerce_path_under_state_project(
                    raw_pp, state_pp, PROJECTS_DIR,
                )
                if warn:
                    logger.warning("[WordMaster] %s", warn)
            return state_pp

        if raw_pp:
            return cls._abs_project_path(raw_pp)

        return ""

    @staticmethod
    def _abs_project_path(project_path: str) -> str:
        """将 project_path 转为绝对路径。

        - 已是绝对路径：直接返回
        - 相对路径：基于 PROJECTS_DIR 解析，按 YYYYMMDD 日期分层
          1) 先扫描已有日期目录下的同名项目（兼容跨天复用）
          2) 找不到则回落到今日日期目录（保证新项目按规范分层）
        """
        p = Path(project_path)
        if p.is_absolute():
            return str(p)
        if PROJECTS_DIR:
            return str(resolve_projects_relative(project_path, PROJECTS_DIR))
        return str(p)

    def _resolve_file_path(
        self,
        raw_path: str,
        context: dict[str, Any] | None,
    ) -> Path:
        """以 session state 中的 project_path 为权威解析文件路径。

        解析规则：
        1. 相对路径：基于 state project_path 拼接
        2. 绝对路径：走 coerce_path_under_state_project 纠偏
        3. 文件不存在时：尝试 output/ → exports/ 互换
        """
        ctx = context or {}
        # 兼容两种 key 格式: "user:wordmaster_state" 和 "wordmaster_state"
        wm_state = ctx.get("user:wordmaster_state") or ctx.get("wordmaster_state") or {}
        state_pp = ""
        if isinstance(wm_state, dict):
            state_pp = (wm_state.get("project_path", "") or "").strip()

        p = Path(raw_path)

        if p.is_absolute():
            if state_pp:
                resolved_str, warn = coerce_path_under_state_project(
                    raw_path, state_pp, PROJECTS_DIR,
                )
                if warn:
                    logger.warning("[WordMaster] %s", warn)
                resolved = Path(resolved_str)
            else:
                resolved = p
        elif state_pp:
            resolved = Path(state_pp) / p
        else:
            resolved = p

        # 文件不存在时：output/ ↔ exports/ 互换
        if not resolved.exists():
            resolved = self._try_output_exports_fallback(resolved)

        return resolved

    @staticmethod
    def _try_output_exports_fallback(path: Path) -> Path:
        """当文件不存在时，尝试 output/ ↔ exports/ 互换。

        生成工具将文件放到 exports/，但 LLM 经常猜测为 output/，反之亦然。
        """
        parts = path.parts
        for i, seg in enumerate(parts):
            if seg == "output":
                alt = Path(*parts[:i], "exports", *parts[i + 1:])
                if alt.exists():
                    logger.info("[WordMaster] Fallback: %s -> %s", path, alt)
                    return alt
            elif seg == "exports":
                alt = Path(*parts[:i], "output", *parts[i + 1:])
                if alt.exists():
                    logger.info("[WordMaster] Fallback: %s -> %s", path, alt)
                    return alt
        return path

    def _run_script(
        self,
        script_rel: str,
        args: list[str],
        cwd: str | Path | None = None,
        timeout: int = 300,
    ) -> tuple[int, str, str]:
        """执行 wordmaster 脚本

        Args:
            script_rel: 相对于 SCRIPTS_DIR 的脚本路径，如 "office/unpack.py"
            args: 额外命令行参数列表
            cwd: 工作目录；None 则使用 SCRIPTS_DIR
            timeout: 超时秒数（默认 300s）

        Returns:
            (returncode, stdout, stderr)
        """
        script_path = SCRIPTS_DIR / script_rel
        if not script_path.exists():
            return -1, "", f"Script not found: {script_path}"

        cmd = [sys.executable, str(script_path)] + args
        work_dir = str(cwd) if cwd else str(SCRIPTS_DIR)

        child_env = {
            **os.environ,
            "GRPC_VERBOSITY": "ERROR",
            "GRPC_ENABLE_FORK_SUPPORT": "0",
        }

        logger.info("[WordMaster] Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
            )
            stdout = result.stdout
            stderr = _strip_grpc_noise(result.stderr)
            if result.returncode != 0:
                logger.error(
                    "[WordMaster] Script failed (exit %d)\n--- stdout ---\n%s\n--- stderr ---\n%s",
                    result.returncode, stdout, stderr,
                )
            else:
                logger.debug("[WordMaster] stdout: %s", stdout)
            return result.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Script timed out after {timeout}s"
        except Exception as exc:
            return -1, "", f"Script execution error: {exc}"

    def _make_result(
        self,
        tool_call,
        returncode: int,
        stdout: str,
        stderr: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        """将 subprocess 结果封装为 AgentToolResult，并注入双层返回"""
        is_error = returncode != 0
        if is_error:
            content = f"[Error (exit {returncode})]\n{stderr or stdout or 'Unknown error'}"
        else:
            content = stdout.strip() or "(script completed with no output)"
            if stderr.strip():
                content += f"\n\n[stderr]\n{stderr.strip()}"

        meta: dict[str, Any] = {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if extra_meta:
            meta.update(extra_meta)

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=content,
            is_error=is_error,
            metadata=meta,
            events=[],
        )

        # 双层返回: llm_digest + frontend_digest
        digest = self._build_digest(tool_call.name, is_error, stdout, stderr)
        apply_dual_layer(result, digest, content)

        return result

    def _build_digest(
        self,
        tool_name: str,
        is_error: bool,
        stdout: str,
        stderr: str,
    ) -> FrontendDigest:
        """构建前端展示摘要（参考 Qoder 平台风格：title=操作对象，summary=紧凑状态）"""
        status = "error" if is_error else "success"
        summary = "执行未能完成" if is_error else "操作已顺利完成"

        # title：从 stdout 提取文件名或关键信息作为操作对象
        title = tool_name
        if stdout.strip():
            first_line = stdout.strip().split("\n")[0][:60]
            if first_line:
                title = first_line

        sections: list[ViewSection] = []
        if is_error and stderr.strip():
            sections.append(
                ViewSection(heading="摘要", content_type="text", data=f"失败: {stderr.strip()[:100]}")
            )
        else:
            preview = stdout.strip().split("\n")[0][:80] if stdout.strip() else "已完成"
            sections.append(
                ViewSection(heading="摘要", content_type="text", data=preview)
            )

        return FrontendDigest(
            tool_name=tool_name,
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(
                title=title,
                summary=summary,
                icon="wordmaster",
                status=status,
            ),
            detailed=DetailedView(title=tool_name, sections=sections),
        )
