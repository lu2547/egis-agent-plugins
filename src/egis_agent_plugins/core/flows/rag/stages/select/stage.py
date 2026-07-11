"""Deterministic document selection over the summary collection.

For one atomic query the stage runs two independent Milvus hybrid searches:

* document summary dense+sparse fields;
* tag/name metadata dense+sparse fields.

The two rankings are fused by document ID with RRF.  There is no filename LLM,
summary reranker, hand-authored weight strategy, or query expansion here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from egis_agent_plugins.core.flows.rag._services.scope_adapter import RecallScope, scope_plan_from_filters_or_context
from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.service.base import MilvusSearchResult, RetrieverType

logger = logging.getLogger(__name__)

SUMMARY_DENSE_FIELD = "embedding"
SUMMARY_SPARSE_FIELD = "content_sparse"
METADATA_DENSE_FIELD = "metadata_embedding"
METADATA_SPARSE_FIELD = "metadata_sparse"
SUMMARY_OUTPUT_FIELDS = ["knowledge_id", "knowledge_base_id", "content", "file_name"]


def _documents(results: list[MilvusSearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "knowledge_id": item.knowledge_id,
            "knowledge_base_id": item.knowledge_base_id,
            "content": item.content,
            "file_name": item.file_name,
            "hybrid_score": float(item.score or 0.0),
        }
        for item in results
        if item.knowledge_id
    ]


def _rrf_merge(
    summary: list[dict[str, Any]],
    metadata: list[dict[str, Any]],
    *,
    query: str,
    rrf_k: int,
    scope: RecallScope,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    route_max = 1.0 / (rrf_k + 1)
    for route, ranking in (("summary", summary), ("metadata", metadata)):
        for rank, raw in enumerate(ranking, 1):
            knowledge_id = str(raw.get("knowledge_id") or "")
            if not knowledge_id:
                continue
            item = merged.setdefault(knowledge_id, {
                **raw,
                "rrf_raw": 0.0,
                "rrf_routes": {"summary": [], "metadata": []},
                "scope": {"kb_id": scope.kb_id, "kb_name": scope.kb_name, "source": scope.source},
            })
            contribution = 1.0 / (rrf_k + rank)
            item["rrf_raw"] += contribution
            item["rrf_routes"][route].append({
                "rank": rank,
                "rrf_score": contribution,
                "hybrid_score": float(raw.get("hybrid_score", 0.0) or 0.0),
            })

    for item in merged.values():
        summary_raw = sum(entry["rrf_score"] for entry in item["rrf_routes"]["summary"])
        metadata_raw = sum(entry["rrf_score"] for entry in item["rrf_routes"]["metadata"])
        summary_score = min(1.0, summary_raw / route_max) if route_max else 0.0
        metadata_score = min(1.0, metadata_raw / route_max) if route_max else 0.0
        document_score = min(1.0, (summary_raw + metadata_raw) / (2.0 * route_max)) if route_max else 0.0
        item["score"] = document_score
        item["document_score"] = document_score
        item["document_match_scores"] = {
            "summary_recall": summary_score,
            "metadata_recall": metadata_score,
            "rrf_raw": float(item["rrf_raw"]),
            "final": document_score,
            "source": "summary_metadata_hybrid_rrf",
        }
        item["initial_recall_components"] = {
            "query": query,
            "rrf_k": rrf_k,
            "routes": item.pop("rrf_routes"),
            "raw_score": item["rrf_raw"],
            "normalized_score": document_score,
        }
    return sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)


def _merge_scopes(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            knowledge_id = str(item.get("knowledge_id") or "")
            current = by_id.get(knowledge_id)
            if knowledge_id and (current is None or float(item.get("score", 0.0)) > float(current.get("score", 0.0))):
                by_id[knowledge_id] = item
    return sorted(by_id.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)


async def _fill_titles(clients: RAGClients, documents: list[dict[str, Any]]) -> None:
    ids = list(dict.fromkeys(str(item.get("knowledge_id") or "") for item in documents if item.get("knowledge_id")))
    if not ids:
        return
    await clients.postgres.connect()
    known = {item.id: item for item in await clients.postgres.get_knowledges_by_ids(ids)}
    documents[:] = [item for item in documents if str(item.get("knowledge_id") or "") in known]
    for item in documents:
        knowledge = known[str(item["knowledge_id"])]
        item["knowledge_title"] = knowledge.title
        item["file_name"] = item.get("file_name") or knowledge.file_name or knowledge.title


def _selected_file_names(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, str]:
    sources: list[Any] = [args.get("rag_filter")]
    if isinstance(ctx, dict):
        for key in ("user:rag_state", "rag_state"):
            value = ctx.get(key)
            if isinstance(value, dict):
                sources.append(value.get("rag_filter"))
    names: dict[str, str] = {}
    for scopes in sources:
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, dict):
                continue
            nested = [scope.get("files", [])]
            nested.extend(tag.get("files", []) for tag in scope.get("tags", []) if isinstance(tag, dict))
            for files in nested:
                for file in files if isinstance(files, list) else []:
                    if isinstance(file, dict) and file.get("id"):
                        names[str(file["id"])] = str(file.get("name") or file.get("file_name") or "")
    return names


def _direct_documents(ids: list[str], names: dict[str, str], scopes: list[RecallScope]) -> list[dict[str, Any]]:
    kb_by_id = {
        knowledge_id: scope.kb_id
        for scope in scopes
        for knowledge_id in scope.knowledge_ids
    }
    return [{
        "knowledge_id": knowledge_id,
        "knowledge_base_id": kb_by_id.get(knowledge_id, ""),
        "knowledge_title": names.get(knowledge_id, ""),
        "file_name": names.get(knowledge_id, ""),
        "score": 1.0,
        "document_score": 1.0,
        "document_match_scores": {
            "summary_recall": 1.0,
            "metadata_recall": 1.0,
            "rrf_raw": 0.0,
            "final": 1.0,
            "source": "user_selected_file",
        },
        "initial_recall_components": {"source": "user_selected_file", "normalized_score": 1.0},
    } for knowledge_id in ids]


def _format(documents: list[dict[str, Any]], query: str) -> str:
    lines = ["=== 文档选择结果 ===", f"查询: {query}", f"选中 {len(documents)} 个文档："]
    for index, item in enumerate(documents, 1):
        scores = item.get("document_match_scores", {})
        lines.append(
            f"{index}. {item.get('knowledge_title') or item.get('file_name') or item.get('knowledge_id')} "
            f"(document={float(item.get('document_score', 0.0)):.4f}, "
            f"summary={float(scores.get('summary_recall', 0.0)):.4f}, "
            f"metadata={float(scores.get('metadata_recall', 0.0)):.4f})"
        )
    return "\n".join(lines)


async def run(*, clients: RAGClients, args: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    bm25_query = str(args.get("bm25_query") or query).strip()
    if not query:
        return {"error": "query 参数不能为空"}

    scope_plan = scope_plan_from_filters_or_context(args, ctx)
    scopes = list(scope_plan.scopes)
    if not scopes:
        scopes = [RecallScope(kb_id=kb_id, source="default_kb") for kb_id in clients.default_kb_ids]
    if not scopes:
        return {"error": "没有已授权的知识库范围"}

    explicitly_selected = list(dict.fromkeys(scope_plan.flat_knowledge_ids()))
    direct_limit = max(1, int(os.getenv("RAG_USER_SELECTED_MAX_DIRECT", "20")))
    if explicitly_selected and len(explicitly_selected) <= direct_limit:
        documents = _direct_documents(explicitly_selected, _selected_file_names(args, ctx), scopes)
        await _fill_titles(clients, documents)
        return {
            "query": query,
            "documents": documents,
            "count": len(documents),
            "knowledge_ids": [item["knowledge_id"] for item in documents],
            "knowledge_base_ids": scope_plan.flat_kb_ids(),
            "tag_ids": scope_plan.flat_tag_ids(),
            "document_select_trace": {"mode": "user_selected_file", "query": query, "bm25_query": bm25_query},
            "summary": _format(documents, query),
            "display_type": "document_shortlist",
        }

    clients.milvus.ensure_collection_loaded(clients.summary_collection)
    embedding = await clients.embedding.embed_query(query)
    top_k = max(1, int(args.get("recall_top_k") or os.getenv("RAG_DOCUMENT_SELECT_RECALL_TOP_K", "60")))
    # Research queries often contain an entity name that also appears in
    # company marketing documents. Keep a wider shortlist so the exact annual
    # data report can still reach chunk-level rerank instead of being cut off
    # by document selection.
    final_top_k = max(1, int(os.getenv("RAG_DOCUMENT_SELECT_FINAL_TOP_K", "6")))
    rrf_k = max(1, int(os.getenv("RAG_DOCUMENT_SELECT_RRF_K", "60")))
    semaphore = asyncio.Semaphore(max(1, int(os.getenv("RAG_SCOPE_SELECT_CONCURRENCY", "3"))))

    async def search_scope(scope: RecallScope) -> list[dict[str, Any]]:
        async with semaphore:
            summary_result, metadata_result = await asyncio.gather(
                asyncio.to_thread(
                    clients.milvus.search,
                    query_embedding=embedding,
                    query_text=bm25_query,
                    retriever_type=RetrieverType.HYBRID,
                    collection_name=clients.summary_collection,
                    filter_expr=scope.to_filter_expr(include_enabled=True),
                    top_k=top_k,
                    output_fields=SUMMARY_OUTPUT_FIELDS,
                    hybrid_ranker="rrf",
                    rrf_k=rrf_k,
                    anns_field=SUMMARY_DENSE_FIELD,
                    sparse_anns_field=SUMMARY_SPARSE_FIELD,
                ),
                asyncio.to_thread(
                    clients.milvus.search,
                    query_embedding=embedding,
                    query_text=bm25_query,
                    retriever_type=RetrieverType.HYBRID,
                    collection_name=clients.summary_collection,
                    filter_expr=scope.to_filter_expr(include_enabled=True),
                    top_k=top_k,
                    output_fields=SUMMARY_OUTPUT_FIELDS,
                    hybrid_ranker="rrf",
                    rrf_k=rrf_k,
                    anns_field=METADATA_DENSE_FIELD,
                    sparse_anns_field=METADATA_SPARSE_FIELD,
                ),
            )
        return _rrf_merge(_documents(summary_result), _documents(metadata_result), query=query, rrf_k=rrf_k, scope=scope)

    results = await asyncio.gather(*(search_scope(scope) for scope in scopes), return_exceptions=True)
    groups: list[list[dict[str, Any]]] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(f"{type(result).__name__}: {result}")
        else:
            groups.append(result)
    documents = _merge_scopes(groups)
    await _fill_titles(clients, documents)
    selected = documents[:final_top_k]
    for rank, item in enumerate(selected, 1):
        item["query_matches"] = [{"query": query, "rank": rank, "score": item["score"]}]

    trace = {
        "mode": "summary_metadata_hybrid_rrf",
        "query": query,
        "bm25_query": bm25_query,
        "rrf_k": rrf_k,
        "scope_count": len(scopes),
        "candidate_count": len(documents),
        "selected_count": len(selected),
        "errors": errors,
    }
    logger.info("[RAG][select] %s", json.dumps({**trace, "selected": [{"knowledge_id": item["knowledge_id"], "scores": item["document_match_scores"]} for item in selected]}, ensure_ascii=False))
    return {
        "query": query,
        "documents": selected,
        "count": len(selected),
        "knowledge_ids": [item["knowledge_id"] for item in selected],
        "knowledge_base_ids": scope_plan.flat_kb_ids() or list(clients.default_kb_ids),
        "tag_ids": scope_plan.flat_tag_ids(),
        "document_select_trace": trace,
        "summary": _format(selected, query) if selected else f"未找到匹配文档（查询: {query}）",
        "display_type": "document_shortlist",
    }
