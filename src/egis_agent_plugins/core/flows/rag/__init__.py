"""RAG Workflow — 状态机驱动的 RAG 全流程编排。

对 LLM 暴露单一 ``rag`` 工具，内部 auto-drive 完成：
query_rewrite → route → recall → rank → read → decide → (retry)。

主要导出：
- ``RagTool``：AgentTool 子类，一次调用完成全流程
- ``RagRetrievalWorkflow``：Workflow 状态机定义（可独立测试）
"""

from .tool import RagTool
from .workflow import RagRetrievalWorkflow
from .schema import Candidate, Reference, RewriteResult
from .events import emit_progress, emit_references

__all__ = [
    "RagTool",
    "RagRetrievalWorkflow",
    "Candidate",
    "Reference",
    "RewriteResult",
    "emit_progress",
    "emit_references",
]
