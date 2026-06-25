"""Skills 配置工厂

从环境变量构建 SkillConfig，统一管理 skill 加载策略。
业务层（agent.py）只需调用 build_skill_config()，不感知任何环境变量细节。

环境变量：
    SKILL_LOAD_MODE         加载模式
                              full    全量注入 System Prompt
                              dynamic 仅注入元数据，LLM 通过 read_skill 工具按需加载全文（默认）
    CORE_SKILLS_DIR         core/skills 目录全路径（默认基于 __file__ 推导）

加载模式说明：
    full    - 每次请求都将所有 skill 全文写入 System Prompt
              优点：LLM 直接可用，无需额外工具调用
              缺点：token 消耗大（技能文档较长时尤为明显）
    dynamic - 只将 skill 名称 + description 写入 System Prompt
              LLM 需主动调用 read_skill 工具才能获取 skill 全文
              优点：大幅节省 token，适合挂载 skills 较多的场景
              缺点：多一次工具调用延迟

示例 .env：
    SKILL_LOAD_MODE=dynamic
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ark_agentic.core.skills.base import SkillConfig
from ark_agentic.core.types import SkillLoadMode

logger = logging.getLogger(__name__)

# core/skills 默认路径：优先读 CORE_SKILLS_DIR 环境变量，否则基于本文件位置推导
# hatchling packages 配置已包含所有子目录文件（含 .md/.svg），whl 和 editable 路径一致
_CORE_SKILLS_DIR_ENV = "CORE_SKILLS_DIR"
_DEFAULT_CORE_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


def build_skill_config(
    agent_id: str,
    *,
    agent_skills_dir: Path | None = None,
    core_skills_dir: Path | None = None,
) -> SkillConfig:
    """从环境变量构建 SkillConfig。

    Args:
        agent_id:         Agent 标识符，用于构建全局唯一 skill id（格式: agent_id.skill_name）
        agent_skills_dir: Agent 私有 skills 目录（None 时不挂载）
        core_skills_dir:  Core 公共 skills 目录（None 时用默认推导路径）

    Returns:
        已配置好 skill_directories / load_mode 的 SkillConfig
    """
    # ── 解析 load_mode ──
    load_mode_raw = os.getenv("SKILL_LOAD_MODE", "dynamic").strip().lower()
    load_mode = SkillLoadMode.dynamic if load_mode_raw == "dynamic" else SkillLoadMode.full

    # ── 构建目录列表 ──
    _core_env = os.getenv(_CORE_SKILLS_DIR_ENV, "").strip()
    effective_core_dir = core_skills_dir or (Path(_core_env) if _core_env else _DEFAULT_CORE_SKILLS_DIR)
    skill_dirs: list[str] = []

    if agent_skills_dir and agent_skills_dir.exists():
        skill_dirs.append(str(agent_skills_dir))

    if effective_core_dir.exists():
        skill_dirs.append(str(effective_core_dir))
    else:
        logger.warning(f"[SkillConfig] core/skills dir not found: {effective_core_dir}")

    logger.info(
        f"[SkillConfig] agent={agent_id} mode={load_mode.value} dirs={skill_dirs}"
    )

    return SkillConfig(
        skill_directories=skill_dirs,
        agent_id=agent_id,
        load_mode=load_mode,
    )
