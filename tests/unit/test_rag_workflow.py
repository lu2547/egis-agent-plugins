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


class TestDocumentQueryPlan:
    def test_rewrite_parses_multi_turn_context_decision(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rewrite.service import QueryRewriteService

        service = object.__new__(QueryRewriteService)
        service._max_sub_queries = 4
        result = service._parse_response(json.dumps({
            "resolved_query": "基于既有报告增加多维表格对比",
            "rewrite_query": "既有报告",
            "doc_query": "既有报告",
            "analysis_query": "增加多维表格对比",
            "sub_queries": ["多维数据表格"],
            "doc_queries": ["既有报告"],
            "intent": "rag",
            "continues_previous_rag": True,
            "reuse_previous_documents": True,
        }), "多点表格比对")

        assert result.resolved_query == "基于既有报告增加多维表格对比"
        assert result.continues_previous_rag is True
        assert result.reuse_previous_documents is True

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

    def test_retry_round_runs_only_atomic_gap_queries(self) -> None:
        from egis_agent_plugins.core.flows.rag.workflow import RagRetrievalWorkflow

        workflow = RagRetrievalWorkflow()
        queries = ["slice-a", "slice-b", "slice-c", "slice-d"]
        ictx = _make_ictx(instance_data={
            "attempt": 1,
            "query": "original analysis question",
            "rewrite": {
                "analysis_query": "incorrectly joined query",
                "sub_queries": queries,
            },
        })

        assert workflow._rag_queries(ictx) == queries

    async def test_multi_query_rerank_keeps_queries_independent(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.rank.stage import run

        class FakeRerank:
            enabled = True

            def __init__(self) -> None:
                self.calls: list[str] = []

            async def rerank(self, query: str, passages: list[str]) -> list[Any]:
                self.calls.append(query)
                index = 0 if query == "slice-a" else 1
                return [SimpleNamespace(index=index, score=0.9, content=passages[index])]

        rerank = FakeRerank()
        result = await run(
            clients=SimpleNamespace(rerank=rerank),
            args={
                "queries": ["slice-a", "slice-b"],
                "top_k": 2,
                "candidates": [
                    {"chunk_id": "a", "content": "content a", "score": 0.5},
                    {"chunk_id": "b", "content": "content b", "score": 0.5},
                ],
            },
        )

        assert set(rerank.calls) == {"slice-a", "slice-b"}
        assert "slice-a slice-b" not in rerank.calls
        assert {item["chunk_id"] for item in result["ranked"]} == {"a", "b"}

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
        assert ictx.instance_data["document_read_mode"] == "per_document_read"

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


class TestDocumentSelection:
    def test_filename_match_response_requires_constraint_decision(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import (
            _parse_filename_score_response,
        )

        parsed = _parse_filename_score_response(json.dumps({
            "scores": [
                {
                    "index": 0,
                    "score": 0.02,
                    "constraint_applies": True,
                    "constraint_matched": False,
                    "constraint_reason": "要求数据报告，文件名是风控管理材料",
                },
            ],
        }), 1)

        assert parsed == [{
            "score": 0.02,
            "constraint_applies": True,
            "constraint_matched": False,
            "constraint_reason": "要求数据报告，文件名是风控管理材料",
        }]

    def test_shortlist_hard_rejects_source_constraint_mismatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _shortlist_documents

        monkeypatch.setenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", "3")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_MIN_SCORE", "0")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", "0.85")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "score")
        docs = [
            {
                "knowledge_id": "wrong",
                "score": 0.99,
                "file_name": "平安养老险年金风控管理V5.docx",
                "document_match_scores": {
                    "constraint_applies": True,
                    "constraint_matched": False,
                    "constraint_reason": "文档类型不匹配",
                },
            },
            {
                "knowledge_id": "report",
                "score": 0.80,
                "file_name": "全国企业年金基金业务数据摘要2025年度.pdf",
                "document_match_scores": {
                    "constraint_applies": True,
                    "constraint_matched": True,
                },
            },
        ]

        selected, rejected, thresholds = _shortlist_documents(docs)

        assert [doc["knowledge_id"] for doc in selected] == ["report"]
        assert [doc["knowledge_id"] for doc in rejected] == ["wrong"]
        assert thresholds["best_score"] == 0.80
        assert thresholds["constraint_rejected_documents"] == 1
        assert thresholds["constraint_mode"] == "hard"

    def test_shortlist_keeps_constraint_mismatched_in_summary_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """summary/balanced 档下，constraint_matched=False 也不应被硬踢：
        用户问“分析平安养老险近三年资产规模变化”时，“全国企业年金
        基金业务数据摘要”这类行业合集不会在文件名里挂“平安”，
        应该靠 summary 分保留，而不能因 constraint LLM 把实体当硬限制全卡。
        """
        from egis_agent_plugins.core.flows.rag.stages.select.stage import _shortlist_documents

        monkeypatch.setenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", "3")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_MIN_SCORE", "0")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", "0.85")
        monkeypatch.setenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "score")
        docs = [
            {
                "knowledge_id": "industry_report",
                "score": 0.86,
                "file_name": "全国企业年金基金业务数据摘要2024年度.pdf",
                "document_match_scores": {
                    "constraint_applies": True,
                    "constraint_matched": False,
                    "constraint_reason": "文件名未命中实体平安养老险",
                },
            },
        ]

        selected, rejected, thresholds = _shortlist_documents(docs, preference="summary")

        assert [doc["knowledge_id"] for doc in selected] == ["industry_report"]
        assert rejected == []
        assert thresholds["constraint_rejected_documents"] == 0
        assert thresholds["constraint_mode"] == "soft"

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
    """前端已圈定文件时，select stage 应直接短路返回，
    不走 filename 打分 / summary rerank / constraint kill。
    """

    def test_collect_file_name_map_from_filters(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import (
            _collect_file_name_map,
        )

        args = {
            "filters": {
                "rag_filter": [
                    {
                        "kb_id": "kb1",
                        "kb_name": "年金业务",
                        "tags": [
                            {
                                "tag_id": "t1",
                                "tag_name": "人设",
                                "files": [
                                    {"id": "f1", "name": "2023年度数据摘要.pdf"},
                                    {"id": "f2", "name": "2024年度数据摘要.pdf"},
                                ],
                            },
                        ],
                        "files": [
                            {"id": "f3", "name": "2025年度数据摘要.pdf"},
                        ],
                    }
                ],
            },
        }

        mapping = _collect_file_name_map(args, ctx={})

        assert mapping == {
            "f1": "2023年度数据摘要.pdf",
            "f2": "2024年度数据摘要.pdf",
            "f3": "2025年度数据摘要.pdf",
        }

    def test_collect_file_name_map_falls_back_to_ctx_rag_state(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import (
            _collect_file_name_map,
        )

        ctx = {
            "user:rag_state": {
                "rag_filter": [
                    {
                        "kb_id": "kb1",
                        "tags": [
                            {
                                "tag_id": "t1",
                                "files": [{"id": "fx", "name": "only-in-ctx.pdf"}],
                            }
                        ],
                    }
                ],
            }
        }

        mapping = _collect_file_name_map(args={}, ctx=ctx)

        assert mapping == {"fx": "only-in-ctx.pdf"}

    def test_build_user_selected_result_shape(self) -> None:
        from egis_agent_plugins.core.flows.rag.stages.select.stage import (
            _build_user_selected_result,
            _document_match_strategy,
        )
        from egis_agent_plugins.core.flows.rag._services.scope_adapter import (
            scope_plan_from_filters,
        )

        rag_filter = [
            {
                "kb_id": "kb1",
                "kb_name": "年金业务",
                "tags": [
                    {
                        "tag_id": "t1",
                        "files": [
                            {"id": "f1", "name": "2023.pdf"},
                            {"id": "f2", "name": "2024.pdf"},
                        ],
                    }
                ],
            }
        ]
        scope_plan = scope_plan_from_filters({"rag_filter": rag_filter})
        strategy = _document_match_strategy({"document_match_preference": "summary"})

        result = _build_user_selected_result(
            query="分析近三年平安养老险资产规模变化趋势",
            document_queries=["平安养老险 2023 年报告"],
            document_match_strategy=strategy,
            scope_plan=scope_plan,
            user_selected_ids=["f1", "f2"],
            file_name_by_id={"f1": "2023.pdf", "f2": "2024.pdf"},
        )

        assert result["count"] == 2
        assert result["knowledge_ids"] == ["f1", "f2"]
        assert result["knowledge_base_ids"] == ["kb1"]
        assert result["document_select_thresholds"]["mode"] == "user_selected_direct"
        # 选中的文档应带标记，下游 workflow 就能在 trace 中看到“用户直选”，
        # 而非误以为是 LLM 选的。
        titles = [doc["knowledge_title"] for doc in result["documents"]]
        assert titles == ["2023.pdf", "2024.pdf"]
        for doc in result["documents"]:
            assert doc["document_match_scores"]["user_directly_selected"] is True
            assert doc["document_match_strategy"]["user_directly_selected"] is True
            assert doc["knowledge_base_id"] == "kb1"

    def test_inject_frontend_filters_defaults_hints_to_summary(self) -> None:
        from egis_agent_plugins.core.flows.rag.tool import RagTool

        args: dict[str, Any] = {"query": "q"}
        ctx = {
            "user:rag_state": {
                "rag_filter": [
                    {
                        "kb_id": "kb1",
                        "files": [{"id": "f1", "name": "a.pdf"}],
                    }
                ],
            }
        }

        filters = RagTool._inject_frontend_filters(args=args, ctx=ctx)

        assert "rag_filter" in filters
        assert args["filters"]["rag_filter"][0]["kb_id"] == "kb1"
        assert args["hints"]["document_match_preference"] == "summary"

    def test_inject_frontend_filters_preserves_explicit_llm_hints(self) -> None:
        from egis_agent_plugins.core.flows.rag.tool import RagTool

        args: dict[str, Any] = {
            "query": "q",
            "hints": {"document_match_preference": "filename", "reason": "user指定文件名"},
        }
        ctx = {
            "user:rag_state": {
                "rag_filter": [
                    {
                        "kb_id": "kb1",
                        "files": [{"id": "f1", "name": "a.pdf"}],
                    }
                ],
            }
        }

        RagTool._inject_frontend_filters(args=args, ctx=ctx)

        # 不覆盖 LLM 已显式传入的偏好。
        assert args["hints"]["document_match_preference"] == "filename"
        assert args["hints"]["reason"] == "user指定文件名"

    def test_inject_frontend_filters_without_rag_filter_leaves_hints_untouched(
        self,
    ) -> None:
        from egis_agent_plugins.core.flows.rag.tool import RagTool

        args: dict[str, Any] = {"query": "q"}
        ctx: dict[str, Any] = {}

        RagTool._inject_frontend_filters(args=args, ctx=ctx)

        # 前端未圈定时，不强塑 preference；交回 _document_match_strategy 默认。
        assert "hints" not in args or not args["hints"].get(
            "document_match_preference"
        )
