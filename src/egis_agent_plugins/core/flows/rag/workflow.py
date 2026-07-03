"""RagRetrievalWorkflow — RAG 全流程状态机。

对 LLM 暴露单一 ``rag`` 工具，内部基于 guarded transition
auto-drive 完成 rewrite → route → recall → rank → read → decide 全流程。

设计要点：
- ``transitions`` 在 ``__init__`` 中按 clients 动态构建（闭包绑定 effect）
- 同一 action 的多条 Transition 构成 guarded fork（first-match-wins）
- effect 是 async 函数，可 ``await tool.execute(...)`` 调下游工具
- instance_data 存放所有中间状态（query / rewrite / candidates / ranked / evidence / references）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, ClassVar

from ark_agentic.core.workflow.engine import Workflow
from ark_agentic.core.workflow.protocol import (
    EffectOutput,
    InstanceCtx,
    Transition,
)

from .guards import (
    _can_retry,
    _evidence_insufficient,
    _evidence_sufficient,
    _has_candidates,
    _has_selected_docs,
    _no_candidates,
    _no_selected_docs,
    _retry_exhausted_has_partial,
    _retry_exhausted_no_evidence,
    _route_no_retrieval,
    _route_rag,
    _route_web,
    _web_requested_but_disabled,
    is_obvious_no_retrieval_query,
)
from .events import emit_progress, emit_references
from .schema import Candidate, Reference, new_instance_data
from .stages.rank.stage import run as _rank_run
from .stages.recall.stage import run as _recall_run
from .stages.recall.web import run as _web_run
from .stages.rewrite.stage import run as _rewrite_run
from .stages.select.stage import run as _select_run

# 前端 rag_state 硬覆盖过滤器
from egis_agent_plugins.core.flows.rag._services.document_reader import read_ranked_context
from egis_agent_plugins.core.flows.rag._services.scope_adapter import read_rag_state

logger = logging.getLogger(__name__)


# ── Scope helpers ──────────────────────────────────────────────────────────


def _has_doc_scope(filters: dict[str, Any]) -> bool:
    """Whether the user explicitly constrained retrieval to documents/files."""
    for kb in filters.get("rag_filter") or []:
        if not isinstance(kb, dict):
            continue
        if kb.get("files"):
            return True
        for tag in kb.get("tags") or kb.get("tag") or []:
            if isinstance(tag, dict) and tag.get("files"):
                return True
    return False


def _routed_web(ictx: InstanceCtx) -> None:
    if ictx.probing:
        return
    if ictx.instance_data.get("route") == "web":
        return
    from ark_agentic.core.workflow.errors import WorkflowRejection
    raise WorkflowRejection(code="guard", message="not web route")


def _routed_rag(ictx: InstanceCtx) -> None:
    if ictx.probing:
        return
    if ictx.instance_data.get("route") == "rag":
        return
    from ark_agentic.core.workflow.errors import WorkflowRejection
    raise WorkflowRejection(code="guard", message="not rag route")


# ── Workflow class ───────────────────────────────────────────────────────


class RagRetrievalWorkflow(Workflow):
    """RAG 检索状态机。

    ``transitions`` 在 ``__init__`` 中按传入的 clients 动态构建，
    effect 直接委托到各 stage 的模块级 ``run()`` 函数。
    """

    flow_id: ClassVar[str] = "rag"

    states: ClassVar[tuple[str, ...]] = (
        "rewrite_pending",
        "rewritten",
        "routed",
        "branch_recalled",
        "recalled",
        "ranked",
        "evidence_checked",
        "insufficient",
        "answer_ready",
        "no_retrieval",
        "no_evidence",
    )

    initial_state: ClassVar[str] = "rewrite_pending"

    final_states: ClassVar[tuple[str, ...]] = (
        "answer_ready",
        "no_retrieval",
        "no_evidence",
    )

    # transitions populated dynamically in __init__
    transitions: ClassVar[tuple[Transition, ...]] = ()

    def __init__(
        self,
        *,
        clients: Any = None,
    ) -> None:
        self._clients = clients
        self.transitions = self._build_transitions()

    # ── Transition builder ───────────────────────────────────────────────

    def _build_transitions(self) -> tuple[Transition, ...]:
        return (
            # ── rewrite ──
            Transition("rewrite", None, "rewritten",
                       effect=self._ef_rewrite),
            Transition("rewrite", "rewrite_pending", "rewritten",
                       effect=self._ef_rewrite),

            # ── route: guarded fork（顺序关键）──
            Transition("route", "rewritten", "no_retrieval",
                       guard=_route_no_retrieval,
                       effect=self._ef_route_no_retrieval),
            Transition("route", "rewritten", "no_evidence",
                       guard=_web_requested_but_disabled,
                       effect=self._ef_route_web_unavailable),
            Transition("route", "rewritten", "routed",
                       guard=_route_web,
                       effect=self._ef_route_web),
            Transition("route", "rewritten", "routed",
                       guard=_route_rag,
                       effect=self._ef_route_rag),

            # ── branch recall: route-specific first recall ──
            Transition("branch_recall", "routed", "recalled",
                       guard=_routed_web,
                       effect=self._ef_branch_recall_web),
            Transition("branch_recall", "routed", "branch_recalled",
                       guard=_routed_rag,
                       effect=self._ef_branch_recall_rag),

            # ── chunk recall: selected docs → chunks ──
            Transition("chunk_recall", "branch_recalled", "recalled",
                       guard=_has_selected_docs,
                       effect=self._ef_chunk_recall_rag),
            Transition("chunk_recall", "branch_recalled", "insufficient",
                       guard=_no_selected_docs,
                       effect=self._ef_mark_no_docs),

            # ── rank: 两条 guarded fork ──
            Transition("rank", "recalled", "ranked",
                       guard=_has_candidates,
                       effect=self._ef_fusion_rerank_mmr),
            Transition("rank", "recalled", "insufficient",
                       guard=_no_candidates,
                       effect=self._ef_mark_no_candidates),

            # ── read ──
            Transition("read", "ranked", "evidence_checked",
                       effect=self._ef_parallel_read_and_assess),

            # ── decide: 两条 guarded fork ──
            Transition("decide", "evidence_checked", "answer_ready",
                       guard=_evidence_sufficient,
                       effect=self._ef_emit_references),
            Transition("decide", "evidence_checked", "insufficient",
                       guard=_evidence_insufficient,
                       effect=self._ef_mark_insufficient),

            # ── retry: 三条 guarded fork ──
            Transition("retry", "insufficient", "rewritten",
                       guard=_can_retry,
                       effect=self._ef_expand_retry),
            Transition("retry", "insufficient", "answer_ready",
                       guard=_retry_exhausted_has_partial,
                       effect=self._ef_emit_partial_references),
            Transition("retry", "insufficient", "no_evidence",
                       guard=_retry_exhausted_no_evidence,
                       effect=self._ef_mark_no_evidence),
        )

    # ── Effect implementations ───────────────────────────────────────────

    async def _ef_start_no_retrieval(self, ictx: InstanceCtx) -> EffectOutput | None:
        ictx.instance_data["route"] = "no_retrieval"
        query = ictx.args.get("query", "")
        ictx.instance_data["query"] = query
        ictx.instance_data["source"] = ictx.args.get("source", "auto")
        ictx.instance_data["filters"] = ictx.args.get("filters") or {}
        ictx.instance_data["max_retries"] = int(ictx.args.get("max_retries", 1))
        ictx.instance_data["attempt"] = 0
        ictx.instance_data["timings"] = {}
        return EffectOutput(message="问题无需检索，可直接回答。")

    async def _ef_start_init(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        data = new_instance_data(
            query=ictx.args.get("query", ""),
            source=ictx.args.get("source", "auto"),
            filters=ictx.args.get("filters"),
            hints=ictx.args.get("hints") or {},
            max_retries=int(ictx.args.get("max_retries", 1)),
        )
        ictx.instance_data.update(data)

        # Mirror frontend hierarchical scope into workflow filters without flattening.
        filters = ictx.instance_data.setdefault("filters", {}) or {}
        if not filters.get("rag_filter"):
            rag_state = read_rag_state(ictx.session_ctx)
            rag_filter = rag_state.get("rag_filter") or rag_state.get("rag_filters")
            if isinstance(rag_filter, list) and rag_filter:
                filters["rag_filter"] = rag_filter

        ictx.instance_data["timings"]["start_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_rewrite(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        ctx = ictx.session_ctx

        if not ictx.instance_data.get("query"):
            data = new_instance_data(
                query=ictx.args.get("query", ""),
                source=ictx.args.get("source", "auto"),
                filters=ictx.args.get("filters"),
                hints=ictx.args.get("hints") or {},
                max_retries=int(ictx.args.get("max_retries", 1)),
            )
            ictx.instance_data.update(data)

            filters = ictx.instance_data.setdefault("filters", {}) or {}
            if not filters.get("rag_filter"):
                rag_state = read_rag_state(ictx.session_ctx)
                rag_filter = rag_state.get("rag_filter") or rag_state.get("rag_filters")
                if isinstance(rag_filter, list) and rag_filter:
                    filters["rag_filter"] = rag_filter

        query = ictx.instance_data.get("query", "")
        if is_obvious_no_retrieval_query(query):
            ictx.instance_data["rewrite"] = {
                "intent": "direct",
                "keywords": [],
                "sub_queries": [query],
                "rewrite_query": query,
                "doc_query": "",
                "analysis_query": "",
            }
            ictx.instance_data["timings"]["rewrite_ms"] = int((time.perf_counter() - t0) * 1000)
            return None

        emit_progress(ctx, tool="query_rewrite", status="pending")

        result = await _rewrite_run(
            args={
                "query": query,
            },
            ctx=ctx,
            clients=self._clients,
        )

        raw_intent = result.get("intent", "rag")
        intent = raw_intent if raw_intent in ("web_search", "direct") else "rag"
        keywords = result.get("keywords", [])
        sub_queries = result.get("sub_queries", [])
        rewrite_query = result.get("rewrite_query", "")
        doc_query = result.get("doc_query", "")
        analysis_query = result.get("analysis_query", "")

        # 如果 LLM 没传 source 但 intent=web_search，设置 source=web
        if ictx.instance_data.get("source") == "auto" and intent == "web_search":
            ictx.instance_data["source"] = "web"

        ictx.instance_data["rewrite"] = {
            "intent": intent,
            "keywords": keywords,
            "sub_queries": sub_queries,
            "rewrite_query": rewrite_query,
            "doc_query": doc_query,
            "analysis_query": analysis_query,
        }

        emit_progress(
            ctx,
            tool="query_rewrite",
            status="done",
            extra={
                "rewrite": rewrite_query,
                "doc_query": doc_query,
                "analysis_query": analysis_query,
                "intent": intent,
                "route": intent,
                "keywords": keywords,
                "sub_queries": sub_queries,
            },
        )

        ictx.instance_data["timings"]["rewrite_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_route_web_unavailable(self, ictx: InstanceCtx) -> EffectOutput | None:
        ictx.instance_data["route"] = "web_unavailable"
        return EffectOutput(message="Web 搜索服务暂未配置，无法获取实时信息。")

    async def _ef_route_no_retrieval(self, ictx: InstanceCtx) -> EffectOutput | None:
        ictx.instance_data["route"] = "no_retrieval"
        return EffectOutput(message="问题无需检索，可直接回答。")

    async def _ef_route_web(self, ictx: InstanceCtx) -> EffectOutput | None:
        ctx = ictx.session_ctx
        ictx.instance_data["route"] = "web"
        emit_progress(ctx, tool="route", status="done", extra={"route": "web_search"})
        return None

    async def _ef_branch_recall_web(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        ctx = ictx.session_ctx

        emit_progress(ctx, tool="web_search", status="pending")

        rewrite = ictx.instance_data.get("rewrite") or {}
        query = (
            rewrite.get("analysis_query")
            or rewrite.get("rewrite_query")
            or ictx.instance_data.get("query", "")
        )

        result = await _web_run(args={"query": query}, ctx=ctx)
        web_results = result.get("results", [])
        # Convert web results to unified Candidate schema
        candidates = []
        for i, wr in enumerate(web_results):
            c = Candidate(
                id=wr.get("id", f"web_{i}"),
                content=wr.get("snippet", wr.get("content", "")),
                chunk_id=wr.get("chunk_id", f"web_chunk_{i}"),
                knowledge_id=wr.get("knowledge_id", f"web_doc_{i}"),
                knowledge_base_id="web",
                score=float(wr.get("score", 0.0)),
                knowledge_title=wr.get("title", ""),
                source="web",
                source_query=query,
                query_type="web",
            )
            candidates.append(c.to_dict())
        ictx.instance_data["candidates"] = candidates

        emit_progress(ctx, tool="web_search", status="done",
                      count=len(ictx.instance_data.get("candidates", [])))
        ictx.instance_data["timings"]["recall_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_route_rag(self, ictx: InstanceCtx) -> EffectOutput | None:
        """RAG route decision only."""
        ctx = ictx.session_ctx
        ictx.instance_data["route"] = "rag"
        emit_progress(ctx, tool="route", status="done", extra={"route": "rag"})
        return None

    async def _ef_branch_recall_rag(self, ictx: InstanceCtx) -> EffectOutput | None:
        """RAG branch recall — select documents by filename + summary."""
        t0 = time.perf_counter()
        ctx = ictx.session_ctx

        rewrite = ictx.instance_data.get("rewrite") or {}
        filters = ictx.instance_data.get("filters") or {}
        doc_query = (
            rewrite.get("doc_query")
            or rewrite.get("rewrite_query")
            or ictx.instance_data.get("query", "")
        )
        analysis_query = (
            rewrite.get("analysis_query")
            or rewrite.get("rewrite_query")
            or ictx.instance_data.get("query", "")
        )

        sd_args: dict[str, Any] = {
            "query": doc_query,
            "summary_query": analysis_query,
            "top_k": int(os.getenv("RAG_DOCUMENT_SELECT_TOP_K", "20")),
            "hints": ictx.instance_data.get("hints") or {},
        }
        if filters.get("rag_filter"):
            sd_args["rag_filter"] = filters["rag_filter"]

        emit_progress(
            ctx,
            tool="document_select",
            status="pending",
            extra={
                "query": doc_query,
                "summary_query": analysis_query,
                "document_match_strategy": {
                    "hints": ictx.instance_data.get("hints") or {},
                },
            },
        )

        try:
            result = await _select_run(clients=self._clients, args=sd_args, ctx=ctx)
        except Exception as e:
            logger.warning("[RAG WF] rag select_documents failed: %s", e)
            ictx.instance_data["selected_knowledge_ids"] = []
            ictx.instance_data["candidates"] = []
            ictx.instance_data["timings"]["branch_recall_ms"] = int((time.perf_counter() - t0) * 1000)
            emit_progress(
                ctx,
                tool="document_select",
                status="error",
                extra={"query": doc_query, "error": str(e)},
            )
            return None

        kid_list = result.get("knowledge_ids", [])
        ictx.instance_data["selected_knowledge_ids"] = kid_list
        ictx.instance_data["selected_documents"] = result.get("documents", [])
        ictx.instance_data["document_match_strategy"] = result.get("document_match_strategy") or {}
        ictx.instance_data["document_select_thresholds"] = result.get("document_select_thresholds") or {}
        ictx.instance_data["rejected_documents"] = result.get("rejected_documents") or []
        ictx.instance_data["excluded_documents"] = result.get("excluded_documents") or []
        ictx.instance_data["candidates"] = []
        emit_progress(
            ctx,
            tool="document_select",
            status="done",
            count=len(kid_list),
            extra={
                "query": doc_query,
                "documents": [
                    {
                        "knowledge_id": doc.get("knowledge_id", ""),
                        "title": doc.get("knowledge_title") or doc.get("file_name", ""),
                        "score": doc.get("score", 0.0),
                        "initial_recall_components": doc.get("initial_recall_components", {}),
                        "document_match_scores": doc.get("document_match_scores", {}),
                    }
                    for doc in result.get("documents", [])[:8]
                ],
                "rejected_documents": [
                    {
                        "knowledge_id": doc.get("knowledge_id", ""),
                        "title": doc.get("knowledge_title") or doc.get("file_name", ""),
                        "score": doc.get("score", 0.0),
                        "initial_recall_components": doc.get("initial_recall_components", {}),
                        "document_match_scores": doc.get("document_match_scores", {}),
                    }
                    for doc in result.get("rejected_documents", [])[:8]
                ],
                "excluded_documents": [
                    {
                        "knowledge_id": doc.get("knowledge_id", ""),
                        "title": doc.get("knowledge_title") or doc.get("file_name", ""),
                        "score": doc.get("score", 0.0),
                    }
                    for doc in result.get("excluded_documents", [])[:8]
                ],
                "document_match_strategy": result.get("document_match_strategy") or {},
                "document_select_thresholds": result.get("document_select_thresholds") or {},
            },
        )
        logger.info("[RAG WF] rag → selected docs=%d; recall chunks next", len(kid_list))
        ictx.instance_data["timings"]["branch_recall_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_chunk_recall_rag(self, ictx: InstanceCtx) -> EffectOutput | None:
        """RAG chunk recall — search chunks inside selected documents."""
        await self._ef_recall_scoped(ictx)
        return None

    async def _ef_recall_scoped(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Run hybrid search scoped by selected knowledge_ids."""
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        emit_progress(ctx, tool="knowledge_search", status="pending")

        rewrite = ictx.instance_data.get("rewrite") or {}
        query = rewrite.get("rewrite_query") or ictx.instance_data.get("query", "")
        sub_queries = rewrite.get("sub_queries", [])
        keywords = rewrite.get("keywords", [])

        # Build queries list: rewrite_query + sub_queries (deduplicated, max 5)
        original_query = ictx.instance_data.get("query", "")
        queries = [query] if query else []
        if original_query and original_query not in queries:
            queries.append(original_query)
        for sq in sub_queries:
            if sq and sq not in queries:
                queries.append(sq)
        queries = queries[:5]

        if not queries:
            ictx.instance_data["candidates"] = []
            emit_progress(ctx, tool="knowledge_search", status="done", count=0)
            return None

        ks_args: dict[str, Any] = {"queries": queries}

        # Scope by selected documents from the document selector.
        kid_list = ictx.instance_data.get("selected_knowledge_ids") or []
        if kid_list:
            ks_args["knowledge_ids"] = kid_list
        selected_documents = ictx.instance_data.get("selected_documents") or []
        if selected_documents:
            ks_args["selected_documents"] = selected_documents

        # Pass only the structured frontend scope. Flat legacy filters are not
        # part of the current RAG contract.
        filters = ictx.instance_data.get("filters") or {}
        if filters.get("rag_filter"):
            ks_args["rag_filter"] = filters["rag_filter"]

        candidates: list[dict[str, Any]] = []
        try:
            result = await _recall_run(
                clients=self._clients,
                args=ks_args,
                ctx=ctx,
                top_k=int(os.getenv("RAG_CHUNK_RECALL_TOP_K", "40")),
            )
        except Exception as e:
            logger.warning("[RAG WF] knowledge_search failed: %s", e)
            result = {}
        raw_results = result.get("results", [])
        if result.get("scope_count") is not None:
            ictx.instance_data["scope_count"] = result.get("scope_count")
        # 诊断：recall 返回结果来源
        raw_kids = list(set(sr.get("knowledge_id", "") for sr in raw_results if sr.get("knowledge_id")))
        raw_titles = list(set(sr.get("knowledge_title", "") for sr in raw_results if sr.get("knowledge_title")))
        logger.info(
            "[RAG WF] recall DIAG: ks_args knowledge_ids=%s, raw_results=%d, unique_kids=%d %s, titles=%s",
            ks_args.get("knowledge_ids"), len(raw_results),
            len(raw_kids), raw_kids[:5], raw_titles[:5],
        )
        for sr_dict in raw_results:
            # Convert to Candidate
            c = Candidate(
                id=sr_dict.get("id", ""),
                content=sr_dict.get("content", ""),
                chunk_id=sr_dict.get("chunk_id", ""),
                knowledge_id=sr_dict.get("knowledge_id", ""),
                knowledge_base_id=sr_dict.get("knowledge_base_id", ""),
                score=float(sr_dict.get("score", 0.0)),
                knowledge_title=sr_dict.get("knowledge_title", ""),
                chunk_index=sr_dict.get("chunk_index", 0),
                source="internal",
                source_query=sr_dict.get("source_query", query),
                query_type=sr_dict.get("query_type", "hybrid"),
            )
            candidates.append(c.to_dict())

        ictx.instance_data["candidates"] = candidates
        emit_progress(ctx, tool="knowledge_search", status="done", count=len(candidates))
        ictx.instance_data["timings"]["recall_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_mark_no_docs(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.info("[RAG WF] No docs selected, marking insufficient")
        return None

    async def _ef_fusion_rerank_mmr(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Rank phase — rerank, threshold, and MMR order candidates."""
        t0 = time.perf_counter()
        candidates = ictx.instance_data.get("candidates") or []
        rewrite = ictx.instance_data.get("rewrite") or {}
        query = (
            rewrite.get("analysis_query")
            or rewrite.get("rewrite_query")
            or ictx.instance_data.get("query", "")
        )
        queries = [query] if query else []
        for sq in rewrite.get("sub_queries", []):
            if sq and sq not in queries:
                queries.append(sq)

        result = await _rank_run(
            clients=self._clients,
            args={"candidates": candidates, "queries": queries},
            ctx=ictx.session_ctx,
        )

        ictx.instance_data["ranked"] = result.get("ranked", [])
        emit_progress(
            ictx.session_ctx,
            tool="rank",
            status="done",
            count=len(ictx.instance_data["ranked"]),
            extra={"queries": queries[:3]},
        )
        ictx.instance_data["timings"]["rank_ms"] = (
            result.get("rank_ms")
            if result.get("rank_ms") is not None
            else int((time.perf_counter() - t0) * 1000)
        )
        return None

    async def _ef_mark_no_candidates(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.info("[RAG WF] No candidates after recall")
        return None

    async def _ef_parallel_read_and_assess(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Read ranked chunk anchors into evidence via the document reader."""
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []

        if not ranked:
            ictx.instance_data["evidence"] = []
            ictx.instance_data["evidence_sufficient"] = False
            return None

        evidence = await read_ranked_context(
            clients=self._clients,
            ranked=ranked,
            top_k=int(os.getenv("RAG_EVIDENCE_TOP_K", "8")),
        )
        ictx.instance_data["evidence"] = evidence
        ictx.instance_data["evidence_sufficient"] = bool(evidence)
        emit_progress(
            ctx,
            tool="read",
            status="done",
            count=len(evidence),
            extra={
                "route": ictx.instance_data.get("route", ""),
                "read_modes": sorted({
                    str(e.get("read_mode", ""))
                    for e in evidence
                    if e.get("read_mode")
                }),
            },
        )
        ictx.instance_data["timings"]["read_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_emit_references(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Build references from evidence chunks and emit to frontend."""
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []
        evidence = ictx.instance_data.get("evidence") or []

        # 诊断：追踪引用来源
        selected_kids = ictx.instance_data.get("selected_knowledge_ids") or []
        ranked_kids = list(set(item.get("knowledge_id", "") for item in ranked if item.get("knowledge_id")))
        ranked_titles = list(set(item.get("knowledge_title", "") for item in ranked if item.get("knowledge_title")))
        evidence_kids = list(set(item.get("knowledge_id", "") for item in evidence if item.get("knowledge_id")))
        logger.info(
            "[RAG WF] references DIAG: selected_kids=%d %s, ranked=%d, unique_kids=%d %s, evidence=%d unique_kids=%d %s, titles=%s",
            len(selected_kids), selected_kids[:3],
            len(ranked), len(ranked_kids), ranked_kids[:5],
            len(evidence), len(evidence_kids), evidence_kids[:5],
            ranked_titles[:5],
        )

        refs: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()
        for item in evidence:
            chunk_id = item.get("chunk_id", item.get("id", ""))
            if not chunk_id or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            ref = Reference(
                chunk_id=chunk_id,
                doc_title=item.get("knowledge_title", ""),
                knowledge_id=item.get("knowledge_id", ""),
                score=float(item.get("score", 0.0)),
            )
            refs.append(ref.to_dict())

        ictx.instance_data["references"] = refs

        # Emit references event (前置推送，不等答案写完)
        emit_references(ctx, refs)

        # Build evidence pack for LLM
        evidence_text = _build_evidence_text(
            ictx.instance_data.get("query", ""),
            ictx.instance_data.get("evidence", []),
            refs,
        )

        logger.info(
            "[RAG_EFFECT] emit_references: %d refs, chunk_ids=%s",
            len(refs),
            [r.get("chunk_id", "?")[:8] for r in refs[:5]],
        )
        evidence_items = ictx.instance_data.get("evidence", [])
        evidence_refs = [
            {
                "chunk_id": ev.get("chunk_id", ev.get("id", "")),
                "doc_title": ev.get("knowledge_title", ""),
                "knowledge_id": ev.get("knowledge_id", ""),
            }
            for ev in evidence_items
        ]
        return EffectOutput(
            message=evidence_text,
            extras={
                "_valid_kb_refs": refs,
                "_rag_evidence_refs": evidence_refs,
                "_rag_evidence_pack": {
                    "query": ictx.instance_data.get("query", ""),
                    "query_plan": {
                        "original": ictx.instance_data.get("query", ""),
                        "rewrite": (ictx.instance_data.get("rewrite") or {}).get("rewrite_query", ""),
                        "doc_query": (ictx.instance_data.get("rewrite") or {}).get("doc_query", ""),
                        "analysis_query": (ictx.instance_data.get("rewrite") or {}).get("analysis_query", ""),
                        "sub_queries": (ictx.instance_data.get("rewrite") or {}).get("sub_queries", []),
                        "intent": (ictx.instance_data.get("rewrite") or {}).get("intent", ""),
                        "hints": ictx.instance_data.get("hints", {}),
                    },
                    "route": ictx.instance_data.get("route"),
                    "scope_count": ictx.instance_data.get("scope_count", 0),
                    "references": refs,
                    "evidence_count": len(ictx.instance_data.get("evidence", [])),
                    "timings": ictx.instance_data.get("timings", {}),
                },
                "_rag_references": refs,
            },
        )

    async def _ef_emit_partial_references(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Retry exhausted but has partial results — emit what we have."""
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []

        refs: list[dict[str, Any]] = []
        for item in ranked[:5]:  # Limit partial results
            ref = Reference(
                chunk_id=item.get("chunk_id", item.get("id", "")),
                doc_title=item.get("knowledge_title", ""),
                knowledge_id=item.get("knowledge_id", ""),
                score=float(item.get("score", 0.0)),
            )
            refs.append(ref.to_dict())

        ictx.instance_data["references"] = refs
        emit_references(ctx, refs)

        evidence_text = _build_evidence_text(
            ictx.instance_data.get("query", ""),
            ranked[:5],  # Use ranked as evidence
            refs,
        )

        partial_evidence = ranked[:5]
        evidence_refs = [
            {
                "chunk_id": ev.get("chunk_id", ev.get("id", "")),
                "doc_title": ev.get("knowledge_title", ""),
                "knowledge_id": ev.get("knowledge_id", ""),
            }
            for ev in partial_evidence
        ]
        return EffectOutput(
            message=f"[部分结果] {evidence_text}",
            extras={
                "_valid_kb_refs": refs,
                "_rag_evidence_refs": evidence_refs,
                "_rag_evidence_pack": {
                    "query": ictx.instance_data.get("query", ""),
                    "query_plan": {
                        "original": ictx.instance_data.get("query", ""),
                        "rewrite": (ictx.instance_data.get("rewrite") or {}).get("rewrite_query", ""),
                        "doc_query": (ictx.instance_data.get("rewrite") or {}).get("doc_query", ""),
                        "analysis_query": (ictx.instance_data.get("rewrite") or {}).get("analysis_query", ""),
                        "sub_queries": (ictx.instance_data.get("rewrite") or {}).get("sub_queries", []),
                        "intent": (ictx.instance_data.get("rewrite") or {}).get("intent", ""),
                        "hints": ictx.instance_data.get("hints", {}),
                    },
                    "route": ictx.instance_data.get("route"),
                    "scope_count": ictx.instance_data.get("scope_count", 0),
                    "references": refs,
                    "evidence_count": len(ranked),
                    "partial": True,
                    "timings": ictx.instance_data.get("timings", {}),
                },
                "_rag_references": refs,
            },
        )

    async def _ef_expand_retry(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Expand search scope and retry — broaden sub_queries or rewrite."""
        ictx.instance_data["attempt"] = ictx.instance_data.get("attempt", 0) + 1

        filters = ictx.instance_data.get("filters") or {}

        # Reset downstream state for fresh run
        ictx.instance_data["selected_knowledge_ids"] = []
        ictx.instance_data["candidates"] = []
        ictx.instance_data["ranked"] = []
        ictx.instance_data["evidence"] = []
        ictx.instance_data["evidence_sufficient"] = False
        ictx.instance_data["references"] = []
        # 文档级约束不可越界；其它场景可在既有 KB/tag/default 范围内放宽召回。
        ictx.instance_data["allow_full_scope_recall"] = not _has_doc_scope(filters)

        # Expand: add original query to sub_queries if not present
        rewrite = ictx.instance_data.get("rewrite") or {}
        original_query = ictx.instance_data.get("query", "")
        sub_queries = list(rewrite.get("sub_queries", []))
        if original_query and original_query not in sub_queries:
            sub_queries.append(original_query)
        rewrite["sub_queries"] = sub_queries
        ictx.instance_data["rewrite"] = rewrite

        logger.info(
            "[RAG WF] retry attempt=%d, expanded sub_queries=%d",
            ictx.instance_data["attempt"], len(sub_queries),
        )
        return None

    async def _ef_mark_insufficient(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.info("[RAG WF] Evidence insufficient, entering retry path")
        return None

    async def _ef_mark_no_evidence(self, ictx: InstanceCtx) -> EffectOutput | None:
        return EffectOutput(message="未找到相关内容。")


# ── Evidence text builder ────────────────────────────────────────────────


def _evidence_excerpt(content: str, max_chars: int) -> str:
    """Return a budgeted evidence excerpt while keeping sentence boundaries."""
    content = (content or "").strip()
    if max_chars <= 0 or len(content) <= max_chars:
        return content

    excerpt = content[:max_chars]
    boundaries = [excerpt.rfind(mark) for mark in ("。", "；", "！", "？", "\n")]
    boundary = max(boundaries)
    if boundary >= max(80, int(max_chars * 0.55)):
        excerpt = excerpt[: boundary + 1]
    return excerpt.rstrip() + "\n（本条证据已按上下文预算省略后续内容。）"


def _build_evidence_text(
    query: str,
    evidence: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> str:
    """Build LLM-facing evidence pack text.

    证据以 [1]、[2] 编号格式展示，LLM 在回答中用 [N] 引用，
    final_answer 负责把 [N] 映射为真实 chunk_id 生成 <kb> 标签。
    """
    lines = [
        "=== RAG 检索结果 ===",
        f"查询: {query}",
        f"找到 {len(evidence)} 条证据，{len(references)} 条引用",
        "回答约束: 只能使用下方证据原文中的事实、数字和结论；证据中没有的信息必须说明未检索到，不得用常识或记忆补全。",
        "引用方式: 在对应文字末尾用 [N] 标注证据编号（如 [1]、[2]），同一来源复用同一编号。禁止使用 <kb> 等其它引用格式。",
        "",
    ]

    # 构建 kid → title 映射
    kid_title_map: dict[str, str] = {}
    for ref in references:
        kid = ref.get("knowledge_id", "")
        title = ref.get("doc_title", "")
        if kid and title:
            kid_title_map[kid] = title

    max_evidence = max(1, int(os.getenv("RAG_EVIDENCE_MAX_CHUNKS", "12")))
    per_chunk_chars = int(os.getenv("RAG_EVIDENCE_SNIPPET_CHARS", "1200"))
    total_budget = max(2000, int(os.getenv("RAG_EVIDENCE_MAX_TOTAL_CHARS", "12000")))
    used_chars = sum(len(line) + 1 for line in lines)
    emitted = 0

    for i, ev in enumerate(evidence[:max_evidence], 1):
        remaining = total_budget - used_chars
        if remaining <= 240:
            break

        content = ev.get("content", "")
        content_budget = remaining - 80
        if per_chunk_chars > 0:
            content_budget = min(per_chunk_chars, content_budget)
        snippet = _evidence_excerpt(content, max(160, content_budget))
        kid = ev.get("knowledge_id", "")
        title = kid_title_map.get(kid, "")
        if title:
            header = f"[{i}] ({title}):"
        else:
            header = f"[{i}]:"
        lines.append(header)
        lines.append(snippet)
        lines.append("")
        used_chars += len(header) + len(snippet) + 2
        emitted += 1

    if evidence and len(evidence) > emitted:
        lines.append(
            f"（还有 {len(evidence) - emitted} 条证据未展开；引用列表已发送给前端，"
            "需要核验原文时可点击引用查看完整材料。）"
        )

    if not evidence:
        lines.append(
            "未检索到相关证据。不得基于常识、记忆或外部信息回答；"
            "请明确告知用户当前知识库中未找到可支撑的信息，并建议补充资料或调整问题。"
        )

    return "\n".join(lines)
