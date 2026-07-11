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
import json
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
from .schema import Candidate, Reference, DEFAULT_QUALITY_MAX_RETRIES, new_instance_data
from .stages.rank.stage import run as _rank_run
from .stages.evaluate.stage import run as _evaluate_run
from .stages.recall.stage import run as _recall_run
from .stages.recall.web import run as _web_run
from .stages.rewrite.stage import run as _rewrite_run
from .stages.select.stage import run as _select_run

# 前端 rag_state 硬覆盖过滤器
from egis_agent_plugins.core.flows.rag._services.document_reader import (
    read_ranked_context,
)
from egis_agent_plugins.core.flows.rag._services.scope_adapter import read_rag_state

logger = logging.getLogger(__name__)


def _ranked_by_document(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in ranked:
        knowledge_id = str(item.get("knowledge_id") or "")
        key = knowledge_id or str(item.get("knowledge_title") or "unknown")
        entry = grouped.setdefault(
            key,
            {
                "title": item.get("knowledge_title") or item.get("file_name") or knowledge_id,
                "kid": knowledge_id,
                "chunk_count": 0,
            },
        )
        entry["chunk_count"] += 1
    return sorted(
        grouped.values(),
        key=lambda item: (-int(item["chunk_count"]), str(item["title"])),
    )


def _coerce_bool(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        return default
    return bool(value) if value is not None else default


def _evaluation_enabled(ictx: InstanceCtx) -> bool:
    """Normalize the public evaluation switch for workflow and direct callers."""
    return _coerce_bool(ictx.instance_data.get("enable_evaluation"), default=True)


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
        # Per-tool-call temporary evidence cache. It is intentionally kept out
        # of workflow/session state, so repeated retrieval rounds do not
        # serialize full chunk content into the conversation context.
        self._evidence_pools: dict[str, list[dict[str, Any]]] = {}
        self.transitions = self._build_transitions()

    def _update_evidence_pool(
        self,
        ictx: InstanceCtx,
        current: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        round_number = int(ictx.instance_data.get("attempt", 0) or 0) + 1
        tagged = []
        for item in current:
            copy = dict(item)
            copy["_quality_round"] = round_number
            tagged.append(copy)
        merged = _merge_evidence_rounds(
            self._evidence_pools.get(ictx.instance_id, []),
            tagged,
        )
        pool_limit = max(1, int(os.getenv("RAG_EVIDENCE_POOL_MAX_CHUNKS", "100")))
        if len(merged) > pool_limit:
            merged = _select_evidence_by_document(merged, max_items=pool_limit)
        self._evidence_pools[ictx.instance_id] = merged
        return merged

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

            # ── read ( + 内嵌 evaluate ) ──
            Transition("read", "ranked", "evidence_checked",
                       effect=self._ef_read_and_evaluate),

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
        ictx.instance_data["max_retries"] = int(
            ictx.args.get("max_retries", DEFAULT_QUALITY_MAX_RETRIES)
        )
        ictx.instance_data["attempt"] = 0
        ictx.instance_data["timings"] = {}
        return EffectOutput(message="问题无需检索，可直接回答。")

    async def _ef_start_init(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        data = new_instance_data(
            query=ictx.args.get("query", ""),
            source=ictx.args.get("source", "auto"),
            filters=ictx.args.get("filters"),
            max_retries=int(ictx.args.get("max_retries", DEFAULT_QUALITY_MAX_RETRIES)),
            enable_evaluation=_coerce_bool(ictx.args.get("enable_evaluation"), default=True),
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
                max_retries=int(ictx.args.get("max_retries", DEFAULT_QUALITY_MAX_RETRIES)),
                enable_evaluation=_coerce_bool(ictx.args.get("enable_evaluation"), default=True),
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
                "doc_queries": [],
            }
            ictx.instance_data["timings"]["rewrite_ms"] = int((time.perf_counter() - t0) * 1000)
            return None

        emit_progress(
            ctx,
            tool="query_rewrite",
            status="pending",
            extra={"input": query},
        )

        current_user_input = str(ictx.args.get("current_user_input") or query).strip()
        previous_rag_context = (
            ictx.args.get("previous_rag_context")
            if isinstance(ictx.args.get("previous_rag_context"), dict)
            else {}
        )
        result = await _rewrite_run(
            args={
                "query": query,
                "current_user_input": current_user_input,
                "previous_rag_context": previous_rag_context,
            },
            ctx=ctx,
            clients=self._clients,
        )

        raw_intent = result.get("intent", "rag")
        intent = raw_intent if raw_intent in ("web_search", "direct") else "rag"
        keywords = result.get("keywords", [])
        sub_queries = result.get("sub_queries", [])
        rewrite_query = result.get("rewrite_query", "")
        bm25_query = result.get("bm25_query", "")
        doc_query = result.get("doc_query", "")
        analysis_query = result.get("analysis_query", "")
        doc_queries = result.get("doc_queries", [])
        resolved_query = str(result.get("resolved_query") or query).strip()
        continues_previous_rag = bool(
            previous_rag_context and result.get("continues_previous_rag")
        )
        reuse_previous_documents = bool(
            continues_previous_rag and result.get("reuse_previous_documents")
        )
        if intent == "rag" and resolved_query:
            # 后续质量评估和答案生成统一使用自包含问题；续问场景下它是
            # “上一轮任务 + 本轮新增要求”，新问题场景下则是本轮规范化任务。
            ictx.instance_data["query"] = resolved_query

        # 如果 LLM 没传 source 但 intent=web_search，设置 source=web
        if ictx.instance_data.get("source") == "auto" and intent == "web_search":
            ictx.instance_data["source"] = "web"

        ictx.instance_data["rewrite"] = {
            "intent": intent,
            "keywords": keywords,
            "sub_queries": sub_queries,
            "rewrite_query": rewrite_query,
            "bm25_query": bm25_query,
            "doc_query": doc_query,
            "analysis_query": analysis_query,
            "doc_queries": doc_queries,
            "resolved_query": resolved_query,
            "continues_previous_rag": continues_previous_rag,
            "reuse_previous_documents": reuse_previous_documents,
        }
        if intent == "rag":
            # 补搜前提应是来源/材料限制，而不是需要得出的分析结论。
            previous_context = (
                previous_rag_context.get("context")
                if isinstance(previous_rag_context.get("context"), dict)
                else {}
            )
            previous_retrieval = (
                previous_context.get("retrieval_context")
                if isinstance(previous_context.get("retrieval_context"), dict)
                else {}
            )
            premise = (
                previous_context.get("premise")
                or previous_retrieval.get("premise")
                or doc_query
                or rewrite_query
                or resolved_query
            ) if continues_previous_rag else (doc_query or rewrite_query or resolved_query)
            retrieval_context = {
                "premise": premise,
                "original_query": resolved_query,
                "doc_query": doc_query,
                "doc_queries": list(doc_queries),
                "analysis_query": analysis_query,
                "keywords": list(keywords),
                "conversation_summary": resolved_query,
            }
            if reuse_previous_documents:
                previous_documents = previous_rag_context.get("selected_documents") or []
                locked_documents = _compact_document_scope([
                    item for item in previous_documents if isinstance(item, dict)
                ])
                if locked_documents:
                    retrieval_context["selected_documents"] = locked_documents
                    retrieval_context["selected_knowledge_ids"] = [
                        item["knowledge_id"] for item in locked_documents
                    ]
                    ictx.instance_data["selected_documents"] = locked_documents
                    ictx.instance_data["selected_knowledge_ids"] = [
                        item["knowledge_id"] for item in locked_documents
                    ]
                    ictx.instance_data["reuse_selected_documents"] = True
            ictx.instance_data["retrieval_context"] = retrieval_context

        rewrite_log = {
            "intent": intent,
            "current_user_input": current_user_input,
            "resolved_query": resolved_query,
            "continues_previous_rag": continues_previous_rag,
            "reuse_previous_documents": reuse_previous_documents,
            "rewrite_query": rewrite_query,
            "bm25_query": bm25_query,
            "doc_query": doc_query,
            "doc_queries": doc_queries,
            "analysis_query": analysis_query,
        }
        emit_progress(
            ctx,
            tool="query_rewrite",
            status="done",
            extra={"input": query, "result": rewrite_log},
        )
        logger.info(
            "[RAG][rewrite] %s",
            json.dumps({"input": query, "result": rewrite_log}, ensure_ascii=False),
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

        if ictx.instance_data.pop("reuse_selected_documents", False):
            selected_documents = ictx.instance_data.get("selected_documents") or []
            kid_list = [
                str(document.get("knowledge_id") or "")
                for document in selected_documents
                if str(document.get("knowledge_id") or "")
            ]
            ictx.instance_data["selected_knowledge_ids"] = kid_list
            ictx.instance_data["document_read_mode"] = "global_chunk_rerank"
            ictx.instance_data["candidates"] = []
            reused_log = [
                {
                    "rank": rank,
                    "title": document.get("knowledge_title") or document.get("file_name", ""),
                    "kid": document.get("knowledge_id", ""),
                    "score": round(float(document.get("score", 0.0) or 0.0), 4),
                }
                for rank, document in enumerate(selected_documents, 1)
            ]
            logger.info(
                "[RAG][select] %s",
                json.dumps(
                    {
                        "queries": rewrite.get("doc_queries") or [],
                        "selected": reused_log,
                        "selected_count": len(reused_log),
                        "reused_from_rag_state": True,
                    },
                    ensure_ascii=False,
                ),
            )
            emit_progress(
                ctx,
                tool="document_select",
                status="done",
                count=len(kid_list),
                extra={
                    "reused_locked_document_scope": True,
                    "retry_queries": rewrite.get("sub_queries") or [],
                    "documents": [
                        {
                            "knowledge_id": document.get("knowledge_id", ""),
                            "title": document.get("knowledge_title") or document.get("file_name", ""),
                        }
                        for document in selected_documents
                    ],
                },
            )
            ictx.instance_data["timings"]["branch_recall_ms"] = int(
                (time.perf_counter() - t0) * 1000
            )
            return None

        sd_args: dict[str, Any] = {
            "query": doc_query,
            "bm25_query": rewrite.get("bm25_query") or doc_query,
            "top_k": int(os.getenv("RAG_DOCUMENT_SELECT_TOP_K", "20")),
            "recall_top_k": int(os.getenv("RAG_DOCUMENT_SELECT_RECALL_TOP_K", "60")),
        }
        if filters.get("rag_filter"):
            sd_args["rag_filter"] = filters["rag_filter"]

        emit_progress(
            ctx,
            tool="document_select",
            status="pending",
            extra={
                "query": doc_query,
                "doc_queries": rewrite.get("doc_queries") or [],
            },
        )

        try:
            result = await _select_run(clients=self._clients, args=sd_args, ctx=ctx)
        except Exception as e:
            result = {"error": f"{type(e).__name__}: {e}"}
            logger.warning(
                "[RAG WF] rag select_documents raised: %s: %s",
                type(e).__name__,
                e,
            )

        # `_select_run` 内层 catch 后会返回 {"error": ...} 而不抛，早期这里会把失败
        # 当成“选中零文档”默默吃掉，让 chunk_recall 走 _no_selected_docs → retry。
        # 统一在这里识别 error 分支，清洗 selected_* 并 emit error 事件，与上方 except 分支合并。
        if isinstance(result, dict) and result.get("error"):
            err_msg = str(result.get("error") or "")
            logger.warning("[RAG WF] rag select_documents failed: %s", err_msg)
            ictx.instance_data["selected_knowledge_ids"] = []
            ictx.instance_data["candidates"] = []
            ictx.instance_data["timings"]["branch_recall_ms"] = int((time.perf_counter() - t0) * 1000)
            emit_progress(
                ctx,
                tool="document_select",
                status="error",
                extra={"query": doc_query, "error": err_msg},
            )
            return None

        kid_list = result.get("knowledge_ids", [])
        ictx.instance_data["selected_knowledge_ids"] = kid_list
        ictx.instance_data["selected_documents"] = result.get("documents", [])
        ictx.instance_data["document_select_trace"] = result.get("document_select_trace") or {}
        retrieval_context = ictx.instance_data.get("retrieval_context") or {}
        current_scope = _compact_document_scope(result.get("documents", []))
        quality = ictx.instance_data.get("quality_evaluation") or {}
        if int(ictx.instance_data.get("attempt", 0) or 0) == 0:
            locked_scope = current_scope
        elif quality.get("requires_document_reselection"):
            locked_scope = _compact_document_scope([
                *retrieval_context.get("selected_documents", []),
                *current_scope,
            ])
        else:
            locked_scope = retrieval_context.get("selected_documents", [])
        if locked_scope:
            retrieval_context["selected_documents"] = locked_scope
            retrieval_context["selected_knowledge_ids"] = [
                document["knowledge_id"] for document in locked_scope
            ]
            ictx.instance_data["retrieval_context"] = retrieval_context
        read_mode = "global_chunk_rerank"
        selected_documents = result.get("documents", [])
        ictx.instance_data["document_read_mode"] = read_mode
        ictx.instance_data["candidates"] = []
        emit_progress(
            ctx,
            tool="document_select",
            status="done",
            count=len(kid_list),
            extra={
                "queries": rewrite.get("doc_queries") or [doc_query],
                "selected": [
                    {
                        "rank": rank,
                        "knowledge_id": doc.get("knowledge_id", ""),
                        "title": doc.get("knowledge_title") or doc.get("file_name", ""),
                        "score": doc.get("score", 0.0),
                        "summary_recall_score": (doc.get("document_match_scores") or {}).get("summary_recall", 0.0),
                        "metadata_recall_score": (doc.get("document_match_scores") or {}).get("metadata_recall", 0.0),
                        "query_matches": [
                            {
                                "query": match.get("query", ""),
                                "rank": match.get("rank", 0),
                                "score": match.get("score", 0.0),
                            }
                            for match in doc.get("query_matches", [])
                            if isinstance(match, dict)
                        ],
                    }
                    for rank, doc in enumerate(result.get("documents", []), 1)
                ],
            },
        )
        ictx.instance_data["timings"]["branch_recall_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_chunk_recall_rag(self, ictx: InstanceCtx) -> EffectOutput | None:
        """RAG chunk recall — search chunks inside selected documents."""
        await self._ef_recall_scoped(ictx)
        return None

    def _rag_queries(self, ictx: InstanceCtx) -> list[str]:
        rewrite = ictx.instance_data.get("rewrite") or {}
        if int(ictx.instance_data.get("attempt", 0) or 0) > 0:
            gaps = [str(item).strip() for item in rewrite.get("sub_queries", []) if str(item).strip()]
            if gaps:
                return list(dict.fromkeys(gaps))[:3]
        query = str(rewrite.get("rewrite_query") or ictx.instance_data.get("query", "")).strip()
        return [query] if query else []

    def _bm25_queries(self, ictx: InstanceCtx) -> list[str]:
        queries = self._rag_queries(ictx)
        if int(ictx.instance_data.get("attempt", 0) or 0) > 0 and queries:
            from .stages.rewrite.service import QueryRewriteService

            return [" ".join(QueryRewriteService._tokenize(query)) or query for query in queries]
        rewrite = ictx.instance_data.get("rewrite") or {}
        query = str(rewrite.get("bm25_query") or rewrite.get("rewrite_query") or ictx.instance_data.get("query", "")).strip()
        return [query] if query else []

    async def _ef_recall_scoped(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Run hybrid search scoped by selected knowledge_ids."""
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        emit_progress(ctx, tool="knowledge_search", status="pending")

        rewrite = ictx.instance_data.get("rewrite") or {}
        query = (
            rewrite.get("analysis_query")
            or rewrite.get("rewrite_query")
            or ictx.instance_data.get("query", "")
        )
        queries = self._rag_queries(ictx)

        if not queries:
            ictx.instance_data["candidates"] = []
            emit_progress(ctx, tool="knowledge_search", status="done", count=0)
            return None

        ks_args: dict[str, Any] = {"queries": queries, "bm25_queries": self._bm25_queries(ictx)}

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
        logger.debug(
            "[RAG WF] recall DIAG: ks_args knowledge_ids=%s, raw_results=%d, unique_kids=%d %s, titles=%s",
            ks_args.get("knowledge_ids"), len(raw_results),
            len(raw_kids), raw_kids[:5], raw_titles[:5],
        )
        for sr_dict in raw_results:
            # Convert to Candidate
            selected_doc = next(
                (doc for doc in selected_documents if doc.get("knowledge_id") == sr_dict.get("knowledge_id")),
                {},
            )
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
                recall_score=float(sr_dict.get("score", 0.0) or 0.0),
                document_score=float(selected_doc.get("document_score", selected_doc.get("score", 0.0)) or 0.0),
                summary_score=float(
                    (selected_doc.get("document_match_scores") or {}).get("summary_recall", 0.0) or 0.0
                ),
                document_match_scores=dict(selected_doc.get("document_match_scores") or {}),
            )
            candidates.append(c.to_dict())

        ictx.instance_data["candidates"] = candidates
        emit_progress(ctx, tool="knowledge_search", status="done", count=len(candidates))
        ictx.instance_data["timings"]["recall_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_mark_no_docs(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.debug("[RAG WF] No docs selected, marking insufficient")
        return None

    async def _ef_fusion_rerank_mmr(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Rank phase — rerank, threshold, and MMR order candidates."""
        t0 = time.perf_counter()
        candidates = ictx.instance_data.get("candidates") or []
        queries = self._rag_queries(ictx)

        result = await _rank_run(
            clients=self._clients,
            args={"candidates": candidates, "queries": queries},
            ctx=ictx.session_ctx,
        )

        ictx.instance_data["ranked"] = result.get("ranked", [])
        by_document = _ranked_by_document(ictx.instance_data["ranked"])
        emit_progress(
            ictx.session_ctx,
            tool="rank",
            status="done",
            count=len(ictx.instance_data["ranked"]),
            extra={"by_document": by_document},
        )
        logger.info(
            "[RAG][rerank] %s",
            json.dumps({"by_document": by_document}, ensure_ascii=False),
        )
        ictx.instance_data["timings"]["rank_ms"] = (
            result.get("rank_ms")
            if result.get("rank_ms") is not None
            else int((time.perf_counter() - t0) * 1000)
        )
        return None

    async def _ef_mark_no_candidates(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.debug("[RAG WF] No candidates after recall")
        return None

    async def _ef_read_and_evaluate(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Read ranked chunk anchors into evidence, then run the quality evaluator.

        名字里不再提 parallel —— 实际是 `read → 等 evidence → evaluate` 串行。
        """
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []

        if not ranked:
            evidence = []
            read_stats = {
                "document_read_mode": ictx.instance_data.get("document_read_mode") or "global_chunk_rerank",
            }
        else:
            evidence = await read_ranked_context(
                clients=self._clients,
                ranked=ranked,
                top_k=int(os.getenv("RAG_EVIDENCE_TOP_K", os.getenv("RAG_RANK_TOP_K", "10"))),
            )
            read_stats = {
                "document_read_mode": "mmr_selected_anchor_expansion",
            }
        evidence_pool = self._update_evidence_pool(ictx, evidence)
        quality_evidence = _select_evidence_by_document(
            evidence_pool,
            max_items=max(1, int(os.getenv("RAG_QUALITY_MAX_EVIDENCE_CHUNKS", "15"))),
        )
        answer_evidence = _select_evidence_by_document(
            evidence_pool,
            max_items=max(1, int(os.getenv("RAG_EVIDENCE_MAX_CHUNKS", "30"))),
        )
        read_stats["evidence_pool_size"] = len(evidence_pool)
        read_stats["quality_evidence_count"] = len(quality_evidence)
        read_stats["answer_evidence_count"] = len(answer_evidence)
        ictx.instance_data["evidence"] = answer_evidence

        # Research harnesses already own their goal coverage evaluation.  In
        # that mode RAG should remain a single-pass atomic fact retriever: read
        # once, emit any evidence, and never invoke its internal evaluator or
        # retry loop.
        if not _evaluation_enabled(ictx):
            round_queries = self._rag_queries(ictx)
            quality = {
                "skipped": True,
                "round": 1,
                "score": None,
                "passed": bool(answer_evidence),
                "reason": "已关闭 RAG 质量评估：首轮检索直接结束",
                "executed_queries": round_queries,
                "retry_queries": [],
                "missing_points": [],
                "resolved_points": [],
            }
            ictx.instance_data["quality_evaluation"] = quality
            ictx.instance_data["quality_history"] = [quality]
            ictx.instance_data["gap_ledger"] = {
                "resolved": [], "unresolved": [], "next_queries": [],
                "attempted_queries": round_queries, "retry_stalled": True,
                "requires_document_reselection": False,
            }
            ictx.instance_data["evidence_sufficient"] = bool(answer_evidence)
            ictx.instance_data["retry_stalled"] = True
            read_stats["quality_evaluation"] = "skipped"
            emit_progress(
                ctx,
                tool="quality_evaluate",
                status="skipped",
                extra={"reason": quality["reason"]},
            )
            ictx.instance_data["timings"]["read_ms"] = int((time.perf_counter() - t0) * 1000)
            return None

        round_number = int(ictx.instance_data.get("attempt", 0) or 0) + 1
        max_rounds = int(
            ictx.instance_data.get("max_retries", DEFAULT_QUALITY_MAX_RETRIES)
            or DEFAULT_QUALITY_MAX_RETRIES
        ) + 1
        emit_progress(
            ctx,
            tool="quality_evaluate",
            status="pending",
            extra={"round": round_number, "max_rounds": max_rounds},
        )
        previous_ledger = ictx.instance_data.get("gap_ledger") or {}
        round_queries = self._rag_queries(ictx)
        attempted_queries = list(dict.fromkeys([
            *previous_ledger.get("attempted_queries", []),
            *round_queries,
        ]))
        evaluation_ledger = {
            **previous_ledger,
            "attempted_queries": attempted_queries,
        }
        quality = await _evaluate_run(
            args={
                "query": ictx.instance_data.get("query", ""),
                "query_plan": ictx.instance_data.get("rewrite") or {},
                "retrieval_context": ictx.instance_data.get("retrieval_context") or {},
                "gap_ledger": evaluation_ledger,
                "selected_documents": ictx.instance_data.get("selected_documents") or [],
                "evidence": quality_evidence,
                "round_number": round_number,
                "max_rounds": max_rounds,
            },
            ctx=ctx,
        )
        quality["round"] = round_number
        quality["executed_queries"] = round_queries
        retrieval_context = ictx.instance_data.get("retrieval_context") or {}
        quality["retry_queries"] = _contextualize_retry_queries(
            str(retrieval_context.get("premise") or ""),
            quality.get("retry_queries") or [],
        )
        quality["retry_queries"] = [
            query
            for query in quality["retry_queries"]
            if query not in attempted_queries
        ]
        retry_stalled = not bool(quality.get("passed")) and not quality["retry_queries"]
        quality["retry_stalled"] = retry_stalled
        resolved_points = list(dict.fromkeys([
            *previous_ledger.get("resolved", []),
            *quality.get("resolved_points", []),
        ]))
        gap_ledger = {
            "resolved": resolved_points,
            "unresolved": list(quality.get("missing_points") or []),
            "next_queries": list(quality.get("retry_queries") or []),
            "requires_document_reselection": bool(
                quality.get("requires_document_reselection")
            ),
            "attempted_queries": attempted_queries,
            "retry_stalled": retry_stalled,
            "round": round_number,
        }
        ictx.instance_data["gap_ledger"] = gap_ledger
        ictx.instance_data["quality_evaluation"] = quality
        quality_history = list(ictx.instance_data.get("quality_history") or [])
        quality_history.append(quality)
        ictx.instance_data["quality_history"] = quality_history
        ictx.instance_data["evidence_sufficient"] = bool(quality.get("passed"))
        ictx.instance_data["retry_stalled"] = retry_stalled
        quality_log = {
            "round": round_number,
            "score": quality.get("score", 0.0),
            "passed": bool(quality.get("passed")),
            "task_type": quality.get("task_type", ""),
            "dimensions": quality.get("dimensions") or {},
            "result": quality.get("reason", ""),
            "evidence_count": len(quality_evidence),
            "resolved_count": len(quality.get("resolved_points") or []),
            "missing_count": len(quality.get("missing_points") or []),
            "executed_queries": round_queries,
            "missing_points": quality.get("missing_points") or [],
            "next_queries": quality.get("retry_queries") or [],
            "requires_document_reselection": bool(
                quality.get("requires_document_reselection")
            ),
            "retry_stalled": retry_stalled,
        }
        emit_progress(
            ctx,
            tool="quality_evaluate",
            status="done",
            extra=quality_log,
        )
        logger.info(
            "[RAG][quality] %s",
            json.dumps(quality_log, ensure_ascii=False, default=str),
        )
        emit_progress(
            ctx,
            tool="read",
            status="done",
            count=len(answer_evidence),
            extra={
                "evidence_count": len(answer_evidence),
                "quality": quality_log,
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
        logger.debug(
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
            query_plan=ictx.instance_data.get("rewrite") or {},
            selected_documents=ictx.instance_data.get("selected_documents") or [],
        )

        logger.debug(
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
        self._evidence_pools.pop(ictx.instance_id, None)
        return EffectOutput(
            message=evidence_text,
            extras={
                "user:rag_state": _build_persistent_rag_state(ictx),
                "_valid_kb_refs": refs,
                "_rag_evidence_refs": evidence_refs,
                "_rag_evidence_pack": {
                    "query": ictx.instance_data.get("query", ""),
                    "query_plan": {
                        "original": ictx.instance_data.get("query", ""),
                        "rewrite": (ictx.instance_data.get("rewrite") or {}).get("rewrite_query", ""),
                        "bm25_query": (ictx.instance_data.get("rewrite") or {}).get("bm25_query", ""),
                        "doc_query": (ictx.instance_data.get("rewrite") or {}).get("doc_query", ""),
                        "doc_queries": (ictx.instance_data.get("rewrite") or {}).get("doc_queries", []),
                        "analysis_query": (ictx.instance_data.get("rewrite") or {}).get("analysis_query", ""),
                        "sub_queries": (ictx.instance_data.get("rewrite") or {}).get("sub_queries", []),
                        "intent": (ictx.instance_data.get("rewrite") or {}).get("intent", ""),
                        "retrieval_context": ictx.instance_data.get("retrieval_context") or {},
                    },
                    "document_select_trace": ictx.instance_data.get("document_select_trace") or {},
                    "selected_documents": ictx.instance_data.get("selected_documents") or [],
                    "ranked_chunks": ictx.instance_data.get("ranked") or [],
                    "evidence": evidence_items,
                    "route": ictx.instance_data.get("route"),
                    "document_read_mode": ictx.instance_data.get("document_read_mode") or "global_chunk_rerank",
                    "document_read_plan": ictx.instance_data.get("document_read_plan", []),
                    "document_read_stats": ictx.instance_data.get("document_read_stats", {}),
                    "scope_count": ictx.instance_data.get("scope_count", 0),
                    "references": refs,
                    "evidence_count": len(ictx.instance_data.get("evidence", [])),
                    "quality_evaluation": ictx.instance_data.get("quality_evaluation") or {},
                    "quality_history": ictx.instance_data.get("quality_history") or [],
                    "gap_ledger": ictx.instance_data.get("gap_ledger") or {},
                    "timings": ictx.instance_data.get("timings", {}),
                },
                "_rag_references": refs,
            },
        )

    async def _ef_emit_partial_references(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Retry exhausted but has partial results — emit what we have."""
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []
        partial_evidence = (
            ictx.instance_data.get("evidence")
            or ranked[:5]
        )

        refs: list[dict[str, Any]] = []
        for item in partial_evidence:
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
            partial_evidence,
            refs,
            query_plan=ictx.instance_data.get("rewrite") or {},
            selected_documents=ictx.instance_data.get("selected_documents") or [],
        )

        evidence_refs = [
            {
                "chunk_id": ev.get("chunk_id", ev.get("id", "")),
                "doc_title": ev.get("knowledge_title", ""),
                "knowledge_id": ev.get("knowledge_id", ""),
            }
            for ev in partial_evidence
        ]
        quality = ictx.instance_data.get("quality_evaluation") or {}
        missing_points = quality.get("missing_points") or []
        quality_note = str(quality.get("reason") or "质量评估未通过").strip()
        if missing_points:
            quality_note += "；仍缺少：" + "；".join(str(item) for item in missing_points)
        self._evidence_pools.pop(ictx.instance_id, None)
        return EffectOutput(
            message=f"[已达到最大检索轮数，以下为部分结果]\n评估说明：{quality_note}\n\n{evidence_text}",
            extras={
                "user:rag_state": _build_persistent_rag_state(ictx),
                "_valid_kb_refs": refs,
                "_rag_evidence_refs": evidence_refs,
                "_rag_evidence_pack": {
                    "query": ictx.instance_data.get("query", ""),
                    "query_plan": {
                        "original": ictx.instance_data.get("query", ""),
                        "rewrite": (ictx.instance_data.get("rewrite") or {}).get("rewrite_query", ""),
                        "bm25_query": (ictx.instance_data.get("rewrite") or {}).get("bm25_query", ""),
                        "doc_query": (ictx.instance_data.get("rewrite") or {}).get("doc_query", ""),
                        "doc_queries": (ictx.instance_data.get("rewrite") or {}).get("doc_queries", []),
                        "analysis_query": (ictx.instance_data.get("rewrite") or {}).get("analysis_query", ""),
                        "sub_queries": (ictx.instance_data.get("rewrite") or {}).get("sub_queries", []),
                        "intent": (ictx.instance_data.get("rewrite") or {}).get("intent", ""),
                        "retrieval_context": ictx.instance_data.get("retrieval_context") or {},
                    },
                    "document_select_trace": ictx.instance_data.get("document_select_trace") or {},
                    "selected_documents": ictx.instance_data.get("selected_documents") or [],
                    "ranked_chunks": ictx.instance_data.get("ranked") or [],
                    "evidence": partial_evidence,
                    "route": ictx.instance_data.get("route"),
                    "document_read_mode": ictx.instance_data.get("document_read_mode") or "global_chunk_rerank",
                    "document_read_plan": ictx.instance_data.get("document_read_plan", []),
                    "document_read_stats": ictx.instance_data.get("document_read_stats", {}),
                    "scope_count": ictx.instance_data.get("scope_count", 0),
                    "references": refs,
                    "evidence_count": len(partial_evidence),
                    "partial": True,
                    "quality_evaluation": ictx.instance_data.get("quality_evaluation") or {},
                    "quality_history": ictx.instance_data.get("quality_history") or [],
                    "gap_ledger": ictx.instance_data.get("gap_ledger") or {},
                    "timings": ictx.instance_data.get("timings", {}),
                },
                "_rag_references": refs,
            },
        )

    async def _ef_expand_retry(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Retry only the gaps returned by evidence quality evaluation."""
        ictx.instance_data["attempt"] = ictx.instance_data.get("attempt", 0) + 1

        quality = ictx.instance_data.get("quality_evaluation") or {}
        retrieval_context = ictx.instance_data.get("retrieval_context") or {}
        locked_documents = list(retrieval_context.get("selected_documents") or [])
        reuse_locked_scope = bool(locked_documents) and not quality.get(
            "requires_document_reselection",
            False,
        )

        # Reset downstream state for fresh run
        ictx.instance_data["selected_documents"] = locked_documents if reuse_locked_scope else []
        ictx.instance_data["selected_knowledge_ids"] = (
            [document["knowledge_id"] for document in locked_documents]
            if reuse_locked_scope
            else []
        )
        ictx.instance_data["reuse_selected_documents"] = reuse_locked_scope
        ictx.instance_data["candidates"] = []
        ictx.instance_data["ranked"] = []
        ictx.instance_data["evidence"] = []
        ictx.instance_data["evidence_sufficient"] = False
        ictx.instance_data["references"] = []
        ictx.instance_data["allow_full_scope_recall"] = False

        gap_ledger = ictx.instance_data.get("gap_ledger") or {}
        retry_queries = [
            item.strip()
            for item in (gap_ledger.get("next_queries") or quality.get("retry_queries") or [])
            if isinstance(item, str) and item.strip()
        ]
        retry_queries = _contextualize_retry_queries(
            str(retrieval_context.get("premise") or ""),
            retry_queries,
        )
        rewrite = ictx.instance_data.get("rewrite") or {}
        if retry_queries:
            # 原始文档证据已经保存在任务内 Evidence Pool。补查轮只执行带
            # immutable premise 的缺口 query，避免重复跑首轮检索。
            #
            # 区分两种情形：
            # 1) reuse_locked_scope=True —— 文档已锁定，doc_queries 不再用于 select，
            #    保留原 doc_queries 作为语义参考，只把 sub_queries 换为 retry，
            #    避免 select 内部意外写新性。
            # 2) requires_document_reselection=True —— 需重选文档，两者都换。
            deduped_retry = list(dict.fromkeys(retry_queries))
            rewrite["sub_queries"] = deduped_retry
            if not reuse_locked_scope:
                rewrite["doc_queries"] = deduped_retry
        else:
            fallback_query = (
                rewrite.get("analysis_query")
                or rewrite.get("rewrite_query")
                or ictx.instance_data.get("query", "")
            )
            rewrite["doc_queries"] = [fallback_query] if fallback_query else []
            rewrite["sub_queries"] = [fallback_query] if fallback_query else []
        ictx.instance_data["rewrite"] = rewrite

        logger.debug(
            "[RAG WF] retry attempt=%d reuse_locked_scope=%s missing=%s retry_queries=%s",
            ictx.instance_data["attempt"],
            reuse_locked_scope,
            quality.get("missing_points", []),
            rewrite.get("doc_queries", []),
        )
        return None

    async def _ef_mark_insufficient(self, ictx: InstanceCtx) -> EffectOutput | None:
        logger.debug("[RAG WF] Evidence insufficient, entering retry path")
        return None

    async def _ef_mark_no_evidence(self, ictx: InstanceCtx) -> EffectOutput | None:
        self._evidence_pools.pop(ictx.instance_id, None)
        return EffectOutput(
            message="未找到相关内容。",
            extras={"user:rag_state": _build_persistent_rag_state(ictx)},
        )


# ── Evidence text builder ────────────────────────────────────────────────


def _compact_document_scope(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only fields needed to lock and reuse the first-round document scope."""
    compact: dict[str, dict[str, Any]] = {}
    for document in documents:
        knowledge_id = str(document.get("knowledge_id") or "").strip()
        if not knowledge_id:
            continue
        compact[knowledge_id] = {
            "knowledge_id": knowledge_id,
            "knowledge_base_id": str(document.get("knowledge_base_id") or ""),
            "knowledge_title": str(document.get("knowledge_title") or ""),
            "file_name": str(document.get("file_name") or ""),
            "score": float(document.get("score", 0.0) or 0.0),
            "query_matches": [
                {
                    "query": str(match.get("query") or ""),
                    "rank": int(match.get("rank", 0) or 0),
                    "score": float(match.get("score", 0.0) or 0.0),
                }
                for match in document.get("query_matches", [])
                if isinstance(match, dict)
            ],
            "document_match_scores": dict(document.get("document_match_scores") or {}),
        }
    return list(compact.values())


def _build_persistent_rag_state(ictx: InstanceCtx) -> dict[str, Any]:
    """Persist compact multi-turn RAG context in the single rag_state object."""
    rag_state = dict(read_rag_state(ictx.session_ctx))
    rewrite = ictx.instance_data.get("rewrite") or {}
    retrieval = ictx.instance_data.get("retrieval_context") or {}
    quality = ictx.instance_data.get("quality_evaluation") or {}
    selected_documents = [
        {
            "knowledge_id": document.get("knowledge_id", ""),
            "knowledge_base_id": document.get("knowledge_base_id", ""),
            "knowledge_title": document.get("knowledge_title", ""),
            "file_name": document.get("file_name", ""),
            "score": document.get("score", 0.0),
            "query_matches": document.get("query_matches", []),
        }
        for document in _compact_document_scope(
            ictx.instance_data.get("selected_documents") or []
        )
    ]
    rag_state["context"] = {
        "resolved_query": (
            rewrite.get("resolved_query")
            or retrieval.get("conversation_summary")
            or ictx.instance_data.get("query", "")
        ),
        "premise": retrieval.get("premise", ""),
        "doc_query": rewrite.get("doc_query", ""),
        "doc_queries": list(rewrite.get("doc_queries") or []),
        "analysis_query": rewrite.get("analysis_query", ""),
        "keywords": list(rewrite.get("keywords") or []),
    }
    rag_state["selected_documents"] = selected_documents
    rag_state["last_quality"] = {
        "round": int(quality.get("round", 0) or 0),
        "score": float(quality.get("score", 0.0) or 0.0),
        "passed": bool(quality.get("passed")),
        "resolved_points": list(quality.get("resolved_points") or []),
        "missing_points": list(quality.get("missing_points") or []),
    }
    return rag_state


def _contextualize_retry_queries(premise: str, queries: list[Any]) -> list[str]:
    """Attach the immutable first-round retrieval premise to gap queries."""
    premise = (premise or "").strip()
    result: list[str] = []
    for item in queries:
        if not isinstance(item, str):
            continue
        query = item.strip()
        if not query:
            continue
        contextualized = query if not premise or premise in query else f"{premise} {query}"
        if contextualized not in result:
            result.append(contextualized)
    return result


def _merge_evidence_rounds(
    archived: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge evidence from multiple retrieval rounds by chunk identity."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*archived, *current]:
        identity = str(item.get("chunk_id") or item.get("id") or "").strip()
        if not identity:
            identity = f"{item.get('knowledge_id', '')}:{item.get('chunk_index', '')}:{len(merged)}"
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def _select_evidence_by_document(
    evidence: list[dict[str, Any]],
    *,
    max_items: int,
) -> list[dict[str, Any]]:
    """Allocate the answer evidence budget fairly across source documents."""
    if max_items <= 0 or len(evidence) <= max_items:
        return evidence

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        group_key = (
            str(item.get("knowledge_id") or "").strip()
            or str(item.get("knowledge_title") or "").strip()
            or f"{item.get('source', 'unknown')}:{item.get('chunk_id', '')}"
        )
        groups.setdefault(group_key, []).append(item)

    def _priority(item: dict[str, Any]) -> tuple[int, int, float, int]:
        score = max(
            float(item.get("score", 0.0) or 0.0),
            float(item.get("anchor_score", 0.0) or 0.0),
        )
        return (
            -int(item.get("_quality_round", 0) or 0),
            0 if item.get("is_anchor") else 1,
            -score,
            int(item.get("chunk_index", 0) or 0),
        )

    queues = [sorted(items, key=_priority) for items in groups.values()]
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < max_items:
        added = False
        for queue in queues:
            if offset < len(queue):
                selected.append(queue[offset])
                added = True
                if len(selected) >= max_items:
                    break
        if not added:
            break
        offset += 1
    return selected


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
    *,
    query_plan: dict[str, Any] | None = None,
    selected_documents: list[dict[str, Any]] | None = None,
) -> str:
    """Build LLM-facing evidence pack text.

    证据以 [1]、[2] 编号格式展示，LLM 在回答中用 [N] 引用，
    final_answer 负责把 [N] 映射为真实 chunk_id 生成 <kb> 标签。
    """
    lines = [
        "=== RAG 检索结果 ===",
        f"当前日期: {time.strftime('%Y-%m-%d')}",
        f"查询: {query}",
        f"找到 {len(evidence)} 条证据，{len(references)} 条引用",
        "回答约束: 只能使用下方证据原文中的事实、数字和结论；不得用常识或记忆补全。",
        "推断约束: 不得使用“预计、推测、可能仍会、初步判断”等措辞补足缺失数据；趋势和优劣结论必须由跨期事实直接支撑。",
        "体裁约束: 证据能支撑分析时，直接给出结论、数据和对比，不得写成“关于 XX 的说明”一类免责体。数据边界、口径差异或未覆盖项只能在文末以“注:”开头的一句话简短提及，不得作为章节标题、小节或大段落展开。",
        "引用方式: 在对应文字末尾用 [N] 标注证据编号（如 [1]、[2]），同一来源复用同一编号。禁止使用 <kb> 等其它引用格式。",
        "",
    ]

    plan_queries = [
        item.strip()
        for item in (query_plan or {}).get("doc_queries", [])
        if isinstance(item, str) and item.strip()
    ]
    if plan_queries:
        lines.append("文档检索计划: " + "；".join(plan_queries))
    selected_titles = [
        str(document.get("knowledge_title") or document.get("file_name") or "").strip()
        for document in selected_documents or []
        if str(document.get("knowledge_title") or document.get("file_name") or "").strip()
    ]
    if selected_titles:
        lines.append("本次选中文档: " + "；".join(dict.fromkeys(selected_titles)))
    if plan_queries or selected_titles:
        lines.append("")

    # 构建 kid → title 映射
    kid_title_map: dict[str, str] = {}
    for ref in references:
        kid = ref.get("knowledge_id", "")
        title = ref.get("doc_title", "")
        if kid and title:
            kid_title_map[kid] = title

    max_evidence = max(1, int(os.getenv("RAG_EVIDENCE_MAX_CHUNKS", "30")))
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
