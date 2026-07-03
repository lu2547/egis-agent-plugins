"""RAG Workflow 数据结构

定义 ``RagRetrievalWorkflow`` 状态机使用的 instance data schema、
candidate 统一 schema、以及 reference / evidence 类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    """统一候选 schema — 所有召回路径（internal / web）输出同构结构。

    后续 rank / read / reference 各阶段共用此 schema，无需按来源分支。
    """

    id: str
    content: str
    chunk_id: str
    knowledge_id: str
    knowledge_base_id: str
    score: float
    knowledge_title: str = ""
    chunk_index: int = 0
    source: str = "internal"          # "internal" | "web"
    source_query: str = ""
    query_type: str = "hybrid"        # "hybrid" | "vector" | "keyword" | "web"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "chunk_id": self.chunk_id,
            "knowledge_id": self.knowledge_id,
            "knowledge_base_id": self.knowledge_base_id,
            "score": self.score,
            "knowledge_title": self.knowledge_title,
            "chunk_index": self.chunk_index,
            "source": self.source,
            "source_query": self.source_query,
            "query_type": self.query_type,
        }

    @classmethod
    def from_search_result(cls, sr: Any, *, source: str = "internal") -> "Candidate":
        """从 recall stage 的 SearchResult 转换。"""
        return cls(
            id=sr.id,
            content=sr.content,
            chunk_id=sr.chunk_id,
            knowledge_id=sr.knowledge_id,
            knowledge_base_id=sr.knowledge_base_id,
            score=sr.score,
            knowledge_title=getattr(sr, "knowledge_title", ""),
            chunk_index=getattr(sr, "chunk_index", 0),
            source=source,
            source_query=getattr(sr, "source_query", ""),
            query_type=getattr(sr, "query_type", "hybrid"),
        )


@dataclass
class Reference:
    """引用条目 — 在 answer 生成前 emit 给前端。"""

    chunk_id: str
    doc_title: str
    knowledge_id: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_title": self.doc_title,
            "knowledge_id": self.knowledge_id,
            "score": round(self.score, 4),
        }


@dataclass
class RewriteResult:
    """Query Rewrite 输出的结构化快照。"""

    intent: str = "rag"
    keywords: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)
    rewrite_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "keywords": self.keywords,
            "sub_queries": self.sub_queries,
            "rewrite_query": self.rewrite_query,
        }


def new_instance_data(
    query: str,
    source: str = "auto",
    filters: dict[str, Any] | None = None,
    hints: dict[str, Any] | None = None,
    max_retries: int = 1,
) -> dict[str, Any]:
    """创建空白 instance_data（``start`` transition 的 effect 调用）。"""
    return {
        "query": query,
        "source": source,
        "filters": filters or {},
        "hints": hints or {},
        "max_retries": max_retries,
        "rewrite": None,              # RewriteResult.to_dict() | None
        "route": None,                # "rag" | "web" | "no_retrieval" | "web_unavailable"
        "selected_knowledge_ids": [],
        "candidates": [],             # list[Candidate.to_dict()]
        "ranked": [],                 # list[Candidate.to_dict()]
        "evidence": [],               # list[dict] — read 阶段的深读结果
        "evidence_sufficient": False,
        "references": [],             # list[Reference.to_dict()]
        "attempt": 0,
        "timings": {},
    }
