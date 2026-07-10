"""RAG Workflow 数据结构

定义 ``RagRetrievalWorkflow`` 状态机使用的 instance data schema、
candidate 统一 schema、以及 reference / evidence 类型。

──── 分数字段命名约定（全局权威定义）────

一条 document / candidate / evidence 上可能同时存在以下分数，语义必须一致：

* ``score``          — **当前阶段的主排序分**。在不同阶段含义不同：
                       - recall/RRF 输出后：rrf 归一化后的初始分；
                       - select 完成后：filename×w + summary×w 的融合分；
                       → 下游 shortlist / MMR / 排序均遵循“当前 score”。
* ``recall_score``   — select 阶段写入的“初始分快照”（select 覆盖 score 前的值），只写不改。
* ``document_score`` — workflow.py 将 select 输出结果继承到 candidate 时，快照“文档维度的融合分”。
* ``anchor_score``   — read 阶段：chunk anchor 定位打分（靠 rerank_score 驱动）。
* ``rerank_score``   — rank 阶段：chunk 级 rerank 分。
* ``document_match_scores`` — **文档型分数细分项定位事实源**。下游优先从这里取。
    * ``.filename``            — LLM 文件名估分
    * ``.summary``             — rerank 模型对文档摘要的打分
    * ``.final``               — filename×w + summary×w 的融合分（与写入 select 后的 ``score`` 一致）
    * ``.recall``              — filename×w + summary×w 之前的 rrf 初始分（与 ``recall_score`` 一致）
    * ``.constraint_*``        — 文件名硬约束判定结果
“先写入、后读取”：rank / read / 引用等阶段只写自己那一条名字字段，不往已有名字字段上“重写以制造新含义”。
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
    doc_query: str = ""
    analysis_query: str = ""
    doc_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "keywords": self.keywords,
            "sub_queries": self.sub_queries,
            "rewrite_query": self.rewrite_query,
            "doc_query": self.doc_query,
            "analysis_query": self.analysis_query,
            "doc_queries": self.doc_queries,
        }


def new_instance_data(
    query: str,
    source: str = "auto",
    filters: dict[str, Any] | None = None,
    hints: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_QUALITY_MAX_RETRIES,
) -> dict[str, Any]:
    """创建空白 instance_data（``start`` transition 的 effect 调用）。"""
    return {
        "query": query,
        "source": source,
        "filters": filters or {},
        "hints": hints or {},
        "max_retries": max_retries,
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
