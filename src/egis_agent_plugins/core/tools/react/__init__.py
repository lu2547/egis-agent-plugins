"""ReAct 推理工具集

包含 ReAct 范式所需的核心工具：任务规划、最终回答。

非工具策略层（callbacks / run_mode / planning_guard）位于本包的
``_services`` 子目录，由 EgisBaseAgent 在 Agent 装配阶段引用，
不会被注册到 tool registry。
"""

from .todo_write import TodoWriteTool
from .final_answer import FinalAnswerTool
from ._services import (
    RunMode,
    build_planning_callbacks,
    build_react_callbacks,
    filter_tool_schemas_for_run_mode,
    get_current_display_mode,
    normalize_run_mode,
    resolve_run_mode,
)

__all__ = [
    "TodoWriteTool",
    "FinalAnswerTool",
    "RunMode",
    "build_planning_callbacks",
    "build_react_callbacks",
    "filter_tool_schemas_for_run_mode",
    "get_current_display_mode",
    "normalize_run_mode",
    "resolve_run_mode",
]
