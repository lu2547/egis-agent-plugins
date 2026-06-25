"""bootstrap.agent — Agent 级 Builder 集合"""

from .skill_builder import build_skill_config
from .agent_builder import EgisBaseAgent

__all__ = [
    "build_skill_config",
    "EgisBaseAgent",
]
