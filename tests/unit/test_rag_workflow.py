"""RAG Workflow 单测 — 覆盖 guards / workflow paths / progress / types。"""

from __future__ import annotations

import os
from types import SimpleNamespace
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


class TestLightweightRewrite:
    async def test_rewrite_only_tokenizes_atomic_query(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rewrite.service import QueryRewriteService

        result = await QueryRewriteService().rewrite("平安养老险 2024年 资产规模")

        assert result.rewrite_query == "平安养老险 2024年 资产规模"
        assert result.sub_queries == ["平安养老险 2024年 资产规模"]
        assert result.doc_queries == ["平安养老险 2024年 资产规模"]
        assert " " in result.bm25_query
        assert "同义词" not in result.bm25_query


class TestDocumentSelectRRF:
    def test_summary_and_metadata_results_are_fused_by_knowledge_id(self) -> None:
        from egis_agent_plugins.core.flows.rag._services.scope_adapter import RecallScope
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _rrf_merge

        result = _rrf_merge(
            [
                {"knowledge_id": "both", "hybrid_score": 0.9},
                {"knowledge_id": "summary-only", "hybrid_score": 0.8},
            ],
            [
                {"knowledge_id": "metadata-only", "hybrid_score": 0.9},
                {"knowledge_id": "both", "hybrid_score": 0.8},
            ],
            query="指定条件的报告",
            rrf_k=60,
            scope=RecallScope(kb_id="kb-1"),
        )

        assert result[0]["knowledge_id"] == "both"
        assert result[0]["document_match_scores"]["summary_recall"] > 0
        assert result[0]["document_match_scores"]["metadata_recall"] > 0
        assert result[0]["score"] == result[0]["document_score"]


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

    def test_workflow_evidence_pool_is_not_written_to_instance_state(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        workflow = RagRetrievalWorkflow()
        ictx = _make_ictx(instance_id="pool-test", instance_data={"attempt": 0})
        pool = workflow._update_evidence_pool(ictx, [
            {"chunk_id": "c1", "knowledge_id": "doc-a", "content": "证据"},
        ])

        assert [item["chunk_id"] for item in pool] == ["c1"]
        assert [item["chunk_id"] for item in workflow._evidence_pools["pool-test"]] == ["c1"]
        assert "evidence_archive" not in ictx.instance_data

    async def test_evaluation_switch_skips_llm_evaluator_and_marks_first_pass_complete(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import egis_agent_plugins.core.flows.rag.workflow as workflow_module
        from egis_agent_plugins.core.flows.rag.schema import new_instance_data
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        async def fake_read_ranked_context(**_: Any) -> list[dict[str, Any]]:
            return [{
                "chunk_id": "chunk-1", "knowledge_id": "doc-1",
                "knowledge_title": "2025年年度报告", "content": "总资产为100亿元。",
                "score": 0.9,
            }]

        async def evaluator_must_not_run(**_: Any) -> dict[str, Any]:
            raise AssertionError("evaluation switch is off; evaluator must not run")

        monkeypatch.setattr(workflow_module, "read_ranked_context", fake_read_ranked_context)
        monkeypatch.setattr(workflow_module, "_evaluate_run", evaluator_must_not_run)
        data = new_instance_data("公司A 2025年总资产", enable_evaluation=False)
        data["ranked"] = [{"chunk_id": "chunk-1", "knowledge_id": "doc-1"}]
        workflow = RagRetrievalWorkflow(clients=object())

        await workflow._ef_read_and_evaluate(_make_ictx(instance_data=data))

        assert data["evidence_sufficient"] is True
        assert data["quality_evaluation"]["skipped"] is True
        assert data["quality_evaluation"]["retry_queries"] == []


class TestQualityEvaluation:
    def test_rag_state_merges_persisted_context_with_request_filter(self) -> None:
        from egis_agent_plugins.core.flows.rag._services.scope_adapter import read_rag_state

        state = read_rag_state({
            "user:rag_state": {
                "context": {"resolved_query": "previous task"},
                "selected_documents": [{"knowledge_id": "doc-a"}],
            },
            "rag_state": {"rag_filter": [{"kb_id": "kb-a"}]},
        })

        assert state["context"]["resolved_query"] == "previous task"
        assert state["selected_documents"][0]["knowledge_id"] == "doc-a"
        assert state["rag_filter"] == [{"kb_id": "kb-a"}]

    async def test_followup_reuses_documents_from_rag_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import egis_agent_plugins.core.flows.rag.workflow as workflow_module
        from egis_agent_plugins.core.flows.rag.schema import new_instance_data
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        async def fake_rewrite_run(**_: Any) -> dict[str, Any]:
            return {
                "intent": "rag",
                "resolved_query": "基于近三年行业报告增加多维表格对比",
                "rewrite_query": "近三年行业报告",
                "doc_query": "行业报告",
                "doc_queries": ["行业报告"],
                "analysis_query": "近三年多维指标表格对比",
                "sub_queries": ["多维指标表格对比"],
                "keywords": ["表格", "对比"],
                "continues_previous_rag": True,
                "reuse_previous_documents": True,
            }

        monkeypatch.setattr(workflow_module, "_rewrite_run", fake_rewrite_run)
        previous_documents = [
            {"knowledge_id": "report-a", "knowledge_title": "年度报告A"},
            {"knowledge_id": "report-b", "knowledge_title": "年度报告B"},
        ]
        instance_data = new_instance_data("expanded tool query")
        ictx = _make_ictx(
            instance_data=instance_data,
            args={
                "query": "expanded tool query",
                "current_user_input": "多点表格比对",
                "previous_rag_context": {
                    "context": {
                        "resolved_query": "基于近三年行业报告分析趋势",
                        "premise": "近三年行业报告",
                    },
                    "selected_documents": previous_documents,
                },
            },
        )
        workflow = RagRetrievalWorkflow()

        await workflow._ef_rewrite(ictx)

        assert ictx.instance_data["query"] == "基于近三年行业报告增加多维表格对比"
        assert ictx.instance_data["reuse_selected_documents"] is True
        assert ictx.instance_data["selected_knowledge_ids"] == ["report-a", "report-b"]
        assert ictx.instance_data["retrieval_context"]["premise"] == "近三年行业报告"

    def test_persistent_rag_state_contains_compact_documents(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import _build_persistent_rag_state

        ictx = _make_ictx(
            instance_data={
                "query": "merged task",
                "rewrite": {
                    "resolved_query": "merged task",
                    "doc_query": "source report",
                    "doc_queries": ["source report"],
                    "analysis_query": "table comparison",
                },
                "retrieval_context": {"premise": "source report"},
                "selected_documents": [
                    {"knowledge_id": "doc-a", "knowledge_title": "Report A", "content": "large"},
                ],
                "quality_evaluation": {"round": 1, "score": 0.8, "passed": True},
            },
            session_ctx={"user:rag_state": {"rag_filter": [{"kb_id": "kb-a"}]}},
        )

        state = _build_persistent_rag_state(ictx)

        assert state["context"]["resolved_query"] == "merged task"
        assert state["selected_documents"][0]["knowledge_id"] == "doc-a"
        assert "content" not in state["selected_documents"][0]
        assert state["rag_filter"] == [{"kb_id": "kb-a"}]

    def test_parses_missing_points_and_retry_queries(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.evaluate.service import EvidenceQualityService

        result = EvidenceQualityService._parse(json.dumps({
            "passed": False,
            "score": 0.4,
            "reason": "缺少对比数据",
            "missing_points": ["缺少产品B数据"],
            "retry_queries": ["产品B业务数据报告"],
            "requires_document_reselection": False,
        }))

        assert result.passed is False
        assert result.missing_points == ["缺少产品B数据"]
        assert result.retry_queries == ["产品B业务数据报告"]
        assert result.requires_document_reselection is False

    def test_factual_averages_three_dimensions_and_ignores_analysis_support(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.evaluate.service import EvidenceQualityService

        result = EvidenceQualityService._parse(json.dumps({
            "task_type": "factual",
            "dimensions": {
                "task_coverage":     {"score": 90, "reason": "完全命中"},
                "concept_alignment": {"score": 85, "reason": "口径匹配"},
                "analysis_support":  None,
                "source_reliability":{"score": 80, "reason": "来源失相关"},
            },
            "score": 12,  # LLM 输错也不影响，service 会重算
            "passed": False,
            "reason": "factual 场景，三维均均高分",
        }))

        assert result.task_type == "factual"
        assert result.dimensions["analysis_support"] is None
        assert result.score == pytest.approx((90 + 85 + 80) / 3, rel=1e-3)
        assert result.passed is True

    def test_analytical_averages_four_dimensions(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.evaluate.service import EvidenceQualityService

        result = EvidenceQualityService._parse(json.dumps({
            "task_type": "analytical",
            "dimensions": {
                "task_coverage":     {"score": 80, "reason": ""},
                "concept_alignment": {"score": 80, "reason": ""},
                "analysis_support":  {"score": 60, "reason": "缺少跨期"},
                "source_reliability":{"score": 90, "reason": ""},
            },
        }))

        assert result.task_type == "analytical"
        assert result.score == pytest.approx((80 + 80 + 60 + 90) / 4, rel=1e-3)
        assert result.passed is False  # 77.5 < 80

    def test_passed_forced_false_when_llm_says_true_but_score_below_threshold(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.evaluate.service import EvidenceQualityService

        result = EvidenceQualityService._parse(json.dumps({
            "task_type": "factual",
            "dimensions": {
                "task_coverage":     {"score": 70, "reason": ""},
                "concept_alignment": {"score": 70, "reason": ""},
                "analysis_support":  None,
                "source_reliability":{"score": 70, "reason": ""},
            },
            "passed": True,  # LLM 误判，service 应强制为 False
        }))

        assert result.score == pytest.approx(70.0, rel=1e-3)
        assert result.passed is False

    def test_passed_forced_true_when_llm_says_false_but_score_above_threshold(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.evaluate.service import EvidenceQualityService

        result = EvidenceQualityService._parse(json.dumps({
            "task_type": "factual",
            "dimensions": {
                "task_coverage":     {"score": 85, "reason": ""},
                "concept_alignment": {"score": 85, "reason": ""},
                "analysis_support":  None,
                "source_reliability":{"score": 85, "reason": ""},
            },
            "passed": False,  # LLM 保守判为不通过，service 应根据 score 强制为 True
        }))

        assert result.score == pytest.approx(85.0, rel=1e-3)
        assert result.passed is True

    async def test_retry_uses_gap_ledger_feedback(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        workflow = RagRetrievalWorkflow()
        ictx = _make_ictx(instance_data={
            "attempt": 0,
            "query": "比较产品A和产品B",
            "rewrite": {
                "analysis_query": "比较产品A和产品B",
                "doc_queries": ["产品A和产品B报告"],
                "sub_queries": [],
            },
            "quality_evaluation": {
                "passed": False,
                "missing_points": ["缺少产品B数据"],
                "retry_queries": ["产品B业务数据报告"],
            },
            "retrieval_context": {
                "premise": "基于近三年行业报告",
                "doc_queries": ["近三年行业报告"],
            },
            "gap_ledger": {
                "resolved": [],
                "unresolved": ["缺少产品B数据"],
                "next_queries": ["产品B业务数据报告"],
            },
            "evidence": [{"chunk_id": "a1", "knowledge_id": "a", "content": "产品A数据"}],
        })

        await workflow._ef_expand_retry(ictx)

        assert ictx.instance_data["attempt"] == 1
        contextual_query = "基于近三年行业报告 产品B业务数据报告"
        assert ictx.instance_data["rewrite"]["doc_queries"] == [contextual_query]
        assert ictx.instance_data["rewrite"]["sub_queries"] == [contextual_query]
        assert ictx.instance_data["rewrite"]["analysis_query"] == "比较产品A和产品B"
        assert ictx.instance_data["evidence"] == []

    def test_retry_round_caps_gap_queries_at_three(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        workflow = RagRetrievalWorkflow()
        queries = ["slice-a", "slice-b", "slice-c", "slice-d"]
        ictx = _make_ictx(instance_data={
            "attempt": 1,
            "query": "original analysis question",
            "rewrite": {"sub_queries": queries},
        })

        assert workflow._rag_queries(ictx) == queries[:3]

    async def test_atomic_rerank_builds_composite_score_trace(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rank.stage import run

        class FakeRerank:
            enabled = True

            def __init__(self) -> None:
                self.calls: list[str] = []

            async def rerank(self, query: str, passages: list[str]) -> list[Any]:
                self.calls.append(query)
                return [
                    SimpleNamespace(index=0, score=0.9),
                    SimpleNamespace(index=1, score=0.1),
                ]

        rerank = FakeRerank()
        result = await run(
            clients=SimpleNamespace(rerank=rerank, postgres=None),
            args={
                "queries": ["one atomic query", "must be ignored"],
                "top_k": 2,
                "candidates": [
                    {"chunk_id": "a", "content": "content a", "score": 0.5, "recall_score": 0.5, "document_score": 0.8},
                    {"chunk_id": "b", "content": "content b", "score": 0.25, "recall_score": 0.25, "document_score": 0.2},
                ],
            },
        )

        assert rerank.calls == ["one atomic query"]
        assert result["ranked"][0]["chunk_id"] == "a"
        assert result["ranked"][0]["composite_score"] == pytest.approx(0.98)
        assert result["ranked"][0]["score_trace"]["weights"] == {
            "rerank": 0.6,
            "recall": 0.3,
            "summary": 0.1,
        }

    async def test_retry_reuses_locked_document_scope(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        workflow = RagRetrievalWorkflow()
        locked_documents = [
            {"knowledge_id": "report-2023", "knowledge_title": "2023年度报告"},
            {"knowledge_id": "report-2024", "knowledge_title": "2024年度报告"},
            {"knowledge_id": "report-2025", "knowledge_title": "2025年度报告"},
        ]
        ictx = _make_ictx(instance_data={
            "attempt": 0,
            "query": "基于近三年报告分析趋势",
            "rewrite": {"analysis_query": "分析趋势", "doc_queries": ["近三年报告"]},
            "retrieval_context": {
                "premise": "近三年报告",
                "doc_queries": ["近三年报告"],
                "selected_documents": locked_documents,
            },
            "quality_evaluation": {
                "passed": False,
                "missing_points": ["缺少2025年数值"],
                "retry_queries": ["2025年具体数值"],
                "requires_document_reselection": False,
            },
            "gap_ledger": {"next_queries": ["2025年具体数值"]},
            "evidence": [],
        })

        await workflow._ef_expand_retry(ictx)

        assert ictx.instance_data["reuse_selected_documents"] is True
        assert ictx.instance_data["selected_knowledge_ids"] == [
            "report-2023", "report-2024", "report-2025",
        ]
        assert ictx.instance_data["rewrite"]["analysis_query"] == "分析趋势"
        assert workflow._rag_queries(ictx) == ["近三年报告 2025年具体数值"]

        ictx.instance_data["timings"] = {}
        await workflow._ef_branch_recall_rag(ictx)
        assert ictx.instance_data["selected_knowledge_ids"] == [
            "report-2023", "report-2024", "report-2025",
        ]
        assert ictx.instance_data["document_read_mode"] == "global_chunk_rerank"

    def test_default_quality_rounds_is_five(self) -> None:
        from egis_agent_plugins.core.flows.rag.schema import DEFAULT_QUALITY_MAX_ROUNDS

        assert DEFAULT_QUALITY_MAX_ROUNDS == 5


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

    def test_route_rag_default_passes(self) -> None:
        # 旧 `_route_internal` 已重命名为 `_route_rag`（guards 重构拆分后的同语义分支）。
        from egis_agent_plugins.core.flows.rag.guards import _route_rag
        ictx = _make_ictx(instance_data={"rewrite": {"intent": "rag"}, "source": "auto"})
        _route_rag(ictx)  # should never raise


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

    def test_can_retry_rejects_stalled_query_plan(self) -> None:
        from egis_agent_plugins.core.flows.rag.guards import _can_retry

        ictx = _make_ictx(instance_data={
            "attempt": 0,
            "max_retries": 4,
            "retry_stalled": True,
        })
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
        # 当前 workflow 已无 `start` action：
        #   rewrite（内置 is_obvious_no_retrieval_query 快速通道） → route → no_retrieval
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t1", "rewrite", {"query": "你好"}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "rewritten"
        r = await w.execute("t1", "route", {}, session)
        assert r.new_state == "no_retrieval"
        assert r.success

    async def test_no_retrieval_thanks(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t1", "rewrite", {"query": "谢谢"}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t1", "route", {}, session)
        assert r.new_state == "no_retrieval"

    async def test_start_to_rewrite_pending(self) -> None:
        # 重构后无独立 `start` action，直接从 None → rewritten。
        # 保留测试名以便 CI 历史对齐，断言修正为 rewritten 。
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        r = await w.execute("t2", "rewrite", {"query": "什么是 RAG"}, {})
        assert r.new_state == "rewritten"

    async def test_rewrite_fallback_no_tool(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t3", "rewrite", {"query": "什么是 RAG"}, session)
        assert r.new_state == "rewritten"
        # rewrite service 无可用 LLM 时 fallback intent="rag"（旧值“kb_search”已废弃）。
        inst = r.state_delta["_workflows"]["rag"]["instances"]["t3"]
        assert inst["data"]["rewrite"]["intent"] == "rag"

    async def test_route_internal_default(self) -> None:
        # `route` 后面拆分了 branch_recall/chunk_recall，这里只断言 route 目标 `routed`。
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}
        r = await w.execute("t4", "rewrite", {"query": "test query"}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t4", "route", {}, session)
        assert r.new_state == "routed"

    async def test_full_path_to_no_evidence(self) -> None:
        """rewrite → route(routed) → branch_recall(branch_recalled)
        → chunk_recall(insufficient) → retry(no_evidence)

        当前 workflow 拆分：recall = branch_recall + chunk_recall；
        无 clients / 无选中文档 → chunk_recall 命中 _no_selected_docs → insufficient。
        """
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow
        w = RagRetrievalWorkflow()
        session: dict[str, Any] = {}

        r = await w.execute("t5", "rewrite", {"query": "test", "max_retries": 0}, session)
        _merge_state(session, r.state_delta)
        r = await w.execute("t5", "route", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "routed"

        r = await w.execute("t5", "branch_recall", {}, session)
        _merge_state(session, r.state_delta)
        assert r.new_state == "branch_recalled"

        r = await w.execute("t5", "chunk_recall", {}, session)
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


class TestDeterministicSelectionAndScoring:
    def test_selection_has_no_filename_llm_or_weight_strategy(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select import stage

        assert not hasattr(stage, "_filename_score_llm")
        assert not hasattr(stage, "_document_match_strategy")
        assert not hasattr(stage, "_shortlist_documents")

    def test_position_prior_is_bounded_to_five_percent(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rank.stage import _position_prior

        assert 0.95 <= _position_prior(0, 100) <= 1.05
        assert 0.95 <= _position_prior(99, 100) <= 1.05
        assert _position_prior(0, 100) > _position_prior(99, 100)
        assert _position_prior(-1, 100) == 1.0


# ────────────────────────────────────────────────────────────
# 5. Progress
# ────────────────────────────────────────────────────────────
class TestProgress:
    def test_emit_progress_with_emitter(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress, PROGRESS_EVENT
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        emit_progress(ctx, tool="query_rewrite", status="pending")
        # 当前 emit_progress 同时下发主进度事件 + frontend_digest 定制事件，只断言主事件。
        main_events = [e for e in events if e.get("type") == PROGRESS_EVENT]
        assert len(main_events) == 1
        assert main_events[0]["tool"] == "query_rewrite"
        assert main_events[0]["status"] == "pending"

    def test_emit_progress_without_emitter(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress
        # Should not raise
        emit_progress(None, tool="test", status="done")
        emit_progress({}, tool="test", status="done")

    def test_research_progress_is_correlated_and_compact(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress, FRONTEND_DIGEST_EVENT

        events: list[dict] = []
        context = {
            "emit_event": events.append,
            "temp:research_trace": {
                "project_id": "p1",
                "turn": 3,
                "phase": "collecting",
                "step": "collect",
                "task_id": "u-2024",
                "query": "公司 A 2024 年企业数",
            },
        }
        emit_progress(
            context,
            tool="document_select",
            status="done",
            count=6,
            extra={"selected": [{"title": f"doc-{index}", "content": "x" * 5000} for index in range(6)]},
        )

        raw = events[0]
        assert raw["key"] == "research:p1:3:u-2024:document_select"
        assert raw["query"] == "公司 A 2024 年企业数"
        assert "selected" not in raw
        assert raw["document_titles"] == ["doc-0", "doc-1", "doc-2"]
        digest = next(event for event in events if event.get("custom_type") == FRONTEND_DIGEST_EVENT)
        step = digest["custom_data"]["step"]
        assert step["turn"] == 3
        assert step["phase"] == "collecting"
        assert step["count"] == 6

    def test_emit_references(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_references, REFERENCES_EVENT
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        refs = [{"chunk_id": "c1", "doc_title": "Doc"}]
        emit_references(ctx, refs)
        # 同上：同时下发 references + frontend_digest。
        main_events = [e for e in events if e.get("type") == REFERENCES_EVENT]
        assert len(main_events) == 1
        assert main_events[0]["data"] == refs

    def test_emit_progress_with_count(self) -> None:
        from egis_agent_plugins.core.flows.rag.events import emit_progress
        events: list[dict] = []
        ctx = {"emit_event": events.append}
        emit_progress(ctx, tool="knowledge_search", status="done", count=12)
        assert events[0]["count"] == 12


# ───────────────────────────────────────────────────────────────
# User-selected shortcut (方案 B) + tool-side hints fallback (方案 C)
# ───────────────────────────────────────────────────────────────


class TestUserSelectedShortcut:
    def test_selected_file_names_are_read_from_authorized_scope(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _selected_file_names

        mapping = _selected_file_names({
            "rag_filter": [{
                "kb_id": "kb1",
                "tags": [{"tag_id": "t1", "files": [{"id": "f1", "name": "2023.pdf"}]}],
                "files": [{"id": "f2", "name": "2024.pdf"}],
            }],
        }, ctx={})

        assert mapping == {"f1": "2023.pdf", "f2": "2024.pdf"}

    def test_frontend_filter_injection_does_not_create_strategy_hints(self) -> None:
        from egis_agent_plugins.core.flows.rag.tool import RagTool

        args: dict[str, Any] = {"query": "q"}
        ctx = {"user:rag_state": {"rag_filter": [{"kb_id": "kb1", "files": [{"id": "f1"}]}]}}

        filters = RagTool._inject_frontend_filters(args=args, ctx=ctx)

        assert filters["rag_filter"][0]["kb_id"] == "kb1"
        assert "hints" not in args
