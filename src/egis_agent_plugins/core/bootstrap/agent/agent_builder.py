"""EgisBaseAgent — egis 业务 Agent 的统一基类

对 ark BaseAgent 的统一扩展：

- **Skill 加载**：ark 默认只扫 ``self.skills_dir``（agent 私有目录），
  egis 额外需要 core/skills 共享目录（CORE_SKILLS_DIR env），因此 override ``_init_skill_subsystem``。
- **ReAct 接线**：egis 统一挂载 flash/pro 可切换的 ReAct callbacks，并在模型调用前
  按 run_mode 裁剪 ReAct 工具 schema。

业务 Agent 只需继承本类，专注实现 build_tools / build_sampling。
其余能力（compaction / session / memory / prompt）全部复用 ark 默认。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ark_agentic.core.runtime.base_agent import BaseAgent
from ark_agentic.core.runtime.callbacks import RunnerCallbacks
from ark_agentic.core.skills.loader import SkillLoader
from ark_agentic.core.skills.matcher import SkillMatcher
from egis_agent_plugins.core.tools.react._services import (
    build_react_callbacks,
    filter_tool_schemas_for_run_mode,
)

from .skill_builder import build_skill_config

logger = logging.getLogger(__name__)


class EgisBaseAgent(BaseAgent):
    """egis 业务 Agent 基类。

    子类只需实现:
    - ``build_tools()``     — 注册 AgentTool 列表
    - ``build_sampling()``  — 自定义 SamplingConfig（可选）
    """

    # ── Skill 加载 ─────────────────────────────────────────────────

    def _init_skill_subsystem(self) -> None:
        # ark 默认只扫 self.skills_dir（agent 私有目录）
        # egis 额外扫描 core/skills（CORE_SKILLS_DIR env）
        agent_dir = self._agent_dir()
        private_skills_dir = agent_dir / "skills"
        # 替代 ark 的 SkillConfig(skill_directories=[...])
        self._skill_config = build_skill_config(  
            self.agent_id,
            agent_skills_dir=private_skills_dir if private_skills_dir.is_dir() else None,
        )
        self.skill_loader: SkillLoader | None = SkillLoader(self._skill_config)
        try:
            self.skill_loader.load_from_directories()
        except Exception as exc:
            logger.warning("Failed to load skills for agent '%s': %s", self.agent_id, exc)
        self.skill_matcher: SkillMatcher | None = SkillMatcher(self.skill_loader)

    # ── ReAct 接线 ──────────────────────────────────────────────────

    def build_callbacks(self) -> RunnerCallbacks | None:
        return build_react_callbacks(agent_id=self.agent_id)

    async def _model_phase(
        self,
        session_id: str,
        ls: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        state: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        tools = filter_tool_schemas_for_run_mode(
            tools,
            state,
            agent_id=self.agent_id,
        )
        return await super()._model_phase(
            session_id,
            ls,
            messages,
            tools,
            state,
            **kwargs,
        )

    # ── 内部辅助 ────────────────────────────────────────────────────

    def _agent_dir(self) -> Path:
        """返回子类模块所在目录，复用 ark 的 ``skills_dir`` 约定。"""
        return self.skills_dir.parent  # ark: agent_dir / "skills"
