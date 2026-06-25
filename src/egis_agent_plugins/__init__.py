"""egis-agent-plugins — 对 ark-agentic 0.7.6 的封装与扩展库

本包是被其他项目（如 egis-gpt-agents）import 使用的库，不再是独立可执行服务。

顶层 API：

- ``app_builder``：框架标准生命周期 API（create / start / stop / serve）
- ``EgisBaseAgent``：业务 Agent 基类（封装 skill 加载 + env-driven compaction）

子模块：

- ``core.plugins``：ark 标准 Plugin（EgisStudioPlugin / EgisDownloadPlugin）
- ``core.bootstrap.app``：App 级 Builder（log_builder / env_builder / app_builder）
- ``core.bootstrap.agent``：Agent 级 Builder（skill_builder / agent_builder）
- ``core.internal``：跨能力复用的内部底座（a2ui / emit / ...）
- ``core.router``：HTTP 路由（download）
- ``core.service``：跨域基础设施（base / download，待迁移至 internal）
- ``core.flows``：Workflow FSM 编排层（对齐 ark 0.7.6 Workflow）
- ``core.tools``：AgentTool 实现集合（rag / react / delivery / pptmaster / ...）；
  各能力域下的 ``_services/`` 子目录承载内部策略层（如
  ``tools.react._services`` = ReAct 运行时策略）。
- ``core.skills``：Skill.md 通用模板（rag / react / ...）
"""

from .core.bootstrap.app import app_builder  # noqa: F401
from .core.bootstrap.agent import EgisBaseAgent  # noqa: F401

__version__ = "0.1.0"
__all__ = ["app_builder", "EgisBaseAgent"]
