"""flows — Workflow FSM 编排层（对齐 ark-agentic 0.7.6）

收纳跨工具的“相对固定流程”，每个子模块对应一个领域 workflow。
当前内置：

- ``rag/``：RAG 检索 workflow（一体化状态机 + auto-drive tool）
  - ``RagTool``：对 LLM 暴露单一工具，内部 auto-drive 全流程
  - ``RagRetrievalWorkflow``：Workflow 状态机（可独立测试）
  - ``_services/`` / ``_steps/``：本 flow 私有的服务与步骤实现，外部不应直接依赖

flows 内部按需调用 ``core.internal.*`` 跨层基座（如 ``RAGConfig``），
自身只负责 FSM 编排（guards / effects / transitions）。
"""

from .rag import RagTool, RagRetrievalWorkflow

__all__ = [
    "RagTool",
    "RagRetrievalWorkflow",
]
