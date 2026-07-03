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
    query = " ".join(q for q in queries if q)
    passages = [c.get("content", "") for c in head]

    try:
        rerank_results = await asyncio.wait_for(
            clients.rerank.rerank(query, passages),
            timeout=rerank_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[Rank] rerank 超时 %.1fs (候选 %d 条)，保留召回原序",
            rerank_timeout,
            len(head),
        )
        return candidates
    except Exception as e:
        logger.warning("[Rank] rerank failed: %s; keep recall order", e)
        return candidates

    accepted: list[dict[str, Any]] = []
    accepted_indices: set[int] = set()
    for rr in rerank_results:
        if 0 <= rr.index < len(head):
            item = head[rr.index]
            item["score"] = rr.score
            item["rerank_score"] = rr.score
            item["reranked"] = True
            accepted.append(item)
            accepted_indices.add(rr.index)

    logger.info(
        "[Rank] rerank accepted=%d dropped=%d tail_dropped=%d",
        len(accepted),
        len(head) - len(accepted),
        len(tail),
    )
    for item in accepted:
        logger.info(
            "[Rank] ✔ %s  score=%.4f  knowledge_id=%s",
            item.get("file_name", "unknown"),
            item.get("rerank_score", 0.0),
            item.get("knowledge_id", ""),
        )
    for i, item in enumerate(head):
        if i not in accepted_indices:
            logger.info(
                "[Rank] ✘ %s  (dropped by rerank/threshold)  knowledge_id=%s",
                item.get("file_name", "unknown"),
                item.get("knowledge_id", ""),
            )
    return accepted


def _apply_diversity(candidates: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    if top_k <= 0 or len(candidates) <= top_k:
        return candidates
    mmr_results = apply_mmr(
        candidates,
        relevance_fn=lambda c: float(c.get("score", 0.0)),
        content_fn=lambda c: c.get("content", ""),
        k=top_k,
        lambda_=float(os.getenv("RAG_MMR_LAMBDA", "0.7")),
    )
    return mmr_results or candidates[:top_k]


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
    logger.info(
        "[Rank] candidates=%d accepted=%d ranked=%d cost_ms=%d",
        len(candidates),
        len(reranked),
        len(ranked),
        elapsed,
    )
    return {"ranked": ranked, "count": len(ranked), "rank_ms": elapsed}
