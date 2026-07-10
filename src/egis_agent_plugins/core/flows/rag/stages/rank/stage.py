"""Ranking stage — rerank, threshold, and MMR ordering."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag.stages.rank.mmr import apply_mmr

logger = logging.getLogger(__name__)


async def _apply_rerank(
    clients: RAGClients,
    *,
    queries: list[str],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply external rerank to the top candidate slice."""
    if not clients.rerank or not clients.rerank.enabled:
        return candidates

    rerank_topn = int(os.getenv("RAG_RERANK_TOPN", "30"))
    rerank_timeout = float(os.getenv("RAG_RERANK_TIMEOUT_S", "5"))

    head = [dict(c) for c in candidates[:rerank_topn]]
    tail = candidates[rerank_topn:]
    passages = [c.get("content", "") for c in head]

    effective_queries = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))
    if not effective_queries:
        return candidates
    default_concurrency = os.getenv("RAG_RETRIEVAL_CONCURRENCY", "6")
    sem = asyncio.Semaphore(max(
        1,
        int(os.getenv("RAG_RERANK_QUERY_CONCURRENCY", default_concurrency)),
    ))

    async def _rerank_one(query: str) -> tuple[str, list[Any]]:
        async with sem:
            results = await asyncio.wait_for(
                clients.rerank.rerank(query, passages),
                timeout=rerank_timeout,
            )
        return query, results

    query_results = await asyncio.gather(
        *(_rerank_one(query) for query in effective_queries),
        return_exceptions=True,
    )
    merged: dict[int, dict[str, Any]] = {}
    successful_queries = 0
    for result in query_results:
        if isinstance(result, asyncio.TimeoutError):
            logger.warning(
                "[Rank] one query rerank timed out after %.1fs; continuing other queries",
                rerank_timeout,
            )
            continue
        if isinstance(result, Exception):
            logger.warning("[Rank] one query rerank failed: %s", result)
            continue
        query, rerank_results = result
        successful_queries += 1
        for query_rank, rr in enumerate(rerank_results, 1):
            if not 0 <= rr.index < len(head):
                continue
            entry = merged.get(rr.index)
            if entry is None:
                entry = dict(head[rr.index])
                entry["rerank_query_matches"] = []
                merged[rr.index] = entry
            entry["rerank_query_matches"].append({
                "query": query,
                "rank": query_rank,
                "score": float(rr.score or 0.0),
            })
            if float(rr.score or 0.0) >= float(entry.get("rerank_score", 0.0) or 0.0):
                entry["score"] = rr.score
                entry["rerank_score"] = rr.score
                entry["rerank_query"] = query
            entry["reranked"] = True

    if not successful_queries:
        return candidates
    accepted = sorted(
        merged.values(),
        key=lambda item: float(item.get("rerank_score", 0.0) or 0.0),
        reverse=True,
    )

    logger.debug(
        "[Rank] rerank accepted=%d dropped=%d tail_dropped=%d",
        len(accepted),
        len(head) - len(merged),
        len(tail),
    )
    for item in accepted:
        logger.debug(
            "[Rank] ✔ %s  score=%.4f  knowledge_id=%s",
            item.get("file_name", "unknown"),
            item.get("rerank_score", 0.0),
            item.get("knowledge_id", ""),
        )
    return accepted


def _apply_diversity(candidates: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    if top_k <= 0 or len(candidates) <= top_k:
        return candidates
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    query_order = list(dict.fromkeys(
        str(match.get("query") or "")
        for candidate in candidates
        for match in candidate.get("rerank_query_matches", [])
        if str(match.get("query") or "")
    ))
    for query in query_order:
        matching = [
            item
            for item in candidates
            if id(item) not in selected_ids
            and any(
                match.get("query") == query
                for match in item.get("rerank_query_matches", [])
            )
        ]
        candidate = max(
            matching,
            key=lambda item: max(
                float(match.get("score", 0.0) or 0.0)
                for match in item.get("rerank_query_matches", [])
                if match.get("query") == query
            ),
            default=None,
        )
        if candidate is None:
            continue
        selected.append(candidate)
        selected_ids.add(id(candidate))
        if len(selected) >= top_k:
            return selected

    remaining = [item for item in candidates if id(item) not in selected_ids]
    mmr_results = apply_mmr(
        remaining,
        relevance_fn=lambda c: float(c.get("score", 0.0)),
        content_fn=lambda c: c.get("content", ""),
        k=top_k - len(selected),
        lambda_=float(os.getenv("RAG_MMR_LAMBDA", "0.7")),
    )
    return [*selected, *(mmr_results or remaining[: top_k - len(selected)])]


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Rank candidates and return a diversity-aware ordered list."""
    t0 = time.perf_counter()
    candidates = list(args.get("candidates") or [])
    queries = [q for q in (args.get("queries") or []) if q]
    top_k = int(args.get("top_k") or os.getenv("RAG_RANK_TOP_K", "10"))

    if not candidates:
        return {"ranked": [], "count": 0, "rank_ms": 0}

    reranked = await _apply_rerank(clients, queries=queries, candidates=candidates)
    reranked.sort(key=lambda c: float(c.get("score", 0.0)), reverse=True)
    ranked = _apply_diversity(reranked, top_k=top_k)

    elapsed = int((time.perf_counter() - t0) * 1000)
    logger.debug(
        "[Rank] candidates=%d accepted=%d ranked=%d cost_ms=%d",
        len(candidates),
        len(reranked),
        len(ranked),
        elapsed,
    )
    return {"ranked": ranked, "count": len(ranked), "rank_ms": elapsed}
