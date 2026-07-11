"""语义搜索 stage —— Milvus 向量 + BM25 hybrid 检索。

模块级 ``run()`` 是 workflow 内部 operator 入口，返回结构化候选结果。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag._services.scope_adapter import (
    RecallScope,
    scope_plan_from_filters_or_context,
)
from egis_agent_plugins.core.service.base import RetrieverType
from egis_agent_plugins.core.flows.rag.stages.recall.collections import (
    group_by_collection,
    group_by_collection_from_names,
)
from egis_agent_plugins.core.flows.rag.filters import (
    ResolvedFilters,
    resolve_filters,
)
logger = logging.getLogger(__name__)


# ── Result dataclass ───────────────────────────────────────────────────────


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    content: str
    chunk_id: str
    knowledge_id: str
    knowledge_base_id: str
    score: float
    knowledge_title: str = ""
    chunk_index: int = 0
    source_query: str = ""
    query_type: str = "vector"

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
            "source_query": self.source_query,
            "query_type": self.query_type,
        }


# ── Helpers ────────────────────────────────────────────────────────────────


def _ensure_str_list(v) -> list[str] | None:
    """LLM 有时传字符串而不是数组，做防御性规范化。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        if v.startswith("["):
            try:
                import json as _json
                parsed = _json.loads(v)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if isinstance(x, str) and x.strip() and len(x.strip()) > 1] or None
            except Exception:
                pass
        return [v]
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if not isinstance(x, str):
                continue
            x = x.strip()
            if not x or len(x) <= 1:
                continue
            if x.startswith("["):
                try:
                    import json as _json
                    parsed = _json.loads(x)
                    if isinstance(parsed, list):
                        out.extend(str(i).strip() for i in parsed if isinstance(i, str) and i.strip() and len(i.strip()) > 1)
                        continue
                except Exception:
                    pass
            out.append(x)
        return out or None
    return None


def _build_kb_meta_groups(
    clients: RAGClients,
    resolved: ResolvedFilters,
) -> dict[str, list[str]]:
    """根据解析后的 KB 元数据生成 collection 分组。"""
    if resolved.kb_metas:
        cfg = clients.milvus._config
        return group_by_collection(
            resolved.kb_metas,
            personal_collection=cfg.personal_collection,
            public_collection=cfg.public_collection,
        )
    default_collection = clients.milvus._config.personal_collection
    return {default_collection: list(resolved.kb_ids or [])}


def _collection_groups_for_scope(
    clients: RAGClients,
    resolved: ResolvedFilters,
    scope: RecallScope,
) -> dict[str, list[str]] | None:
    """Return collection routing groups for one scope."""
    if not scope.kb_id:
        return None
    metas = [meta for meta in resolved.kb_metas if meta.id == scope.kb_id]
    if metas:
        cfg = clients.milvus._config
        return group_by_collection(
            metas,
            personal_collection=cfg.personal_collection,
            public_collection=cfg.public_collection,
        )
    default_collection = clients.milvus._config.personal_collection
    return {default_collection: [scope.kb_id]}


async def _resolve_collection_groups(
    clients: RAGClients,
    resolved: ResolvedFilters,
) -> dict[str, list[str]] | None:
    """优先按 knowledge.collection_name 路由 selected documents。"""
    if not resolved.knowledge_ids:
        return None

    try:
        await clients.postgres.connect()
        knowledges = await clients.postgres.get_knowledges_by_ids(resolved.knowledge_ids)
    except Exception as e:
        logger.warning("[KnowledgeSearch] resolve knowledge collections failed: %s", e)
        return None

    collection_map: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    missing_collection: list[str] = []
    for knowledge in knowledges:
        if knowledge.collection_name:
            pair = (knowledge.collection_name, knowledge.knowledge_base_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                collection_map.setdefault(knowledge.collection_name, []).append(
                    knowledge.knowledge_base_id
                )
        else:
            missing_collection.append(knowledge.id)

    groups = group_by_collection_from_names(collection_map)
    if groups:
        logger.debug(
            "[KnowledgeSearch] routed by knowledge.collection_name: %s",
            {k: len(v) for k, v in groups.items()},
        )
        if missing_collection:
            logger.warning(
                "[KnowledgeSearch] %d selected docs missing collection_name: %s",
                len(missing_collection), missing_collection[:5],
            )
        return groups

    if missing_collection:
        logger.warning(
            "[KnowledgeSearch] selected docs have no collection_name; using scope collection routing: %s",
            missing_collection[:5],
        )
    return None


def _milvus_search_with_embedding(
    clients: RAGClients,
    *,
    query: str,
    bm25_query: str,
    query_embedding: list[float],
    resolved: ResolvedFilters,
    collection_groups: dict[str, list[str]] | None,
    filter_expr: str | None = None,
    top_k: int,
) -> list[SearchResult]:
    """同步 Milvus hybrid 检索。"""
    try:
        kb_meta_groups = collection_groups or _build_kb_meta_groups(clients, resolved)

        search_results = clients.milvus.search_across_collections(
            kb_meta_groups=kb_meta_groups,
            query_embedding=query_embedding,
            query_text=bm25_query,
            retriever_type=RetrieverType.HYBRID,
            knowledge_ids=resolved.knowledge_ids or None,
            tag_ids=resolved.tag_ids or None,
            filter_expr=filter_expr,
            top_k=top_k * 2,
        )
        return [
            SearchResult(
                id=r.id, content=r.content, chunk_id=r.chunk_id,
                knowledge_id=r.knowledge_id, knowledge_base_id=r.knowledge_base_id,
                score=r.score, source_query=query, query_type="hybrid",
            )
            for r in search_results
        ]
    except Exception as e:
        logger.error("[KnowledgeSearch] Single query search failed: %s", e)
        return []


def _deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduplicated: list[SearchResult] = []
    for r in results:
        if r.chunk_id in seen:
            continue
        seen.add(r.chunk_id)
        deduplicated.append(r)
    return deduplicated


def _scope_with_selected_documents(
    scope: RecallScope,
    selected_documents: list[dict[str, Any]],
) -> RecallScope:
    """Narrow a UI/tag scope to the documents selected by the summary stage.

    Explicit file selections are kept. Once select_documents has produced
    documents for a scope, tag selectors should not expand recall back to the
    whole tag; recall should read selected docs plus explicitly selected files.
    """
    selected_ids = [
        str(doc.get("knowledge_id", "")).strip()
        for doc in selected_documents
        if str(doc.get("knowledge_id", "")).strip()
        and (not scope.kb_id or doc.get("knowledge_base_id") == scope.kb_id)
    ]
    if not selected_ids:
        return scope
    return RecallScope(
        kb_id=scope.kb_id,
        kb_name=scope.kb_name,
        tag_ids=[],
        knowledge_ids=list(dict.fromkeys([*scope.knowledge_ids, *selected_ids])),
        file_names=list(scope.file_names),
        source=scope.source,
    )


async def _fill_knowledge_titles(clients: RAGClients, results: list[SearchResult]) -> None:
    """填充 knowledge title 并过滤孤儿结果。"""
    try:
        await clients.postgres.connect()

        knowledge_ids = list({r.knowledge_id for r in results if r.knowledge_id})
        if not knowledge_ids:
            return

        knowledges = await clients.postgres.get_knowledges_by_ids(knowledge_ids)
        knowledge_map = {k.id: k for k in knowledges}

        for r in results:
            if r.knowledge_id in knowledge_map:
                r.knowledge_title = knowledge_map[r.knowledge_id].title

        before = len(results)
        results[:] = [r for r in results if r.knowledge_id in knowledge_map]
        dropped = before - len(results)
        if dropped > 0:
            logger.debug("[KnowledgeSearch] 过滤 %d 条孤儿结果（PG 无对应 knowledge 记录）", dropped)
    except Exception as e:
        logger.warning("[KnowledgeSearch] 填充 knowledge title 失败: %s", e)


def _format_empty_output(queries: list[str], kb_ids: list[str]) -> str:
    return "\n".join([
        "=== 搜索结果 ===",
        f"未找到相关内容（搜索了 {len(kb_ids) if kb_ids else 0} 个知识库）",
        "",
        "=== 下一步建议 ===",
        "- 如果启用了网络搜索，可以尝试网络搜索",
        "- 尝试使用不同的查询词",
        "- 检查知识库是否包含相关内容",
    ])


def _format_output(results: list[SearchResult], queries: list[str], kb_ids: list[str]) -> str:
    lines = [
        "=== 搜索结果 ===",
        f"找到 {len(results)} 条相关结果",
        "",
    ]
    kb_counts: dict[str, int] = {}
    for r in results:
        title = r.knowledge_title or r.knowledge_id
        kb_counts[title] = kb_counts.get(title, 0) + 1

    lines.append("知识库覆盖:")
    for title, count in kb_counts.items():
        lines.append(f"  - {title}: {count} 条结果")

    lines.append("")
    lines.append("=== 详细结果 ===")
    lines.append("")

    current_kb = ""
    for i, r in enumerate(results):
        if r.knowledge_title != current_kb:
            current_kb = r.knowledge_title
            if i > 0:
                lines.append("")
            lines.append(f"[来源文档: {current_kb}]")

        content = r.content
        snippet = content[:150] + ("..." if len(content) > 150 else "")
        lines.append(
            f"\n结果 #{i + 1}:\n"
            f"  [knowledge_id: {r.knowledge_id}] [chunk_id: {r.id}]\n"
            f"  摘要: {snippet}"
        )

    if len(results) > 10:
        lines.append("")
        lines.append("提示: 结果较多，workflow 会按命中块自动读取连续上下文。")

    return "\n".join(lines)


# ── Stage entrypoint ───────────────────────────────────────────────────────


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Knowledge-search 业务核心。返回 dict 含 ``results`` / ``count`` / ``summary`` 等字段。"""
    queries = args.get("queries", [])

    scope_plan = scope_plan_from_filters_or_context(args, ctx)
    scoped_kb_ids = scope_plan.flat_kb_ids() if scope_plan.has_scopes else []
    selected_documents = args.get("selected_documents") or []
    selected_knowledge_ids = _ensure_str_list(args.get("knowledge_ids") or None) or [
        str(doc.get("knowledge_id", "")).strip()
        for doc in selected_documents
        if str(doc.get("knowledge_id", "")).strip()
    ]

    kb_ids_raw = scoped_kb_ids or clients.default_kb_ids
    kb_ids_in = _ensure_str_list(kb_ids_raw) or kb_ids_raw

    if not queries:
        return {"error": "queries 参数不能为空"}
    max_queries = max(1, int(os.getenv("RAG_CHUNK_RECALL_MAX_QUERIES", "12")))
    queries = queries[:max_queries]

    # 名称 → ID 解析
    _t_resolve = time.perf_counter()
    try:
        await clients.postgres.connect()
        resolved = await resolve_filters(
            clients.postgres,
            kb_ids=kb_ids_in,
            knowledge_ids=selected_knowledge_ids,
        )
    except Exception as e:
        logger.error("[KnowledgeSearch] resolve_filters failed: %s", e)
        return {"error": f"过滤条件解析失败: {e}"}
    logger.debug("[KS] stage=resolve_filters cost_ms=%d", int((time.perf_counter() - _t_resolve) * 1000))

    logger.debug(
        "[KnowledgeSearch] queries=%s, kb_ids=%s, tag_ids=%s, knowledge_ids=%s",
        queries, resolved.kb_ids, resolved.tag_ids, resolved.knowledge_ids,
    )

    try:
        effective_queries = [q for q in queries if q and q.strip()]
        if not effective_queries:
            return {"error": "queries 全为空"}
        recall_queries = effective_queries
        bm25_queries_raw = args.get("bm25_queries") or []
        bm25_queries = [str(item or "").strip() for item in bm25_queries_raw] if isinstance(bm25_queries_raw, list) else []
        if len(bm25_queries) != len(recall_queries):
            bm25_queries = list(recall_queries)

        _t_embed = time.perf_counter()
        try:
            embeddings = await clients.embedding.embed_queries(recall_queries)
        except Exception as e:
            logger.error("[KnowledgeSearch] embed_queries failed: %s", e)
            return {"error": f"embedding 失败: {e}"}
        logger.debug("[KS] stage=embed cost_ms=%d", int((time.perf_counter() - _t_embed) * 1000))

        scopes = scope_plan.scopes if scope_plan.has_scopes else [RecallScope()]
        if not scopes:
            scopes = [RecallScope()]

        scopes = [
            _scope_with_selected_documents(scope, selected_documents)
            for scope in scopes
        ]
        selected_collection_groups = await _resolve_collection_groups(clients, resolved)

        # 多 query 并发 Milvus 检索
        _t_milvus = time.perf_counter()
        default_concurrency = os.getenv("RAG_RETRIEVAL_CONCURRENCY", "6")
        sem = asyncio.Semaphore(max(
            1,
            int(os.getenv("RAG_SCOPE_RECALL_CONCURRENCY", default_concurrency)),
        ))

        async def _search_scope_query(scope: RecallScope, q: str, bm25_q: str, emb: list[float]) -> list[SearchResult]:
            scope_groups = _collection_groups_for_scope(clients, resolved, scope)
            collection_groups = scope_groups or selected_collection_groups
            filter_expr = scope.to_filter_expr(include_enabled=True)
            scope_resolved = ResolvedFilters(
                kb_ids=[scope.kb_id] if scope.kb_id else resolved.kb_ids,
                kb_metas=[
                    meta for meta in resolved.kb_metas
                    if not scope.kb_id or meta.id == scope.kb_id
                ],
                tag_ids=list(scope.tag_ids),
                knowledge_ids=list(scope.knowledge_ids),
            )
            async with sem:
                results = await asyncio.to_thread(
                    _milvus_search_with_embedding,
                    clients,
                    query=q,
                    bm25_query=bm25_q,
                    query_embedding=emb,
                    resolved=scope_resolved,
                    collection_groups=collection_groups,
                    filter_expr=filter_expr,
                    top_k=top_k,
                )
            for item in results:
                item.query_type = f"{item.query_type}:scoped"
            logger.debug(
                "[KnowledgeSearch] scope=%s query='%s' results=%d filter=%s",
                scope.kb_id or "*",
                q[:40],
                len(results),
                filter_expr,
            )
            return results

        search_tasks = [
            _search_scope_query(scope, q, bm25_q, emb)
            for scope in scopes
            for q, bm25_q, emb in zip(recall_queries, bm25_queries, embeddings)
        ]
        per_query_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        logger.debug("[KS] stage=milvus cost_ms=%d", int((time.perf_counter() - _t_milvus) * 1000))

        all_results: list[SearchResult] = []
        for res in per_query_results:
            if isinstance(res, Exception):
                logger.warning("[KnowledgeSearch] scoped query 检索失败: %s", res)
                continue
            all_results.extend(res)

        if not all_results:
            return {
                "queries": queries,
                "results": [],
                "count": 0,
                "knowledge_base_ids": resolved.kb_ids,
                "tag_ids": resolved.tag_ids,
                "knowledge_ids": resolved.knowledge_ids,
                "scope_count": len(scopes),
                "summary": _format_empty_output(queries, resolved.kb_ids),
            }

        deduplicated = _deduplicate_results(all_results)
        logger.debug("[KnowledgeSearch] 去重后: %d 条", len(deduplicated))

        # Keep a deterministic baseline order for rank stage input.
        deduplicated.sort(key=lambda x: x.score, reverse=True)

        # 填充 title
        _t_fill = time.perf_counter()
        await _fill_knowledge_titles(clients, deduplicated)
        logger.debug("[KS] stage=fill_titles cost_ms=%d", int((time.perf_counter() - _t_fill) * 1000))

        output = _format_output(deduplicated, queries, resolved.kb_ids)
        return {
            "queries": queries,
            "results": [r.to_dict() for r in deduplicated],
            "count": len(deduplicated),
            "knowledge_base_ids": resolved.kb_ids,
            "tag_ids": resolved.tag_ids,
            "knowledge_ids": resolved.knowledge_ids,
            "scope_count": len(scopes),
            "display_type": "search_results",
            "summary": output,
        }
    except Exception as e:
        logger.error("[KnowledgeSearch] 搜索失败: %s", e)
        return {"error": f"搜索失败: {e}"}
