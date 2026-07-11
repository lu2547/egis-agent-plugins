"""Lightweight, deterministic query preparation for hybrid retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

IntentType = Literal["rag", "web_search", "direct"]


@dataclass
class RewriteResult:
    """One semantic query plus a whitespace-tokenized BM25 representation."""

    rewrite_query: str
    bm25_query: str = ""
    resolved_query: str = ""
    sub_queries: list[str] = field(default_factory=list)
    intent: IntentType = "rag"
    keywords: list[str] = field(default_factory=list)
    doc_query: str = ""
    analysis_query: str = ""
    doc_queries: list[str] = field(default_factory=list)
    continues_previous_rag: bool = False
    reuse_previous_documents: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rewrite_query": self.rewrite_query,
            "bm25_query": self.bm25_query or self.rewrite_query,
            "resolved_query": self.resolved_query or self.rewrite_query,
            "sub_queries": self.sub_queries or [self.rewrite_query],
            "intent": self.intent,
            "keywords": self.keywords,
            "doc_query": self.doc_query or self.rewrite_query,
            "analysis_query": self.analysis_query or self.rewrite_query,
            "doc_queries": self.doc_queries or [self.rewrite_query],
            "continues_previous_rag": self.continues_previous_rag,
            "reuse_previous_documents": self.reuse_previous_documents,
        }


class QueryRewriteService:
    """Prepare an atomic query without an LLM, expansion, or decomposition."""

    def __init__(self, llm: Any | None = None, **_: Any) -> None:  # compatibility with old construction
        self._llm = llm

    async def rewrite(self, query: str, **_: Any) -> RewriteResult:
        normalized = " ".join(str(query or "").split())
        tokens = self._tokenize(normalized)
        return RewriteResult(
            rewrite_query=normalized,
            bm25_query=" ".join(tokens) or normalized,
            resolved_query=normalized,
            sub_queries=[normalized],
            intent="rag",
            keywords=tokens[:12],
            doc_query=normalized,
            analysis_query=normalized,
            doc_queries=[normalized],
        )

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        """Use jieba only as a tokenizer; never add or rewrite terms."""
        try:
            import jieba

            raw = jieba.lcut(query, cut_all=False, HMM=False)
        except ImportError:
            raw = []
            for segment in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", query):
                if re.fullmatch(r"[\u4e00-\u9fff]+", segment) and len(segment) > 2:
                    raw.extend(segment[index:index + 2] for index in range(len(segment) - 1))
                else:
                    raw.append(segment)
        tokens: list[str] = []
        for item in raw:
            token = str(item or "").strip()
            if token and re.search(r"[A-Za-z0-9\u4e00-\u9fff]", token):
                tokens.append(token)
        return tokens

    @staticmethod
    def _extract_simple_keywords(query: str) -> list[str]:
        return QueryRewriteService._tokenize(query)[:12]
