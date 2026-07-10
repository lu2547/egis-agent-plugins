"""RAG Workflow 单测 — 覆盖 guards / workflow paths / progress / types。"""

from __future__ import annotations

import os
from typing import Any
import json

import pytest

from ark_agentic.core.workflow.errors import WorkflowRejection
from ark_agentic.core.workflow.protocol import InstanceCtx


# ────────────────────────────────────────────────────────────
# Helper: build InstanceCtx
# ────────────────────────────────────────────────────────────


def _make_ictx(
    *,
    instance_id: str = "test",
    current_state: str | None = None,
    instance_data: dict[str, Any] | None = None,
    args: dict[str, Any] | None = None,
    session_ctx: dict[str, Any] | None = None,
    probing: bool = False,
) -> InstanceCtx:
    return InstanceCtx(
        instance_id=instance_id,
        current_state=current_state,
        instance_data=instance_data or {},
        args=args or {},
        session_ctx=session_ctx or {},
        probing=probing,
    )


# ────────────────────────────────────────────────────────────
# 1. Guards
# ────────────────────────────────────────────────────────────


class TestChitchatGuard:
    def test_chitchat_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _is_obvious_no_retrieval
        ictx = _make_ictx(args={"query": "你好"})
        _is_obvious_no_retrieval(ictx)  # should not raise

    def test_hello_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _is_obvious_no_retrieval
        ictx = _make_ictx(args={"query": "hello"})
        _is_obvious_no_retrieval(ictx)

    def test_complex_query_rejects(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _is_obvious_no_retrieval
        ictx = _make_ictx(args={"query": "什么是 RAG 的核心原理"})
        with pytest.raises(WorkflowRejection):
            _is_obvious_no_retrieval(ictx)

    def test_empty_query_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _is_obvious_no_retrieval
        ictx = _make_ictx(args={"query": ""})
        _is_obvious_no_retrieval(ictx)  # empty = no opinion

    def test_probing_always_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _is_obvious_no_retrieval
        ictx = _make_ictx(args={"query": "complex query"}, probing=True)
        _is_obvious_no_retrieval(ictx)


class TestDocumentQueryPlan:
    def test_rewrite_preserves_range_and_all_year_queries(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rewrite.service import QueryRewriteService

        # _parse_response only needs the configured sub-query limit; no model call.
        service = object.__new__(QueryRewriteService)
        service._max_sub_queries = 4
        result = service._parse_response(json.dumps({
            "rewrite_query": "企业年金近五年数据报告",
            "doc_query": "企业年金数据报告",
            "analysis_query": "企业年金近五年数据趋势",
            "sub_queries": ["企业年金数据报告"],
            "intent": "rag",
            "doc_queries": [
                "企业年金最近五个完整自然年数据报告",
                "企业年金2025年数据报告",
                "企业年金2024年数据报告",
                "企业年金2023年数据报告",
                "企业年金2022年数据报告",
                "企业年金2021年数据报告",
            ],
        }), "企业年金最近五年数据报告")

        assert len(result.doc_queries) == 6
        assert result.doc_queries[0] == "企业年金最近五个完整自然年数据报告"
        assert result.doc_queries[-1] == "企业年金2021年数据报告"

    def test_object_doc_queries_are_not_supported(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rewrite.service import QueryRewriteService

        service = object.__new__(QueryRewriteService)
        service._max_sub_queries = 4
        result = service._parse_response(json.dumps({
            "rewrite_query": "测试报告",
            "doc_query": "测试报告",
            "analysis_query": "分析测试报告",
            "intent": "rag",
            "doc_queries": [{"query": "旧对象格式"}],
        }), "测试报告")

        assert result.doc_queries == ["测试报告"]


class TestDocumentSelectRRF:
    def test_summary_and_metadata_results_are_fused_by_knowledge_id(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _rrf_merge_documents

        result = _rrf_merge_documents(
            [
                ("summary", [
                    {"knowledge_id": "both", "score": 0.9},
                    {"knowledge_id": "summary-only", "score": 0.8},
                ]),
                ("metadata", [
                    {"knowledge_id": "metadata-only", "score": 0.9},
                    {"knowledge_id": "both", "score": 0.8},
                ]),
            ],
            rrf_k=60,
            query="指定条件的报告",
        )

        assert result[0]["knowledge_id"] == "both"
        assert set(result[0]["initial_recall_components"]["routes"]) == {"summary", "metadata"}

    def test_independent_query_scores_are_not_added(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _union_query_candidates

        selected, coverage = _union_query_candidates([
            ("产品A报告", [
                {"knowledge_id": "shared", "score": 0.6},
                {"knowledge_id": "a-only", "score": 0.9},
            ]),
            ("产品B报告", [
                {"knowledge_id": "shared", "score": 0.7},
                {"knowledge_id": "b-only", "score": 0.8},
            ]),
        ])

        by_id = {item["knowledge_id"]: item for item in selected}
        assert by_id["shared"]["score"] == 0.7
        assert coverage["产品A报告"] == ["shared", "a-only"]
        assert coverage["产品B报告"] == ["shared", "b-only"]


class TestEvidenceBudget:
    def test_budget_is_shared_across_documents_and_prefers_anchors(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import _select_evidence_by_document

        evidence = [
            {"knowledge_id": "doc-a", "chunk_id": "a-0", "chunk_index": 0, "score": 0.1},
            {"knowledge_id": "doc-a", "chunk_id": "a-1", "chunk_index": 1, "score": 0.9, "is_anchor": True},
            {"knowledge_id": "doc-a", "chunk_id": "a-2", "chunk_index": 2, "score": 0.2},
            {"knowledge_id": "doc-b", "chunk_id": "b-0", "chunk_index": 0, "score": 0.1},
            {"knowledge_id": "doc-b", "chunk_id": "b-1", "chunk_index": 1, "score": 0.8, "is_anchor": True},
            {"knowledge_id": "doc-b", "chunk_id": "b-2", "chunk_index": 2, "score": 0.2},
        ]

        selected = _select_evidence_by_document(evidence, max_items=4)

        assert [item["chunk_id"] for item in selected[:2]] == ["a-1", "b-1"]
        assert {item["knowledge_id"] for item in selected} == {"doc-a", "doc-b"}


class TestRouteGuards:
    def test_web_disabled_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _web_requested_but_disabled
        os.environ.pop("WEB_SEARCH_PROVIDER", None)
        ictx = _make_ictx(instance_data={
            "rewrite": {"intent": "web_search"},
            "source": "auto",
        })
        _web_requested_but_disabled(ictx)

    def test_web_disabled_rejects_when_available(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _web_requested_but_disabled
        os.environ["WEB_SEARCH_PROVIDER"] = "serpapi"
        try:
            ictx = _make_ictx(instance_data={
                "rewrite": {"intent": "web_search"},
                "source": "auto",
            })
            with pytest.raises(WorkflowRejection):
                _web_requested_but_disabled(ictx)
        finally:
            os.environ.pop("WEB_SEARCH_PROVIDER", None)

    def test_web_disabled_rejects_when_not_web_intent(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _web_requested_but_disabled
        ictx = _make_ictx(instance_data={
            "rewrite": {"intent": "kb_search"},
            "source": "auto",
        })
        with pytest.raises(WorkflowRejection):
            _web_requested_but_disabled(ictx)

    def test_route_internal_always_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _route_internal
        ictx = _make_ictx()
        _route_internal(ictx)  # should never raise


class TestRecallGuards:
    def test_has_selected_docs_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _has_selected_docs
        ictx = _make_ictx(instance_data={"selected_knowledge_ids": ["kid1"]})
        _has_selected_docs(ictx)

    def test_has_selected_docs_rejects_empty(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _has_selected_docs
        ictx = _make_ictx(instance_data={"selected_knowledge_ids": []})
        with pytest.raises(WorkflowRejection):
            _has_selected_docs(ictx)

    def test_no_selected_docs_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _no_selected_docs
        ictx = _make_ictx(instance_data={"selected_knowledge_ids": []})
        _no_selected_docs(ictx)

    def test_no_selected_docs_rejects_nonempty(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _no_selected_docs
        ictx = _make_ictx(instance_data={"selected_knowledge_ids": ["kid1"]})
        with pytest.raises(WorkflowRejection):
            _no_selected_docs(ictx)


class TestRankGuards:
    def test_has_candidates_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _has_candidates
        ictx = _make_ictx(instance_data={"candidates": [{"id": "1"}]})
        _has_candidates(ictx)

    def test_has_candidates_rejects_empty(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _has_candidates
        ictx = _make_ictx(instance_data={"candidates": []})
        with pytest.raises(WorkflowRejection):
            _has_candidates(ictx)

    def test_no_candidates_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _no_candidates
        ictx = _make_ictx(instance_data={"candidates": []})
        _no_candidates(ictx)


class TestDecideGuards:
    def test_evidence_sufficient_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _evidence_sufficient
        ictx = _make_ictx(instance_data={"evidence_sufficient": True})
        _evidence_sufficient(ictx)

    def test_evidence_sufficient_rejects_false(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _evidence_sufficient
        ictx = _make_ictx(instance_data={"evidence_sufficient": False})
        with pytest.raises(WorkflowRejection):
            _evidence_sufficient(ictx)

    def test_evidence_insufficient_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _evidence_insufficient
        ictx = _make_ictx(instance_data={"evidence_sufficient": False})
        _evidence_insufficient(ictx)

    def test_evidence_insufficient_rejects_true(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _evidence_insufficient
        ictx = _make_ictx(instance_data={"evidence_sufficient": True})
        with pytest.raises(WorkflowRejection):
            _evidence_insufficient(ictx)


class TestRetryGuards:
    def test_can_retry_passes(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _can_retry
        ictx = _make_ictx(instance_data={"attempt": 0, "max_retries": 1})
        _can_retry(ictx)

    def test_can_retry_rejects_exhausted(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _can_retry
        ictx = _make_ictx(instance_data={"attempt": 1, "max_retries": 1})
        with pytest.raises(WorkflowRejection):
            _can_retry(ictx)

    def test_retry_exhausted_has_partial(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _retry_exhausted_has_partial
        ictx = _make_ictx(instance_data={
            "attempt": 1, "max_retries": 1, "ranked": [{"id": "1"}],
        })
        _retry_exhausted_has_partial(ictx)

    def test_retry_exhausted_no_evidence(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _retry_exhausted_no_evidence
        ictx = _make_ictx(instance_data={
            "attempt": 1, "max_retries": 1, "ranked": [],
        })
        _retry_exhausted_no_evidence(ictx)


# ────────────────────────────────────────────────────────────
# 2. Workflow paths (no tools — test state transitions)
# ────────────────────────────────────────────────────────────


def _merge_state(session: dict, delta: dict | None) -> None:
    if not delta:
        return
    for k, v in delta.items():
        if isinstance(v, dict) and k in session and isinstance(session[k], dict):
            session[k].update(v)
        else:
            session[k] = v


@pytest.mark.asyncio
class TestWorkflowPaths:

    async def test_no_retrieval_chitchat(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        r = await w.execute("t1", "start", {"query": "你好"}, {})
        assert r.new_state == "no_retrieval"
        assert r.success

    async def test_no_retrieval_thanks(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        r = await w.execute("t1", "start", {"query": "谢谢"}, {})
        assert r.new_state == "no_retrieval"

    async def test_start_to_rewrite_pending(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        r = await w.execute("t2", "start", {"query": "什么是 RAG"}, {})
        assert r.new_state == "rewrite_pending"

    async def test_rewrite_fallback_no_tool(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t3", "start", {"query": "什么是 RAG"}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t3", "rewrite", {}, session)
        assert r.new_state == "rewritten"
        # Check fallback rewrite data
        inst = r.state_delta["_workflows"]["rag"]["instances"]["t3"]
        assert inst["data"]["rewrite"]["intent"] == "kb_search"

    async def test_route_internal_default(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t4", "start", {"query": "test query"}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t4", "rewrite", {}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t4", "route", {}, session)
        assert r.new_state == "docs_selected"

    async def test_full_path_to_no_evidence(self) -> None:
        """start → rewrite → route → recall → rank(no candidates) → insufficient → no_evidence"""
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}

        r = await w.execute("t5", "start", {"query": "test", "max_retries": 0}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t5", "rewrite", {}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t5", "route", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "docs_selected"

        # recall → recalled (allow_full_scope_recall=True when no select_documents tool)
        r = await w.execute("t5", "recall", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "recalled"

        # rank → insufficient (no candidates, no knowledge_search tool)
        r = await w.execute("t5", "rank", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "insufficient"

        r = await w.execute("t5", "retry", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "no_evidence"

    async def test_web_unavailable_path(self) -> None:
        """start → rewrite → route(no_evidence) when web disabled"""
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        os.environ.pop("WEB_SEARCH_PROVIDER", None)
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}

        r = await w.execute("t6", "start", {"query": "news", "source": "web"}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t6", "rewrite", {}, session)
        _merge_state(session, r.state_delta)

        # Manually override intent to web_search
        inst = session["_workflows"]["rag"]["instances"]["t6"]
        inst["data"]["rewrite"]["intent"] = "web_search"

        r = await w.execute("t6", "route", {}, session)
        assert r.new_state == "no_evidence"
        assert "Web 搜索" in (r.message or "")


# ────────────────────────────────────────────────────────────
# 3. Types
# ────────────────────────────────────────────────────────────


class TestTypes:
    def test_candidate_to_dict(self) -> None:
        from egis_agent_plugins.core.flows.rag.schema import Candidate
        c = Candidate(
            id="c1", content="test content", chunk_id="ch1",
            knowledge_id="k1", knowledge_base_id="kb1", score=0.95,
        )
        d = c.to_dict()
        assert d["id"] == "c1"
        assert d["score"] == 0.95
        assert d["source"] == "internal"

    def test_reference_to_dict(self) -> None:
        from egis_agent_plugins.core.flows.rag.schema import Reference
        r = Reference(chunk_id="ch1", doc_title="Test Doc", knowledge_id="k1", score=0.8)
        d = r.to_dict()
        assert d["chunk_id"] == "ch1"
        assert d["score"] == 0.8

    def test_new_instance_data(self) -> None:
        from egis_agent_plugins.core.flows.rag.schema import new_instance_data
        data = new_instance_data("test query", source="web", max_retries=2)
        assert data["query"] == "test query"
        assert data["source"] == "web"
        assert data["max_retries"] == 2
        assert data["attempt"] == 0
        assert data["candidates"] == []


# ────────────────────────────────────────────────────────────
# 4. Document selection
# ────────────────────────────────────────────────────────────


class TestDocumentSelection:
    def test_document_match_strategy_defaults_to_filename_heavy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _document_match_strategy

        monkeypatch.delenv("RAG_DOCUMENT_MATCH_PREFERENCE", raising=False)
        monkeypatch.delenv("RAG_DOCUMENT_SELECT_FILENAME_WEIGHT", raising=False)
        monkeypatch.delenv("RAG_DOCUMENT_SELECT_SUMMARY_WEIGHT", raising=False)

        strategy = _document_match_strategy({})

        assert strategy["document_match_preference"] == "filename"
        assert strategy["weights"] == {"filename": 0.8, "summary": 0.2}

    def test_document_match_strategy_allows_env_weight_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _document_match_strategy

        monkeypatch.setenv("RAG_DOCUMENT_SELECT_FILENAME_WEIGHT", "8")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_SUMMARY_WEIGHT", "2")

        strategy = _document_match_strategy({"document_match_preference": "summary"})

        assert strategy["document_match_preference"] == "summary"
        assert strategy["weights"] == {"filename": 0.8, "summary": 0.2}

    def test_shortlist_uses_mmr_for_diverse_documents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _shortlist_documents

        monkeypatch.setenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", "2")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_MIN_SCORE", "0")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", "0.95")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "mmr")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_MMR_LAMBDA", "0.5")

        docs = [
            {"knowledge_id": "a", "score": 1.0, "file_name": "养老险产品介绍.pdf", "content": "养老险 产品 介绍 账户 权益"},
            {"knowledge_id": "b", "score": 0.99, "file_name": "养老险产品介绍副本.pdf", "content": "养老险 产品 介绍 账户 权益"},
            {"knowledge_id": "c", "score": 0.98, "file_name": "投资报告.pdf", "content": "投资 组合 收益 风险 回撤"},
        ]

        selected, rejected, thresholds = _shortlist_documents(docs)

        assert [doc["knowledge_id"] for doc in selected] == ["a", "c"]
        assert [doc["knowledge_id"] for doc in rejected] == ["b"]
        assert thresholds["diversity_strategy"] == "mmr"
        assert thresholds["eligible_documents"] == 3

    def test_shortlist_can_keep_score_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _shortlist_documents

        monkeypatch.setenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", "2")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_MIN_SCORE", "0")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", "0.95")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "score")

        docs = [
            {"knowledge_id": "a", "score": 1.0, "file_name": "养老险产品介绍.pdf", "content": "养老险 产品 介绍 账户 权益"},
            {"knowledge_id": "b", "score": 0.99, "file_name": "养老险产品介绍副本.pdf", "content": "养老险 产品 介绍 账户 权益"},
            {"knowledge_id": "c", "score": 0.98, "file_name": "投资报告.pdf", "content": "投资 组合 收益 风险 回撤"},
        ]

        selected, rejected, thresholds = _shortlist_documents(docs)

        assert [doc["knowledge_id"] for doc in selected] == ["a", "b"]
        assert [doc["knowledge_id"] for doc in rejected] == ["c"]
        assert thresholds["diversity_strategy"] == "score"


# ────────────────────────────────────────────────────────────
# 5. Progress
# ────────────────────────────────────────────────────────────


class TestProgress:
    def test_emit_progress_with_emitter(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        emit_progress(ctx, tool="query_rewrite", status="pending")
        assert len(events) == 1
        assert events[0]["tool"] == "query_rewrite"
        assert events[0]["status"] == "pending"

    def test_emit_progress_without_emitter(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress
        # Should not raise
        emit_progress(None, tool="test", status="done")
        emit_progress({}, tool="test", status="done")

    def test_emit_references(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_references
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        refs = [{"chunk_id": "c1", "doc_title": "Doc"}]
        emit_references(ctx, refs)
        assert len(events) == 1
        assert events[0]["type"] == "references"
        assert events[0]["data"] == refs

    def test_emit_progress_with_count(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        emit_progress(ctx, tool="knowledge_search", status="done", count=12)
        assert events[0]["count"] == 12
