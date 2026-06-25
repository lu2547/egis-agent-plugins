"""RAG Workflow 单测 — 覆盖 guards / workflow paths / progress / types。"""

from __future__ import annotations

import os
from typing import Any

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
# 4. Progress
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
