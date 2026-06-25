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
    _is_obvious_no_retrieval,
    _no_candidates,
    _no_selected_docs,
    _retry_exhausted_has_partial,
    _retry_exhausted_no_evidence,
    _route_internal,
    _route_web,
    _web_requested_but_disabled,
)
from .events import emit_progress, emit_references
from .schema import Candidate, Reference, new_instance_data
from .stages.read.stage import run as _read_run
from .stages.rank.stage import run as _rank_run
from .stages.recall.stage import run as _recall_run
from .stages.recall.web import run as _web_run
from .stages.rewrite.stage import run as _rewrite_run
from .stages.select.stage import run as _select_run

# 前端 rag_state 硬覆盖过滤器
from egis_agent_plugins.core.flows.rag.state import read_forced_filters

logger = logging.getLogger(__name__)


# ── Scope helpers ──────────────────────────────────────────────────────────


def _has_doc_scope(filters: dict[str, Any]) -> bool:
    """Whether the user explicitly constrained retrieval to documents/files."""
    return bool(filters.get("knowledge_ids") or filters.get("file_names"))


def _has_kb_or_tag_scope(filters: dict[str, Any]) -> bool:
    """Whether the user constrained retrieval to KB/tag scope."""
    return bool(
        filters.get("knowledge_base_ids")
        or filters.get("kb_names")
        or filters.get("tag_ids")
        or filters.get("tag_names")
    )


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
        "docs_selected",
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
            # ── start: 两条 guarded fork ──
            Transition("start", None, "no_retrieval",
                       guard=_is_obvious_no_retrieval,
                       effect=self._ef_start_no_retrieval),
            Transition("start", None, "rewrite_pending",
                       effect=self._ef_start_init),

            # ── rewrite ──
            Transition("rewrite", "rewrite_pending", "rewritten",
                       effect=self._ef_rewrite),

            # ── route: 三条 guarded fork（顺序关键）──
            Transition("route", "rewritten", "no_evidence",
                       guard=_web_requested_but_disabled,
                       effect=self._ef_route_web_unavailable),
            Transition("route", "rewritten", "recalled",
                       guard=_route_web,
                       effect=self._ef_route_web),
            Transition("route", "rewritten", "docs_selected",
                       guard=_route_internal,
                       effect=self._ef_route_internal),

            # ── recall: 两条 guarded fork ──
            Transition("recall", "docs_selected", "recalled",
                       guard=_has_selected_docs,
                       effect=self._ef_recall_scoped),
            Transition("recall", "docs_selected", "insufficient",
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
            max_retries=int(ictx.args.get("max_retries", 1)),
        )
        ictx.instance_data.update(data)

        # 合并前端 rag_state 硬覆盖过滤器到 filters
        forced_kbs, forced_tags, forced_files = read_forced_filters(ictx.session_ctx)
        filters = ictx.instance_data.setdefault("filters", {}) or {}
        if forced_kbs is not None:
            filters["knowledge_base_ids"] = forced_kbs
        if forced_tags is not None:
            filters["tag_ids"] = forced_tags
        if forced_files is not None:
            filters["knowledge_ids"] = forced_files

        ictx.instance_data["timings"]["start_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_rewrite(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        emit_progress(ctx, tool="query_rewrite", status="pending")

        filters = ictx.instance_data.get("filters") or {}
        result = await _rewrite_run(
            args={
                "query": ictx.instance_data.get("query", ""),
                "pinned_knowledge_ids": filters.get("knowledge_ids") or None,
            },
            ctx=ctx,
            clients=self._clients,
        )

        emit_progress(ctx, tool="query_rewrite", status="done")

        intent = result.get("intent", "kb_search")
        keywords = result.get("keywords", [])
        sub_queries = result.get("sub_queries", [])
        rewrite_query = result.get("rewrite_query", "")

        # 如果 LLM 没传 source 但 intent=web_search，设置 source=web
        if ictx.instance_data.get("source") == "auto" and intent == "web_search":
            ictx.instance_data["source"] = "web"

        ictx.instance_data["rewrite"] = {
            "intent": intent,
            "keywords": keywords,
            "sub_queries": sub_queries,
            "rewrite_query": rewrite_query,
        }

        ictx.instance_data["timings"]["rewrite_ms"] = int((time.perf_counter() - t0) * 1000)
        return None

    async def _ef_route_web_unavailable(self, ictx: InstanceCtx) -> EffectOutput | None:
        ictx.instance_data["route"] = "web_unavailable"
        return EffectOutput(message="Web 搜索服务暂未配置，无法获取实时信息。")

    async def _ef_route_web(self, ictx: InstanceCtx) -> EffectOutput | None:
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        ictx.instance_data["route"] = "web"

        emit_progress(ctx, tool="web_search", status="pending")

        rewrite = ictx.instance_data.get("rewrite") or {}
        query = rewrite.get("rewrite_query") or ictx.instance_data.get("query", "")

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

    async def _ef_route_internal(self, ictx: InstanceCtx) -> EffectOutput | None:
        """Internal route — select documents, then scoped hybrid recall.

        Explicit ``knowledge_ids`` are treated as an already selected scope.
        If the selector is unavailable, the workflow may fall back to full-scope
        recall; if the selector runs and returns no docs, recall is blocked.
        """
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        ictx.instance_data["route"] = "internal"

        rewrite = ictx.instance_data.get("rewrite") or {}
        filters = ictx.instance_data.get("filters") or {}

        explicit_ids = filters.get("knowledge_ids") or []
        if explicit_ids:
            ictx.instance_data["selected_knowledge_ids"] = explicit_ids
            ictx.instance_data["allow_full_scope_recall"] = False
            ictx.instance_data["timings"]["route_ms"] = int((time.perf_counter() - t0) * 1000)
            return None

        sd_args: dict[str, Any] = {
            "query": rewrite.get("rewrite_query")
                     or ictx.instance_data.get("query", ""),
        }
        if filters.get("knowledge_base_ids"):
            sd_args["knowledge_base_ids"] = filters["knowledge_base_ids"]
        if filters.get("kb_names"):
            sd_args["kb_names"] = filters["kb_names"]
        if filters.get("file_names"):
            sd_args["file_names"] = filters["file_names"]
        if filters.get("tag_ids"):
            sd_args["tag_ids"] = filters["tag_ids"]
        if filters.get("tag_names"):
            sd_args["tag_names"] = filters["tag_names"]

        try:
            result = await _select_run(clients=self._clients, args=sd_args, ctx=ctx)
        except Exception as e:
            if _has_doc_scope(filters):
                logger.warning(
                    "[RAG WF] select_documents failed under document scope: %s; block fallback",
                    e,
                )
                ictx.instance_data["selected_knowledge_ids"] = []
                ictx.instance_data["allow_full_scope_recall"] = False
                ictx.instance_data["timings"]["route_ms"] = int((time.perf_counter() - t0) * 1000)
                return None
            logger.warning("[RAG WF] select_documents failed: %s, fallback within configured scope", e)
            ictx.instance_data["selected_knowledge_ids"] = []
            ictx.instance_data["allow_full_scope_recall"] = True
            ictx.instance_data["timings"]["route_ms"] = int((time.perf_counter() - t0) * 1000)
            return None
        kid_list = result.get("knowledge_ids", [])
        ictx.instance_data["selected_knowledge_ids"] = kid_list
        logger.info("[RAG WF] select_documents → %d docs", len(kid_list))

        if kid_list:
            # 有选中文档 → 限定范围检索
            ictx.instance_data["allow_full_scope_recall"] = False
        elif _has_doc_scope(filters):
            logger.info("[RAG WF] select_documents 空结果 + 文档约束存在 → 阻断越界回退")
            ictx.instance_data["allow_full_scope_recall"] = False
        elif _has_kb_or_tag_scope(filters):
            # 0 篇结果 + KB/tag 过滤器 → 允许在同一 KB/tag 范围内回退到 chunk search
            logger.info("[RAG WF] select_documents 空结果 + KB/tag 过滤器存在 → 允许 scoped fallback")
            ictx.instance_data["allow_full_scope_recall"] = True
        else:
            # 0 篇结果且无过滤器 → 保持 retry 设置的全域允许状态
            pass

        ictx.instance_data["timings"]["route_ms"] = int((time.perf_counter() - t0) * 1000)
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
        queries = [query] if query else []
        for sq in sub_queries:
            if sq and sq not in queries:
                queries.append(sq)
        queries = queries[:5]

        if not queries:
            ictx.instance_data["candidates"] = []
            emit_progress(ctx, tool="knowledge_search", status="done", count=0)
            return None

        ks_args: dict[str, Any] = {"queries": queries}

        # Scope by knowledge_ids
        kid_list = ictx.instance_data.get("selected_knowledge_ids") or []
        if kid_list:
            ks_args["knowledge_ids"] = kid_list

        # Pass filters
        filters = ictx.instance_data.get("filters") or {}
        if filters.get("knowledge_base_ids"):
            ks_args["knowledge_base_ids"] = filters["knowledge_base_ids"]
        if filters.get("kb_names"):
            ks_args["kb_names"] = filters["kb_names"]
        if filters.get("tag_ids"):
            ks_args["tag_ids"] = filters["tag_ids"]
        if filters.get("tag_names"):
            ks_args["tag_names"] = filters["tag_names"]
        if filters.get("file_names"):
            ks_args["file_names"] = filters["file_names"]

        candidates: list[dict[str, Any]] = []
        try:
            result = await _recall_run(clients=self._clients, args=ks_args, ctx=ctx)
        except Exception as e:
            logger.warning("[RAG WF] knowledge_search failed: %s", e)
            result = {}
        raw_results = result.get("results", [])
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
        query = rewrite.get("rewrite_query") or ictx.instance_data.get("query", "")
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
        """Deep-read full small docs or continuous windows around reranked chunks.

        Small selected documents are cheap and safest to read in full. Large
        documents are expanded from reranked anchors with a local chunk window;
        this preserves context without replacing child chunks with parent
        chunks or reading unrelated document starts.
        """
        t0 = time.perf_counter()
        ctx = ictx.session_ctx
        ranked = ictx.instance_data.get("ranked") or []

        if not ranked:
            ictx.instance_data["evidence"] = []
            ictx.instance_data["evidence_sufficient"] = False
            return None

        # Take top N matched chunks as anchors.
        top_k = int(os.getenv("RAG_EVIDENCE_TOP_K", "8"))
        top_items = ranked[:top_k]
        evidence: list[dict[str, Any]] = []

        window = max(
            0,
            int(os.getenv("RAG_CHUNK_WINDOW", os.getenv("RAG_DEEP_CHUNK_WINDOW", "10"))),
        )
        small_doc_limit = max(1, int(os.getenv("RAG_SMALL_DOC_CHUNK_LIMIT", "50")))

        anchors_by_kid: dict[str, list[dict[str, Any]]] = {}
        for item in top_items:
            if item.get("source") != "internal":
                continue
            kid = item.get("knowledge_id", "")
            if kid:
                anchors_by_kid.setdefault(kid, []).append(item)

        doc_ids: list[str] = []
        for item in top_items:
            if item.get("source") != "internal":
                continue
            kid = item.get("knowledge_id", "")
            if kid and kid not in doc_ids:
                doc_ids.append(kid)
        for kid in ictx.instance_data.get("selected_knowledge_ids") or []:
            if kid and kid not in doc_ids:
                doc_ids.append(kid)

        doc_counts: dict[str, int] = {}
        pg = getattr(self._clients, "postgres", None)
        if pg is not None and doc_ids:
            try:
                await pg.connect()
                counts = await asyncio.gather(
                    *(pg.get_chunk_count_by_knowledge_id(kid) for kid in doc_ids),
                    return_exceptions=True,
                )
                for kid, count in zip(doc_ids, counts):
                    if isinstance(count, Exception):
                        logger.warning("[RAG WF] chunk count failed kid=%s: %s", kid, count)
                        continue
                    doc_counts[kid] = int(count or 0)
            except Exception as e:
                logger.warning("[RAG WF] chunk count batch failed: %s", e)

        if doc_ids:
            read_specs: list[dict[str, Any]] = []

            for kid in doc_ids:
                total = doc_counts.get(kid)
                anchors = anchors_by_kid.get(kid, [])

                if total is not None and 0 < total <= small_doc_limit:
                    anchor = anchors[0] if anchors else {"knowledge_id": kid}
                    read_specs.append({
                        "mode": "full_small_doc",
                        "anchor": anchor,
                        "anchors": anchors,
                        "knowledge_id": kid,
                        "offset": 0,
                        "limit": small_doc_limit,
                        "anchor_chunk_ids": {
                            a.get("chunk_id", a.get("id", "")) for a in anchors
                            if a.get("chunk_id", a.get("id", ""))
                        },
                    })
                    continue

                if not anchors or window <= 0:
                    continue

                intervals: list[tuple[int, int]] = []
                for anchor in anchors:
                    try:
                        chunk_index = int(anchor.get("chunk_index", 0) or 0)
                    except (TypeError, ValueError):
                        chunk_index = 0
                    start = max(chunk_index - window, 0)
                    end = chunk_index + window
                    if total is not None and total > 0:
                        end = min(end, total - 1)
                    intervals.append((start, end))

                intervals.sort()
                merged: list[tuple[int, int]] = []
                for start, end in intervals:
                    if not merged or start > merged[-1][1] + 1:
                        merged.append((start, end))
                    else:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end))

                anchor_ids = {
                    a.get("chunk_id", a.get("id", "")) for a in anchors
                    if a.get("chunk_id", a.get("id", ""))
                }
                for start, end in merged:
                    window_anchors = []
                    for anchor in anchors:
                        try:
                            anchor_index = int(anchor.get("chunk_index", 0) or 0)
                        except (TypeError, ValueError):
                            anchor_index = 0
                        if start <= anchor_index <= end:
                            window_anchors.append(anchor)
                    read_specs.append({
                        "mode": "anchor_window",
                        "anchor": window_anchors[0] if window_anchors else anchors[0],
                        "anchors": window_anchors or anchors,
                        "knowledge_id": kid,
                        "offset": start,
                        "limit": end - start + 1,
                        "anchor_chunk_ids": anchor_ids,
                    })

            seen_specs: set[tuple[str, int, int]] = set()
            deduped_specs: list[dict[str, Any]] = []
            for spec in read_specs:
                key = (
                    spec["knowledge_id"],
                    int(spec["offset"]),
                    int(spec["limit"]),
                )
                if key in seen_specs:
                    continue
                seen_specs.add(key)
                deduped_specs.append(spec)

            read_tasks: list[tuple[dict[str, Any], Any]] = []
            for spec in deduped_specs:
                read_tasks.append((
                    spec,
                    _read_run(
                        clients=self._clients,
                        args={
                            "knowledge_id": spec["knowledge_id"],
                            "offset": spec["offset"],
                            "limit": spec["limit"],
                        },
                        ctx=ctx,
                    ),
                ))

            results = await asyncio.gather(
                *(task for _item, task in read_tasks),
                return_exceptions=True,
            )
            seen_chunks: set[str] = set()
            for (spec, _task), result in zip(read_tasks, results):
                anchor = spec.get("anchor") or {}
                anchors = spec.get("anchors") or []
                anchor_by_chunk_id = {
                    a.get("chunk_id", a.get("id", "")): a
                    for a in anchors
                    if a.get("chunk_id", a.get("id", ""))
                }
                if isinstance(result, Exception):
                    logger.warning(
                        "[RAG WF] deep read %s failed: %s",
                        spec.get("knowledge_id", ""),
                        result,
                    )
                    continue
                chunks = result.get("chunks", result.get("results", []))
                title = (
                    result.get("knowledge_title")
                    or anchor.get("knowledge_title", "")
                )
                anchor_chunk_ids = spec.get("anchor_chunk_ids") or set()
                for chunk in chunks:
                    chunk_id = chunk.get("chunk_id", chunk.get("id", ""))
                    content = (chunk.get("content") or "").strip()
                    if not chunk_id or chunk_id in seen_chunks or not content:
                        continue
                    seen_chunks.add(chunk_id)
                    evidence_kid = (
                        anchor.get("knowledge_id")
                        or chunk.get("knowledge_id")
                        or spec.get("knowledge_id", "")
                    )
                    is_anchor = chunk_id in anchor_by_chunk_id
                    matched_anchor = anchor_by_chunk_id.get(chunk_id)
                    if not matched_anchor and anchors:
                        try:
                            chunk_index = int(chunk.get("chunk_index", 0) or 0)
                        except (TypeError, ValueError):
                            chunk_index = 0

                        def _anchor_distance(a: dict[str, Any]) -> int:
                            try:
                                return abs(int(a.get("chunk_index", 0) or 0) - chunk_index)
                            except (TypeError, ValueError):
                                return 0

                        matched_anchor = min(anchors, key=_anchor_distance)
                    matched_anchor = matched_anchor or anchor
                    matched_anchor_id = matched_anchor.get(
                        "chunk_id", matched_anchor.get("id", "")
                    )
                    evidence.append({
                        "knowledge_id": evidence_kid,
                        "knowledge_title": title,
                        "chunk_id": chunk_id,
                        "chunk_index": chunk.get("chunk_index", 0),
                        "content": content,
                        "score": matched_anchor.get("score", 0.0) if is_anchor else 0.0,
                        "anchor_score": matched_anchor.get("score", 0.0),
                        "source_query": matched_anchor.get("source_query", ""),
                        "anchor_chunk_id": chunk_id if is_anchor else matched_anchor_id,
                        "anchor_chunk_ids": sorted(anchor_chunk_ids),
                        "is_anchor": is_anchor,
                        "read_mode": spec.get("mode", ""),
                    })

            logger.info(
                "[RAG WF] deep read DIAG: docs=%d counts=%d specs=%d evidence=%d small_limit=%d window=%d",
                len(doc_ids),
                len(doc_counts),
                len(deduped_specs),
                len(evidence),
                small_doc_limit,
                window,
            )

        # Fallback: if deep-read is unavailable/empty, use reranked hit chunks.
        if not evidence:
            for item in top_items:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                chunk_id = item.get("chunk_id", item.get("id", ""))
                evidence.append({
                    "knowledge_id": item.get("knowledge_id", ""),
                    "knowledge_title": item.get("knowledge_title", ""),
                    "chunk_id": chunk_id,
                    "chunk_index": item.get("chunk_index", 0),
                    "content": content,
                    "score": item.get("score", 0.0),
                    "source_query": item.get("source_query", ""),
                    "anchor_chunk_id": chunk_id,
                    "is_anchor": True,
                })

        ictx.instance_data["evidence"] = evidence

        # Assess sufficiency: at least one usable grounding chunk. If a
        # threshold is configured, keep it as an extra quality gate.
        sufficiency_threshold = float(
            os.getenv("RAG_SUFFICIENCY_THRESHOLD", "0.0")
        )
        if sufficiency_threshold > 0:
            high_quality = [
                e for e in evidence
                if e.get("score", 0.0) >= sufficiency_threshold
            ]
            ictx.instance_data["evidence_sufficient"] = bool(high_quality)
        else:
            ictx.instance_data["evidence_sufficient"] = bool(evidence)

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
