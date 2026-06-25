"""ReAct 内部策略层 — 回调组装、模式策略、规划护栏。

这些模块不是 AgentTool，不会被注册到 tool registry。
它们是 ReAct 循环的内部机制，由 EgisBaseAgent 在构造时和每轮调用中组装。
"""

from .callbacks import build_react_callbacks
from .planning_guard import build_planning_callbacks
from .run_mode import (
    RunMode,
    filter_tool_schemas_for_run_mode,
    get_current_display_mode,
    normalize_run_mode,
    resolve_run_mode,
)

__all__ = [
    "RunMode",
    "build_planning_callbacks",
    "build_react_callbacks",
    "filter_tool_schemas_for_run_mode",
    "get_current_display_mode",
    "normalize_run_mode",
    "resolve_run_mode",
]
