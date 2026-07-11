"""Expand only MMR-selected chunk anchors without changing their order/scores."""

from __future__ import annotations

import logging
import os
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients

logger = logging.getLogger(__name__)


def _rune_len(value: str) -> int:
    return len(value or "")


def _concat_no_overlap(left: str, right: str, *, max_overlap: int = 128) -> str:
    if not left:
        return right
    if not right:
        return left
    upper = min(max_overlap, len(left), len(right))
    for size in range(upper, 0, -1):
        if left[-size:] == right[:size]:
            return left + right[size:]
    return left + right


def _prepend_with_budget(neighbor: str, current: str, max_chars: int) -> str:
    remaining = max_chars - _rune_len(current)
    if remaining <= 0:
        return current
    return _concat_no_overlap(neighbor[-remaining:], current)


def _append_with_budget(current: str, neighbor: str, max_chars: int) -> str:
    remaining = max_chars - _rune_len(current)
    if remaining <= 0:
        return current
    return _concat_no_overlap(current, neighbor[:remaining])


async def _expand_anchor(
    pg: Any,
    anchor: dict[str, Any],
    *,
    min_chars: int,
    max_chars: int,
    radius: int,
) -> dict[str, Any]:
    item = dict(anchor)
    content = str(item.get("content") or "")
    anchor_id = str(item.get("chunk_id") or item.get("id") or "")
    knowledge_id = str(item.get("knowledge_id") or "")
    anchor_index = int(item.get("chunk_index", 0) or 0)
    included_ids = [anchor_id] if anchor_id else []
    if not anchor_id or not knowledge_id or _rune_len(content) >= min_chars:
        item.update({
            "anchor_chunk_id": anchor_id,
            "included_chunk_ids": included_ids,
            "read_mode": "mmr_anchor",
        })
        return item

    chunks = await pg.get_chunks_around_index(knowledge_id, anchor_index, radius=radius)
    before = sorted(
        (chunk for chunk in chunks if chunk.chunk_index < anchor_index),
        key=lambda chunk: chunk.chunk_index,
        reverse=True,
    )
    after = sorted(
        (chunk for chunk in chunks if chunk.chunk_index > anchor_index),
        key=lambda chunk: chunk.chunk_index,
    )
    previous_content = ""
    next_content = ""
    previous_ids: list[str] = []
    next_ids: list[str] = []

    while _rune_len(_concat_no_overlap(_concat_no_overlap(previous_content, content), next_content)) < min_chars:
        expanded = False
        if before:
            chunk = before.pop(0)
            previous_budget = max(0, max_chars - _rune_len(content) - _rune_len(next_content))
            previous_content = _prepend_with_budget(str(chunk.content or ""), previous_content, previous_budget)
            previous_ids.insert(0, chunk.id)
            expanded = True
        merged = _concat_no_overlap(_concat_no_overlap(previous_content, content), next_content)
        if _rune_len(merged) >= min_chars:
            break
        if after:
            chunk = after.pop(0)
            next_budget = max(0, max_chars - _rune_len(content) - _rune_len(previous_content))
            next_content = _append_with_budget(next_content, str(chunk.content or ""), next_budget)
            next_ids.append(chunk.id)
            expanded = True
        if not expanded:
            break

    merged = _concat_no_overlap(_concat_no_overlap(previous_content, content), next_content)
    if _rune_len(content) < min_chars and _rune_len(merged) > max_chars:
        merged = merged[:max_chars]
    item.update({
        "content": merged,
        "anchor_chunk_id": anchor_id,
        "anchor_chunk_ids": [anchor_id],
        "included_chunk_ids": [*previous_ids, *included_ids, *next_ids],
        "expanded_chunk_ids": [*previous_ids, *next_ids],
        "read_mode": "mmr_anchor_expanded" if previous_ids or next_ids else "mmr_anchor",
        "expansion": {
            "before_chars": _rune_len(content),
            "after_chars": _rune_len(merged),
            "min_chars": min_chars,
            "max_chars": max_chars,
        },
    })
    return item


async def read_ranked_context(
    *,
    clients: RAGClients,
    ranked: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Expand the already-ranked top-k anchors and preserve MMR order."""
    anchors = [dict(item) for item in ranked[: max(0, top_k)]]
    if not anchors:
        return []
    pg = getattr(clients, "postgres", None)
    if pg is None:
        return anchors
    await pg.connect()
    min_chars = max(1, int(os.getenv("RAG_EXPAND_MIN_CHARS", "350")))
    max_chars = max(min_chars, int(os.getenv("RAG_EXPAND_MAX_CHARS", "1000")))
    radius = max(1, int(os.getenv("RAG_EXPAND_MAX_NEIGHBORS", "25")))
    evidence: list[dict[str, Any]] = []
    for anchor in anchors:
        if str(anchor.get("source") or "internal") != "internal":
            evidence.append(anchor)
            continue
        try:
            evidence.append(await _expand_anchor(pg, anchor, min_chars=min_chars, max_chars=max_chars, radius=radius))
        except Exception as exc:
            logger.warning("[DocumentReader] anchor expansion failed chunk=%s: %s", anchor.get("chunk_id"), exc)
            evidence.append(anchor)
    return evidence


async def read_selected_documents_context(
    *,
    clients: RAGClients,
    selected_documents: list[dict[str, Any]],  # noqa: ARG001
    ranked_anchors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compatibility adapter: never reads an entire selected document."""
    evidence = await read_ranked_context(clients=clients, ranked=ranked_anchors, top_k=len(ranked_anchors))
    return evidence, {
        "document_read_mode": "mmr_selected_anchor_expansion",
        "evidence_count": len(evidence),
    }
