"""DocGen Flows — 基于 ark Workflow 引擎的文档制作公共编排

DocgenEntryFlow: 统一入口流程（选方式 + 选模板/格式 + 建项目）

每个 Flow 配套一个 WorkflowTool 适配器（见 tools.py）。

标书等具体业务逻辑由 Agent 层（如 doc_creator_agent）自行定义。
"""

from .entry import DocgenEntryFlow

__all__ = [
    "DocgenEntryFlow",
]
