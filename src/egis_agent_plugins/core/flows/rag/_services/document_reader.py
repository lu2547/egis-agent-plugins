"""RAG document reader.

The RAG branch always recalls chunks first and ranks/MMRs them. This reader
then turns ranked chunk anchors into evidence:

- small documents (< ``small_doc_chunk_limit`` chunks): read the whole document.
- large documents: keep anchors, and expand short anchors downward until the
  window reaches roughly ``expand_target_bytes``.

Dedup follows the weknora-style two-layer rule:

- ``processed_ids`` during collection, seeded with all anchor chunk ids.
- ``added_chunk_ids`` during final assembly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients

logger = logging.getLogger(__name__)


def _content_bytes(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _chunk_to_evidence(
    *,
    chunk_id: str,
    knowledge_id: str,
    knowledge_base_id: str = "",
    knowledge_title: str = "",
    chunk_index: int = 0,
    content: str,
    score: float = 0.0,
    anchor_score: float = 0.0,
    source_query: str = "",
    anchor_chunk_id: str = "",
    anchor_chunk_ids: list[str] | None = None,
    is_anchor: bool = False,
    read_mode: str,
    source: str = "internal",
) -> dict[str, Any]:
    return {
        "knowledge_id": knowledge_id,
        "knowledge_base_id": knowledge_base_id,
        "knowledge_title": knowledge_title,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "content": content,
        "score": score,
        "anchor_score": anchor_score,
        "source_query": source_query,
        "anchor_chunk_id": anchor_chunk_id,
        "anchor_chunk_ids": anchor_chunk_ids or ([] if not anchor_chunk_id else [anchor_chunk_id]),
        "is_anchor": is_anchor,
        "read_mode": read_mode,
        "source": source,
    }


def _anchor_chunk_id(anchor: dict[str, Any]) -> str:
    return str(anchor.get("chunk_id") or anchor.get("id") or "").strip()


def _anchor_index(anchor: dict[str, Any]) -> int:
    try:
        return int(anchor.get("chunk_index", 0) or 0)
    except (TypeError, ValueError):
        return 0


async def read_ranked_context(
    *,
    clients: RAGClients,
    ranked: list[dict[str, Any]],
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Read evidence from ranked branch-local candidates."""
    if not ranked:
        return []

    top_k = top_k or int(os.getenv("RAG_EVIDENCE_TOP_K", "8"))
    small_doc_chunk_limit = int(os.getenv("RAG_SMALL_DOC_CHUNK_LIMIT", "50"))
    expand_min_bytes = int(os.getenv("RAG_EXPAND_MIN_BYTES", "350"))
    expand_target_bytes = int(os.getenv("RAG_EXPAND_TARGET_BYTES", "1000"))
    expand_max_chunks = int(os.getenv("RAG_EXPAND_MAX_CHUNKS", "10"))

    top_items = ranked[:top_k]

    web_evidence = [
        _chunk_to_evidence(
            chunk_id=_anchor_chunk_id(item),
            knowledge_id=str(item.get("knowledge_id", "")),
            knowledge_base_id=str(item.get("knowledge_base_id", "")),
            knowledge_title=str(item.get("knowledge_title", "")),
            chunk_index=_anchor_index(item),
            content=(item.get("content") or "").strip(),
            score=float(item.get("score", 0.0) or 0.0),
            anchor_score=float(item.get("score", 0.0) or 0.0),
            source_query=str(item.get("source_query", "")),
            anchor_chunk_id=_anchor_chunk_id(item),
            anchor_chunk_ids=[_anchor_chunk_id(item)] if _anchor_chunk_id(item) else [],
            is_anchor=True,
            read_mode="web_snippet",
            source=str(item.get("source", "web")),
        )
        for item in top_items
        if item.get("source") == "web" and (item.get("content") or "").strip()
    ]

    internal_anchors = [
        item for item in top_items
        if item.get("source", "internal") == "internal"
        and item.get("knowledge_id")
        and _anchor_chunk_id(item)
        and (item.get("content") or "").strip()
    ]
    if not internal_anchors:
        return web_evidence

    pg = getattr(clients, "postgres", None)
    if pg is None:
        return web_evidence

    await pg.connect()

    anchors_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    doc_order: list[str] = []
    for anchor in internal_anchors:
        kid = str(anchor.get("knowledge_id", ""))
        if kid not in anchors_by_doc:
            doc_order.append(kid)
        anchors_by_doc[kid].append(anchor)

    count_results = await asyncio.gather(
        *(pg.get_chunk_count_by_knowledge_id(kid) for kid in doc_order),
        return_exceptions=True,
    )
    doc_counts: dict[str, int] = {}
    for kid, count in zip(doc_order, count_results):
        if isinstance(count, Exception):
            logger.warning("[DocumentReader] count failed kid=%s: %s", kid, count)
            continue
        doc_counts[kid] = int(count or 0)

    knowledge_results = await asyncio.gather(
        *(pg.get_knowledge_by_id(kid) for kid in doc_order),
        return_exceptions=True,
    )
    title_by_doc: dict[str, str] = {}
    kb_by_doc: dict[str, str] = {}
    for kid, knowledge in zip(doc_order, knowledge_results):
        if isinstance(knowledge, Exception) or not knowledge:
            continue
        title_by_doc[kid] = knowledge.title or knowledge.file_name
        kb_by_doc[kid] = knowledge.knowledge_base_id

    evidence: list[dict[str, Any]] = list(web_evidence)
    processed_ids: set[str] = {
        _anchor_chunk_id(anchor)
        for anchor in internal_anchors
        if _anchor_chunk_id(anchor)
    }

    small_doc_ids = [
        kid for kid in doc_order
        if 0 < doc_counts.get(kid, 0) < small_doc_chunk_limit
    ]
    large_doc_ids = [kid for kid in doc_order if kid not in small_doc_ids]

    if small_doc_ids:
        small_results = await asyncio.gather(
            *(
                pg.get_chunks_by_knowledge_id(
                    kid,
                    limit=small_doc_chunk_limit,
                    offset=0,
                )
                for kid in small_doc_ids
            ),
            return_exceptions=True,
        )
        for kid, result in zip(small_doc_ids, small_results):
            if isinstance(result, Exception):
                logger.warning("[DocumentReader] small doc read failed kid=%s: %s", kid, result)
                continue
            chunks, _total = result
            anchor_ids = {
                _anchor_chunk_id(anchor)
                for anchor in anchors_by_doc.get(kid, [])
                if _anchor_chunk_id(anchor)
            }
            anchor_score_by_id = {
                _anchor_chunk_id(anchor): float(anchor.get("score", 0.0) or 0.0)
                for anchor in anchors_by_doc.get(kid, [])
                if _anchor_chunk_id(anchor)
            }
            for chunk in chunks:
                content = (chunk.content or "").strip()
                if not chunk.id or not content:
                    continue
                evidence.append(
                    _chunk_to_evidence(
                        chunk_id=chunk.id,
                        knowledge_id=kid,
                        knowledge_base_id=chunk.knowledge_base_id or kb_by_doc.get(kid, ""),
                        knowledge_title=title_by_doc.get(kid, ""),
                        chunk_index=chunk.chunk_index,
                        content=content,
                        score=anchor_score_by_id.get(chunk.id, 0.0),
                        anchor_score=max(anchor_score_by_id.values() or [0.0]),
                        anchor_chunk_id=chunk.id if chunk.id in anchor_ids else "",
                        anchor_chunk_ids=sorted(anchor_ids),
                        is_anchor=chunk.id in anchor_ids,
                        read_mode="small_doc_full",
                    )
                )

    for kid in large_doc_ids:
        anchors = anchors_by_doc.get(kid, [])
        for anchor in anchors:
            anchor_id = _anchor_chunk_id(anchor)
            anchor_content = (anchor.get("content") or "").strip()
            if not anchor_id or not anchor_content:
                continue

            window_items = [
                _chunk_to_evidence(
                    chunk_id=anchor_id,
                    knowledge_id=kid,
                    knowledge_base_id=str(anchor.get("knowledge_base_id") or kb_by_doc.get(kid, "")),
                    knowledge_title=str(anchor.get("knowledge_title") or title_by_doc.get(kid, "")),
                    chunk_index=_anchor_index(anchor),
                    content=anchor_content,
                    score=float(anchor.get("score", 0.0) or 0.0),
                    anchor_score=float(anchor.get("score", 0.0) or 0.0),
                    source_query=str(anchor.get("source_query", "")),
                    anchor_chunk_id=anchor_id,
                    anchor_chunk_ids=[anchor_id],
                    is_anchor=True,
                    read_mode="large_doc_anchor",
                )
            ]

            accumulated_bytes = _content_bytes(anchor_content)
            if accumulated_bytes < expand_min_bytes:
                try:
                    chunks = await pg.get_chunks_around_index(
                        kid,
                        _anchor_index(anchor),
                        radius=expand_max_chunks,
                    )
                except Exception as exc:
                    logger.warning(
                        "[DocumentReader] expand read failed kid=%s anchor=%s: %s",
                        kid,
                        anchor_id,
                        exc,
                    )
                    chunks = []
                for chunk in sorted(
                    (c for c in chunks if c.chunk_index > _anchor_index(anchor)),
                    key=lambda c: c.chunk_index,
                ):
                    if accumulated_bytes >= expand_target_bytes:
                        break
                    content = (chunk.content or "").strip()
                    if not chunk.id or not content or chunk.id in processed_ids:
                        continue
                    processed_ids.add(chunk.id)
                    accumulated_bytes += _content_bytes(content)
                    window_items.append(
                        _chunk_to_evidence(
                            chunk_id=chunk.id,
                            knowledge_id=kid,
                            knowledge_base_id=chunk.knowledge_base_id or kb_by_doc.get(kid, ""),
                            knowledge_title=title_by_doc.get(kid, ""),
                            chunk_index=chunk.chunk_index,
                            content=content,
                            score=0.0,
                            anchor_score=float(anchor.get("score", 0.0) or 0.0),
                            source_query=str(anchor.get("source_query", "")),
                            anchor_chunk_id=anchor_id,
                            anchor_chunk_ids=[anchor_id],
                            is_anchor=False,
                            read_mode="large_doc_expand",
                        )
                    )

            evidence.extend(window_items)

    added_chunk_ids: set[str] = set()
    assembled: list[dict[str, Any]] = []
    for item in sorted(
        evidence,
        key=lambda ev: (
            0 if ev.get("source") == "web" else 1,
            doc_order.index(ev.get("knowledge_id")) if ev.get("knowledge_id") in doc_order else 999999,
            int(ev.get("chunk_index", 0) or 0),
        ),
    ):
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id or chunk_id in added_chunk_ids:
            continue
        added_chunk_ids.add(chunk_id)
        assembled.append(item)

    logger.info(
        "[DocumentReader] ranked=%d docs=%d small=%d large=%d evidence=%d processed=%d added=%d",
        len(ranked),
        len(doc_order),
        len(small_doc_ids),
        len(large_doc_ids),
        len(assembled),
        len(processed_ids),
        len(added_chunk_ids),
    )
    return assembled
