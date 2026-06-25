"""PPT Master 工具基类

所有 pptmaster Tool 均继承此基类，提供统一的 subprocess 调用封装。
脚本目录通过环境变量 PPT_MASTER_SCRIPTS_DIR 配置。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from datetime import datetime
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

logger = logging.getLogger(__name__)

# YYYYMMDD 日期目录正则
_DATE_DIR_RE = re.compile(r"^\d{8}$")

# 脚本目录：优先从环境变量读取，回退到本文件旁边的 scripts/
_ENV_SCRIPTS_DIR = os.getenv("PPT_MASTER_SCRIPTS_DIR", "")
SCRIPTS_DIR: Path = (
    Path(_ENV_SCRIPTS_DIR)
    if _ENV_SCRIPTS_DIR
    else Path(__file__).resolve().parent / "scripts"
)

# 资源目录（templates/icons/references 等）。
# lazy 求值：未配置时为 None，模块导入不报错；仅在真正使用 PPT 工具时由 get_skill_dir() 兜底 raise。
_ENV_SKILL_DIR = os.getenv("PPT_MASTER_SKILL_DIR", "")
SKILL_DIR: Path | None = Path(_ENV_SKILL_DIR) if _ENV_SKILL_DIR else None


def get_skill_dir() -> Path:
    """返回 PPT_MASTER_SKILL_DIR。未配置时抛 RuntimeError（供工具 execute 阶段按需调用）。"""
    if SKILL_DIR is None:
        raise RuntimeError(
            "PPT_MASTER_SKILL_DIR 环境变量未设置。"
            "请指向 egis-agents/pptmaster 目录（包含 references/ 和 templates/）。"
        )
    return SKILL_DIR

# 需过滤的 grpc fork 日志关键词（pymilvus 底层 grpcio 在 subprocess fork 时会打印）
_GRPC_FORK_NOISE_KEYWORDS = (
    "ev_poll_posix.cc",
    "FD from fork parent",
    "ev_epoll1_linux.cc",
)


def _filter_grpc_fork_noise(stderr: str) -> str:
    """从 stderr 中过滤掉 grpc fork hook 打印的 INFO 日志行。"""
    if not stderr:
        return stderr
    lines = [
        line for line in stderr.splitlines()
        if not any(kw in line for kw in _GRPC_FORK_NOISE_KEYWORDS)
    ]
    return "\n".join(lines)

# 项目目录：存放所有 PPT 项目的基础目录
_ENV_PROJECTS_DIR = os.getenv("PPT_MASTER_PROJECTS_DIR", "")
PROJECTS_DIR: Path | None = Path(_ENV_PROJECTS_DIR) if _ENV_PROJECTS_DIR else None


# 项目内已知子目录白名单：用于判断 LLM 传入的相对路径首段是否是
# 项目内子路径（如 svg_output/01.svg）还是漂移的项目名（如 my_project/...）
_KNOWN_PROJECT_SUBDIRS = frozenset({
    "sources", "svg_output", "svg_final", "notes", "images",
    "templates", "exports", "scripts", "assets",
})


def coerce_path_under_state_project(
    raw_path: str,
    state_project_path: str | None,
    projects_dir: Path | None,
) -> tuple[str, str | None]:
    """以 session state 中的 project_path 为权威，校正 LLM 传入的路径。

    第二轮对话起，LLM 经常自己重新拼写项目路径，导致丢失日期分层（YYYYMMDD/）
    或 sid_uid 后缀（例如把 ``pingan_annuity_analysis_xxx_anon`` 简写成
    ``pingan_annuity_analysis``），使文件写到错误目录。本函数以 state 中的
    完整 ``project_path`` 为基准，把 LLM 传入的路径强制纠正回该项目内。

    解析规则：
        - ``state_project_path`` 为空：原样返回（无校正基准）。
        - ``raw_path`` 是 ``state_project_path`` 内的合法绝对路径：直接用。
        - ``raw_path`` 是 ``projects_dir`` 内的其他绝对路径（项目段漂移）：
          剥离 ``projects_dir`` 前缀、可能的日期段、项目段，将"项目内子路径"
          拼到 ``state_project_path``。
        - ``raw_path`` 是 ``projects_dir`` 外的绝对路径：原样返回，由上层沙箱兜底。
        - ``raw_path`` 是相对路径：
            - 首段为已知子目录名（svg_output/notes/...）或包含 ``.``（文件名）：
              视为项目内子路径，直接拼到 ``state_project_path``。
            - 否则：视为漂移的项目名，剥离后拼。

    Returns:
        ``(resolved_path, warning_msg)`` — ``warning_msg`` 不为 None 表示发生纠正，
        调用方应当 ``logger.warning`` 记录方便排障。
    """
    if not raw_path or not state_project_path:
        return raw_path, None

    state_path = Path(state_project_path)
    p = Path(raw_path)

    # case 1: 绝对路径
    if p.is_absolute():
        try:
            rp = p.resolve()
            sp = state_path.resolve()
        except OSError:
            return raw_path, None

        # 已在 state_project_path 内（含路径本身）：合法，原样返回
        try:
            if rp == sp or rp.is_relative_to(sp):
                return str(p), None
        except (ValueError, AttributeError):
            pass

        if projects_dir is None:
            return raw_path, None
        try:
            pd = projects_dir.resolve()
            rel = rp.relative_to(pd)
        except (ValueError, OSError):
            return raw_path, None  # projects_dir 外，原样

        # 剥离 PROJECTS_DIR 前缀，可能含日期段
        rel_parts = list(rel.parts)
        if rel_parts and _DATE_DIR_RE.match(rel_parts[0]):
            rel_parts = rel_parts[1:]  # 去日期段
        # 剥离项目名段（无论是否匹配 state.basename，都跳过）
        if rel_parts:
            rel_parts = rel_parts[1:]

        corrected = str(state_path.joinpath(*rel_parts)) if rel_parts else str(state_path)
        warn = (
            f"Rewrote LLM absolute path {raw_path!r} -> {corrected!r} "
            f"(using session state project_path={state_project_path!r})"
        )
        return corrected, warn

    # case 2: 相对路径
    parts = p.parts
    if not parts:
        return str(state_path), None

    first = parts[0]
    # 启发式：首段是已知子目录或带扩展名的文件 → 视为项目内子路径
    if "." in first or first in _KNOWN_PROJECT_SUBDIRS:
        return str(state_path / p), None

    # 首段视为漂移的项目名，剥离
    rest = parts[1:]
    corrected = str(state_path.joinpath(*rest)) if rest else str(state_path)
    warn = (
        f"Rewrote LLM relative path {raw_path!r} -> {corrected!r}: "
        f"first segment {first!r} treated as drifted project name, "
        f"replaced via session state project_path={state_project_path!r}"
    )
    return corrected, warn


def resolve_projects_relative(rel: str, projects_dir: Path) -> Path:
    """将 PROJECTS_DIR 下的相对路径解析为带日期分层的绝对路径。

    解析顺序：
    1) 直接命中：projects_dir / rel 已存在（兼容旧的无日期分层项目）
    2) 扫描 projects_dir 下所有 YYYYMMDD 子目录，找到同名（首段）项目目录
       → 返回 <date_dir> / rel（保留 rel 内的子路径，如 svg_output/x.svg）
    3) 都没命中：拼今日日期 → projects_dir / today / rel

    这样既兼容跨天复用既有项目，也保证新项目按 YYYYMMDD 分层。
    """
    rel_path = Path(rel)
    direct = projects_dir / rel_path
    if direct.exists():
        return direct

    # 取相对路径首段作为项目名（如 'pingan_xxx_anon'），其余为项目内子路径
    parts = rel_path.parts
    if parts and projects_dir.is_dir():
        project_name = parts[0]
        inner = Path(*parts[1:]) if len(parts) > 1 else None
        # 倒序扫描日期目录（最新优先），找到同名项目即用
        try:
            date_dirs = sorted(
                (d for d in projects_dir.iterdir()
                 if d.is_dir() and _DATE_DIR_RE.match(d.name)),
                key=lambda d: d.name,
                reverse=True,
            )
        except OSError:
            date_dirs = []
        for date_dir in date_dirs:
            cand = date_dir / project_name
            if cand.exists():
                return (cand / inner) if inner else cand

    # 兜底：今日日期目录
    today = datetime.now().strftime("%Y%m%d")
    return projects_dir / today / rel_path


def assert_within_allowed_roots(target: Path, allowed_roots: list[Path | None]) -> Path:
    """校验 target 解析后位于任一 allowed_root 之内，防止路径穿越。

    - target：待校验路径（可以是相对路径，会被 resolve）
    - allowed_roots：允许的根目录列表，None 项被忽略
    - 所有 root 均为 None（即未配置沙箱）时直接放行，保持兼容
    - 校验通过返回 resolve 后的绝对 Path；失败抛 PermissionError
    """
    resolved = target.resolve()
    roots = [r.resolve() for r in allowed_roots if r is not None]
    if not roots:
        return resolved
    for root in roots:
        try:
            if resolved.is_relative_to(root):
                return resolved
        except (ValueError, OSError):
            continue
    raise PermissionError(
        f"路径不在允许的根目录内: {resolved} (allowed_roots={[str(r) for r in roots]})"
    )


class PptMasterBaseTool(AgentTool):
    """PPT Master 工具基类

    提供 run_script() 方法，通过 subprocess 调用 pptmaster scripts/ 下的 Python 脚本。
    子类只需实现 name、description、parameters 和 execute()。
    """

    name: str = "_pptmaster_base"
    description: str = "PPT Master base tool (abstract)"

    @classmethod
    def _project_path_from(
        cls,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> str:
        """解析项目路径：state 优先，args 兜底。

        ``ppt_project_init`` 会把创建成功的完整绝对路径（含
        ``YYYYMMDD/<proj>_<sid>_<uid>``）写入
        ``session.state.pptmaster_state.project_path``，后续工具应以此为权威。

        - state 有：返回 state 中的 project_path（LLM 跨轮拼路径易丢日期段/
          sid_uid 后缀）。若 args 同时传了 ``project_path`` 但与 state 不一致，
          通过 ``coerce_path_under_state_project`` 打 warn 便于排障。
        - state 没有：回退使用 args 中的 ``project_path``，必要时通过
          ``_abs_project_path`` 解析相对路径（兼容跨天复用 / state 未恢复场景）。
        - 两者都没有：返回空，调用方报 ``project_path is required``。
        """
        ctx = context or {}
        pptmaster = ctx.get("pptmaster_state", {})
        state_pp = ""
        if isinstance(pptmaster, dict):
            state_pp = (pptmaster.get("project_path", "") or "").strip()

        raw_pp = ""
        if isinstance(args, dict):
            raw_pp = (args.get("project_path") or "").strip()

        if state_pp:
            if raw_pp:
                _, warn = coerce_path_under_state_project(
                    raw_pp, state_pp, PROJECTS_DIR,
                )
                if warn:
                    logger.warning("[PptMaster] %s", warn)
            return state_pp

        if raw_pp:
            return cls._abs_project_path(raw_pp)

        return ""

    @staticmethod
    def _abs_project_path(project_path: str) -> str:
        """将 project_path 转为绝对路径。

        - 已是绝对路径：直接返回
        - 相对路径：基于 PPT_MASTER_PROJECTS_DIR 解析，按 YYYYMMDD 日期分层
          1) 先扫描已有日期目录下的同名项目（兼容跨天复用）
          2) 找不到则回落到今日日期目录（保证新项目按规范分层）
        """
        p = Path(project_path)
        if p.is_absolute():
            return str(p)
        if PROJECTS_DIR:
            return str(resolve_projects_relative(project_path, PROJECTS_DIR))
        return str(p)

    def _run_script(
        self,
        script_rel: str,
        args: list[str],
        cwd: str | Path | None = None,
        timeout: int = 300,
    ) -> tuple[int, str, str]:
        """执行 pptmaster 脚本

        Args:
            script_rel: 相对于 SCRIPTS_DIR 的脚本路径，如 "project_manager.py"
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

        # 抑制 grpc (pymilvus 底层) 在 subprocess fork 时的 INFO 日志噪音：
        # - GRPC_VERBOSITY=ERROR: 只打 ERROR 级别
        # - GRPC_ENABLE_FORK_SUPPORT=0: 禁用 fork hook（child 不用 grpc，安全）
        child_env = {
            **os.environ,
            "GRPC_VERBOSITY": "ERROR",
            "GRPC_ENABLE_FORK_SUPPORT": "0",
        }

        logger.info("[PptMaster] Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=child_env,
            )
            # 兑底过滤 grpc fork 日志（防止环境变量未生效时污染 stderr）
            clean_stderr = _filter_grpc_fork_noise(result.stderr)
            if result.returncode != 0:
                logger.error(
                    "[PptMaster] Script failed (exit %d)\n--- stdout ---\n%s\n--- stderr ---\n%s",
                    result.returncode, result.stdout, clean_stderr,
                )
                print(f"[PptMaster] Script failed (exit {result.returncode})", flush=True)
                print(f"[PptMaster] CMD: {' '.join(cmd)}", flush=True)
                print(f"[PptMaster] CWD: {work_dir}", flush=True)
                print(f"[PptMaster] stdout: {result.stdout}", flush=True)
                print(f"[PptMaster] stderr: {clean_stderr}", flush=True)
            else:
                logger.debug("[PptMaster] stdout: %s", result.stdout)
            return result.returncode, result.stdout, clean_stderr
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

        # title：从 stdout 提取关键信息作为操作对象
        title = tool_name
        if stdout.strip():
            first_line = stdout.strip().split("\n")[0][:60]
            if first_line:
                title = first_line

        sections: list[ViewSection] = []
        if stdout.strip():
            sections.append(
                ViewSection(heading="输出", content_type="text", data=stdout.strip()[:500])
            )
        if stderr.strip() and is_error:
            sections.append(
                ViewSection(heading="错误信息", content_type="text", data=stderr.strip()[:300])
            )
        if not sections:
            sections.append(
                ViewSection(heading="状态", content_type="status", data={"status": status, "message": summary})
            )

        return FrontendDigest(
            tool_name=tool_name,
            display_type=ToolDisplayType.FILE,
            minimal=MinimalView(
                title=title,
                summary=summary,
                icon="pptmaster",
                status=status,
            ),
            detailed=DetailedView(title=tool_name, sections=sections),
        )
