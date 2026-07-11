"""RAG Workflow 数据结构

定义 ``RagRetrievalWorkflow`` 状态机使用的 instance data schema、
candidate 统一 schema、以及 reference / evidence 类型。

──── 分数字段命名约定（全局权威定义）────

* ``document_score``：summary/metadata 两路 hybrid 排名经文档级 RRF 后的文档选择分。
* ``summary_score``：summary 路的归一化文档召回分，进入综合评分的 10% 项。
* ``raw_recall_score`` / ``recall_score``：chunk hybrid recall 原始分/批内归一化分。
* ``raw_rerank_score`` / ``rerank_score``：chunk rerank 原始分/批内归一化分。
* ``composite_score``：``0.6*rerank + 0.3*recall + 0.1*summary``，再乘 position prior。
* ``score``：当前阶段排序主分；rank 完成后等于 ``composite_score``。
* ``score_trace``：保存上述全部原始值、归一化值、权重与 position prior。

上下文扩块只替换 MMR 已选 chunk 的 content，不改变任何分数或顺序。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


try:
    DEFAULT_QUALITY_MAX_ROUNDS = max(1, int(os.getenv("RAG_QUALITY_MAX_ROUNDS", "5")))
except ValueError:
    DEFAULT_QUALITY_MAX_ROUNDS = 5
DEFAULT_QUALITY_MAX_RETRIES = DEFAULT_QUALITY_MAX_ROUNDS - 1


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
    recall_score: float = 0.0
    document_score: float = 0.0
    summary_score: float = 0.0
    document_match_scores: dict[str, Any] = field(default_factory=dict)

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
            "recall_score": self.recall_score or self.score,
            "document_score": self.document_score,
            "summary_score": self.summary_score,
            "document_match_scores": self.document_match_scores,
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
            recall_score=sr.score,
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
    bm25_query: str = ""
    doc_query: str = ""
    analysis_query: str = ""
    doc_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "keywords": self.keywords,
            "sub_queries": self.sub_queries,
            "rewrite_query": self.rewrite_query,
            "bm25_query": self.bm25_query,
            "doc_query": self.doc_query,
            "analysis_query": self.analysis_query,
            "doc_queries": self.doc_queries,
        }


def new_instance_data(
    query: str,
    source: str = "auto",
    filters: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_QUALITY_MAX_RETRIES,
    enable_evaluation: bool = True,
) -> dict[str, Any]:
    """创建空白 instance_data（``start`` transition 的 effect 调用）。"""
    return {
        "query": query,
        "source": source,
        "filters": filters or {},
        "max_retries": max_retries,
        "enable_evaluation": enable_evaluation,
        "rewrite": None,              # RewriteResult.to_dict() | None
        "retrieval_context": None,    # 首轮 rewrite 固化的不可变检索前提
        "route": None,                # "rag" | "web" | "no_retrieval" | "web_unavailable"
        "selected_knowledge_ids": [],
        "candidates": [],             # list[Candidate.to_dict()]
        "ranked": [],                 # list[Candidate.to_dict()]
        "evidence": [],               # list[dict] — read 阶段的深读结果
        "evidence_sufficient": False,
        "quality_evaluation": None,
        "quality_history": [],
        "gap_ledger": {
            "resolved": [],
            "unresolved": [],
            "next_queries": [],
            "attempted_queries": [],
            "retry_stalled": False,
            "requires_document_reselection": False,
        },
        "references": [],             # list[Reference.to_dict()]
        "attempt": 0,
        "timings": {},
    }
