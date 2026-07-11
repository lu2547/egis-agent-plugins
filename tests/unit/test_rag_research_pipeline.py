"""Focused tests for the WeKnora-inspired deterministic RAG path."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from egis_agent_plugins.core.flows.rag._services.document_reader import read_ranked_context
from egis_agent_plugins.core.flows.rag.stages.rank.stage import run as rank_chunks
from egis_agent_plugins.core.flows.rag.stages.select.stage import run as select_documents
from egis_agent_plugins.core.service.base.milvus_client import MilvusSearchResult


class FakeEmbedding:
    async def embed_query(self, query: str) -> list[float]:
        assert query == "泰康 2024 年 资产规模"
        return [0.1, 0.2]


class FakeMilvus:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def ensure_collection_loaded(self, collection: str) -> None:
        assert collection == "summary_knowledge_base"

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["anns_field"] == "embedding":
            return [
                MilvusSearchResult("1", "summary", "1", "both", "kb1", 0.9),
                MilvusSearchResult("2", "summary", "2", "summary-only", "kb1", 0.8),
            ]
        return [
            MilvusSearchResult("3", "meta", "3", "metadata-only", "kb1", 0.9),
            MilvusSearchResult("4", "meta", "4", "both", "kb1", 0.8),
        ]


class FakePostgres:
    async def connect(self) -> None:
        return None

    async def get_knowledges_by_ids(self, ids: list[str]):
        return [SimpleNamespace(id=item, title=f"{item}.pdf", file_name=f"{item}.pdf") for item in ids]


@pytest.mark.asyncio
async def test_document_selection_runs_two_hybrid_routes_and_rrf() -> None:
    milvus = FakeMilvus()
    clients = SimpleNamespace(
        milvus=milvus,
        embedding=FakeEmbedding(),
        postgres=FakePostgres(),
        summary_collection="summary_knowledge_base",
        default_kb_ids=["kb1"],
    )

    result = await select_documents(
        clients=clients,
        args={
            "query": "泰康 2024 年 资产规模",
            "bm25_query": "泰康 2024 年 资产 规模",
            "rag_filter": [{"kb_id": "kb1", "tags": [], "files": []}],
        },
    )

    assert len(milvus.calls) == 2
    assert {call["anns_field"] for call in milvus.calls} == {"embedding", "metadata_embedding"}
    assert all(call["hybrid_ranker"] == "rrf" for call in milvus.calls)
    assert result["documents"][0]["knowledge_id"] == "both"
    scores = result["documents"][0]["document_match_scores"]
    assert scores["summary_recall"] > 0
    assert scores["metadata_recall"] > 0


@pytest.mark.asyncio
async def test_document_shortlist_keeps_annual_report_beyond_marketing_top3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WiderMilvus(FakeMilvus):
        def search(self, **kwargs):
            self.calls.append(kwargs)
            return [
                MilvusSearchResult(str(index), "summary", str(index), f"doc-{index}", "kb1", 1.0 / index)
                for index in range(1, 7)
            ]

    monkeypatch.delenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", raising=False)
    clients = SimpleNamespace(
        milvus=WiderMilvus(),
        embedding=FakeEmbedding(),
        postgres=FakePostgres(),
        summary_collection="summary_knowledge_base",
        default_kb_ids=["kb1"],
    )

    result = await select_documents(
        clients=clients,
        args={
            "query": "泰康 2024 年 资产规模",
            "bm25_query": "泰康 2024 年 资产 规模",
            "rag_filter": [{"kb_id": "kb1", "tags": [], "files": []}],
        },
    )

    assert len(result["documents"]) == 6
    assert result["documents"][-1]["knowledge_id"] == "doc-6"


@pytest.mark.asyncio
async def test_composite_uses_summary_route_not_combined_document_score() -> None:
    clients = SimpleNamespace(postgres=None, rerank=SimpleNamespace(enabled=False))
    result = await rank_chunks(
        clients=clients,
        args={
            "queries": ["泰康 2024 年 资产规模"],
            "top_k": 1,
            "candidates": [{
                "chunk_id": "c1",
                "knowledge_id": "doc-1",
                "content": "资产规模为 100 亿元",
                "score": 0.8,
                "recall_score": 0.8,
                "document_score": 0.9,
                "summary_score": 0.2,
            }],
        },
    )

    item = result["ranked"][0]
    assert item["composite_before_prior"] == pytest.approx(0.92)
    assert item["score_trace"]["summary_recall"] == pytest.approx(0.2)


class FakeChunk:
    def __init__(self, chunk_id: str, index: int, content: str) -> None:
        self.id = chunk_id
        self.chunk_index = index
        self.content = content


class FakeChunkPostgres:
    async def connect(self) -> None:
        return None

    async def get_chunks_around_index(self, knowledge_id: str, center_index: int, radius: int):
        assert knowledge_id == "doc-1"
        assert center_index == 10
        return [
            FakeChunk("prev", 9, "前文" * 80),
            FakeChunk("anchor", 10, "核心数值"),
            FakeChunk("next", 11, "后文" * 80),
        ]


@pytest.mark.asyncio
async def test_expansion_only_changes_content_and_preserves_mmr_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_EXPAND_MIN_CHARS", "100")
    monkeypatch.setenv("RAG_EXPAND_MAX_CHARS", "300")
    clients = SimpleNamespace(postgres=FakeChunkPostgres())
    ranked = [
        {
            "chunk_id": "anchor",
            "chunk_index": 10,
            "knowledge_id": "doc-1",
            "content": "核心数值",
            "composite_score": 0.91,
            "score": 0.91,
            "mmr_rank": 1,
            "source": "internal",
        },
        {
            "chunk_id": "long",
            "chunk_index": 20,
            "knowledge_id": "doc-2",
            "content": "无需扩展" * 80,
            "composite_score": 0.82,
            "score": 0.82,
            "mmr_rank": 2,
            "source": "internal",
        },
    ]

    evidence = await read_ranked_context(clients=clients, ranked=ranked, top_k=2)

    assert [item["chunk_id"] for item in evidence] == ["anchor", "long"]
    assert evidence[0]["score"] == 0.91
    assert evidence[0]["composite_score"] == 0.91
    assert evidence[0]["mmr_rank"] == 1
    assert len(evidence[0]["content"]) >= 100
    assert len(evidence[0]["content"]) <= 300
    assert evidence[0]["expanded_chunk_ids"]
    assert evidence[1]["content"] == ranked[1]["content"]
