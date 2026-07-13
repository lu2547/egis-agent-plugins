"""Chunk rerank, composite scoring, and greedy MMR."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag.stages.rank.mmr import apply_mmr
from egis_agent_plugins.core.flows.rag.stages.rank.reranker import RerankError

logger = logging.getLogger(__name__)


def _normalize(values: list[float]) -> list[float]:
    """Max-normalize non-negative retrieval scores while preserving zero."""
    cleaned = [max(0.0, float(value or 0.0)) for value in values]
    maximum = max(cleaned, default=0.0)
    return [value / maximum if maximum > 0 else 0.0 for value in cleaned]


async def _rerank(clients: RAGClients, query: str, candidates: list[dict[str, Any]]) -> list[float]:
    if not candidates:
        return []
    if not clients.rerank or not clients.rerank.enabled:
        return [float(item.get("recall_score", item.get("score", 0.0)) or 0.0) for item in candidates]
    timeout = clients.rerank.timeout_seconds
    try:
        results = await asyncio.wait_for(
            clients.rerank.rerank(query, [str(item.get("content") or "") for item in candidates]),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        message = f"Rerank timeout after {timeout:g}s"
        logger.error("[Rerank] %s", message, exc_info=True)
        raise RerankError(message) from exc
    scores = [0.0] * len(candidates)
    for item in results:
        if 0 <= item.index < len(scores):
            scores[item.index] = float(item.score or 0.0)
    return scores


async def _attach_chunk_metadata(clients: RAGClients, candidates: list[dict[str, Any]]) -> None:
    pg = getattr(clients, "postgres", None)
    if pg is None:
        return
    await pg.connect()
    chunks = await pg.get_chunks_by_ids([str(item.get("chunk_id") or item.get("id") or "") for item in candidates])
    by_id = {item.id: item for item in chunks}
    for candidate in candidates:
        chunk = by_id.get(str(candidate.get("chunk_id") or candidate.get("id") or ""))
        if chunk is None:
            continue
        candidate["chunk_index"] = chunk.chunk_index
        candidate["start_at"] = chunk.start_at
        candidate["end_at"] = chunk.end_at
        candidate["image_info"] = chunk.image_info


def _position_prior(start_at: int, end_at: int) -> float:
    """Match WeKnora's bounded chunk-position prior exactly."""
    if start_at < 0 or end_at <= start_at:
        return 1.0
    adjustment = 1.0 - float(start_at) / float(end_at + 1)
    return 1.0 + max(-0.05, min(0.05, adjustment))


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    t0 = time.perf_counter()
    candidates = [dict(item) for item in args.get("candidates") or []]
    queries = [str(item).strip() for item in args.get("queries") or [] if str(item).strip()]
    top_k = max(1, int(args.get("top_k") or os.getenv("RAG_RANK_TOP_K", "10")))
    if not candidates:
        return {"ranked": [], "count": 0, "rank_ms": 0}

    query = queries[0] if queries else ""
    await _attach_chunk_metadata(clients, candidates)
    raw_recall = [float(item.get("recall_score", item.get("score", 0.0)) or 0.0) for item in candidates]
    raw_rerank = await _rerank(clients, query, candidates)
    recall_scores = _normalize(raw_recall)
    rerank_scores = _normalize(raw_rerank)

    for index, item in enumerate(candidates):
        match_scores = item.get("document_match_scores") or {}
        if "summary_score" in item:
            summary_value = item.get("summary_score")
        elif "summary_recall" in match_scores:
            summary_value = match_scores.get("summary_recall")
        else:
            # Compatibility for non-document-selection candidates and older
            # persisted states that only carried the fused document score.
            summary_value = item.get("document_score", 0.0)
        summary_score = float(summary_value or 0.0)
        prior = _position_prior(int(item.get("start_at", 0) or 0), int(item.get("end_at", 0) or 0))
        composite_before_prior = 0.6 * rerank_scores[index] + 0.3 * recall_scores[index] + 0.1 * summary_score
        composite = max(0.0, min(1.0, composite_before_prior * prior))
        item.update({
            "raw_recall_score": raw_recall[index],
            "recall_score": recall_scores[index],
            "raw_rerank_score": raw_rerank[index],
            "rerank_score": rerank_scores[index],
            "summary_score": summary_score,
            "position_prior": prior,
            "composite_before_prior": composite_before_prior,
            "composite_score": composite,
            "score": composite,
            "score_trace": {
                "raw_recall": raw_recall[index],
                "normalized_recall": recall_scores[index],
                "raw_rerank": raw_rerank[index],
                "normalized_rerank": rerank_scores[index],
                "summary_recall": summary_score,
                "weights": {"rerank": 0.6, "recall": 0.3, "summary": 0.1},
                "position_prior": prior,
                "position": {
                    "start_at": int(item.get("start_at", 0) or 0),
                    "end_at": int(item.get("end_at", 0) or 0),
                },
                "composite": composite,
            },
        })

    candidates.sort(key=lambda item: float(item.get("composite_score", 0.0)), reverse=True)
    selected = apply_mmr(
        candidates,
        relevance_fn=lambda item: float(item.get("composite_score", 0.0)),
        content_fn=lambda item: str(item.get("content") or ""),
        k=min(top_k, len(candidates)),
        lambda_=float(os.getenv("RAG_MMR_LAMBDA", "0.7")),
    )
    for rank, item in enumerate(selected, 1):
        item["mmr_rank"] = rank
    elapsed = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "[RAG][rank] candidates=%d selected=%d top_composite=%.4f cost_ms=%d",
        len(candidates), len(selected), float(selected[0]["composite_score"]) if selected else 0.0, elapsed,
    )
    return {"ranked": selected, "count": len(selected), "rank_ms": elapsed}
