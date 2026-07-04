"""RAG document reader.

The reader is intentionally shared by both RAG read modes:

- ``global_chunk_rerank``: read from globally ranked chunk anchors.
- ``per_document_read``: read selected documents directly; small documents are
  read in full, while large documents use per-document ranked anchors.

Dedup follows the weknora-style two-layer rule:

- ``processed_ids`` during expansion, seeded with all anchor chunk ids.
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
    document_score: float = 0.0,
    source_query: str = "",
    anchor_chunk_id: str = "",
    anchor_chunk_ids: list[str] | None = None,
    included_chunk_ids: list[str] | None = None,
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
        "document_score": document_score,
        "source_query": source_query,
        "anchor_chunk_id": anchor_chunk_id,
        "anchor_chunk_ids": anchor_chunk_ids or ([] if not anchor_chunk_id else [anchor_chunk_id]),
        "included_chunk_ids": included_chunk_ids or ([chunk_id] if chunk_id else []),
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


def _document_id(doc: dict[str, Any]) -> str:
    return str(doc.get("knowledge_id") or doc.get("id") or "").strip()


def _document_title(doc: dict[str, Any]) -> str:
    return str(doc.get("knowledge_title") or doc.get("file_name") or doc.get("title") or "").strip()


def _document_score(doc: dict[str, Any]) -> float:
    try:
        return float(doc.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _group_anchors_by_doc(
    anchors: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    anchors_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    doc_order: list[str] = []
    for anchor in anchors:
        kid = str(anchor.get("knowledge_id", "")).strip()
        if not kid:
            continue
        if kid not in anchors_by_doc:
            doc_order.append(kid)
        anchors_by_doc[kid].append(anchor)
    return anchors_by_doc, doc_order


async def _load_document_meta(
    *,
    clients: RAGClients,
    doc_order: list[str],
    selected_documents: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, int], dict[str, str], dict[str, str]]:
    pg = getattr(clients, "postgres", None)
    if pg is None or not doc_order:
        return {}, {}, {}

    selected_by_id = {
        _document_id(doc): doc
        for doc in selected_documents or []
        if _document_id(doc)
    }

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
        selected = selected_by_id.get(kid, {})
        if isinstance(knowledge, Exception) or not knowledge:
            title_by_doc[kid] = _document_title(selected)
            kb_by_doc[kid] = str(selected.get("knowledge_base_id") or "")
            continue
        title_by_doc[kid] = knowledge.title or knowledge.file_name or _document_title(selected)
        kb_by_doc[kid] = knowledge.knowledge_base_id or str(selected.get("knowledge_base_id") or "")

    return doc_counts, title_by_doc, kb_by_doc


async def _read_small_documents(
    *,
    pg: Any,
    small_doc_ids: list[str],
    anchors_by_doc: dict[str, list[dict[str, Any]]],
    selected_by_id: dict[str, dict[str, Any]],
    title_by_doc: dict[str, str],
    kb_by_doc: dict[str, str],
    chunk_limit: int,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    if not small_doc_ids:
        return evidence

    small_results = await asyncio.gather(
        *(
            pg.get_chunks_by_knowledge_id(
                kid,
                limit=chunk_limit,
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
        anchors = anchors_by_doc.get(kid, [])
        anchor_ids = {
            _anchor_chunk_id(anchor)
            for anchor in anchors
            if _anchor_chunk_id(anchor)
        }
        anchor_score_by_id = {
            _anchor_chunk_id(anchor): float(anchor.get("score", 0.0) or 0.0)
            for anchor in anchors
            if _anchor_chunk_id(anchor)
        }
        doc_score = _document_score(selected_by_id.get(kid, {}))
        max_anchor_score = max(anchor_score_by_id.values() or [doc_score])
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
                    score=anchor_score_by_id.get(chunk.id, doc_score),
                    anchor_score=max_anchor_score,
                    document_score=doc_score,
                    anchor_chunk_id=chunk.id if chunk.id in anchor_ids else "",
                    anchor_chunk_ids=sorted(anchor_ids),
                    included_chunk_ids=[chunk.id],
                    is_anchor=chunk.id in anchor_ids,
                    read_mode="small_doc_full",
                )
            )
    return evidence


def _merge_chunk_windows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort, deduplicate, and merge continuous/overlapping chunk windows."""
    added_chunk_ids: set[str] = set()
    unique_items: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda ev: int(ev.get("chunk_index", 0) or 0)):
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id or chunk_id in added_chunk_ids:
            continue
        added_chunk_ids.add(chunk_id)
        unique_items.append(item)

    groups: list[list[dict[str, Any]]] = []
    for item in unique_items:
        if not groups:
            groups.append([item])
            continue
        last_group = groups[-1]
        last_index = int(last_group[-1].get("chunk_index", 0) or 0)
        item_index = int(item.get("chunk_index", 0) or 0)
        if item_index <= last_index + 1:
            last_group.append(item)
        else:
            groups.append([item])

    merged: list[dict[str, Any]] = []
    for group in groups:
        included = [str(item.get("chunk_id") or "") for item in group if item.get("chunk_id")]
        anchor_ids = sorted({
            anchor_id
            for item in group
            for anchor_id in (item.get("anchor_chunk_ids") or [])
            if anchor_id
        })
        for item in group:
            item["included_chunk_ids"] = included
            item["anchor_chunk_ids"] = anchor_ids or item.get("anchor_chunk_ids", [])
            merged.append(item)
    return merged


async def _expand_large_documents(
    *,
    pg: Any,
    large_doc_ids: list[str],
    anchors_by_doc: dict[str, list[dict[str, Any]]],
    selected_by_id: dict[str, dict[str, Any]],
    title_by_doc: dict[str, str],
    kb_by_doc: dict[str, str],
    expand_min_bytes: int,
    expand_target_bytes: int,
    expand_max_chunks: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    processed_ids: set[str] = {
        _anchor_chunk_id(anchor)
        for anchors in anchors_by_doc.values()
        for anchor in anchors
        if _anchor_chunk_id(anchor)
    }
    expanded_window_count = 0
    dedup_skipped = 0

    for kid in large_doc_ids:
        anchors = anchors_by_doc.get(kid, [])
        doc_score = _document_score(selected_by_id.get(kid, {}))
        doc_items: list[dict[str, Any]] = []
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
                    document_score=doc_score,
                    source_query=str(anchor.get("source_query", "")),
                    anchor_chunk_id=anchor_id,
                    anchor_chunk_ids=[anchor_id],
                    included_chunk_ids=[anchor_id],
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
                    if not chunk.id or not content:
                        continue
                    if chunk.id in processed_ids:
                        dedup_skipped += 1
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
                            document_score=doc_score,
                            source_query=str(anchor.get("source_query", "")),
                            anchor_chunk_id=anchor_id,
                            anchor_chunk_ids=[anchor_id],
                            included_chunk_ids=[chunk.id],
                            is_anchor=False,
                            read_mode="large_doc_expand",
                        )
                    )
            expanded_window_count += 1
            doc_items.extend(_merge_chunk_windows(window_items))
        evidence.extend(_merge_chunk_windows(doc_items))

    stats = {
        "processed_count": len(processed_ids),
        "expanded_window_count": expanded_window_count,
        "dedup_skipped": dedup_skipped,
    }
    return evidence, stats


def dedup_evidence_chunks(
    evidence: list[dict[str, Any]],
    *,
    doc_order: list[str],
) -> tuple[list[dict[str, Any]], int]:
    """Final evidence assembly guard: one chunk ID can appear only once."""
    added_chunk_ids: set[str] = set()
    assembled: list[dict[str, Any]] = []
    skipped = 0
    for item in sorted(
        evidence,
        key=lambda ev: (
            0 if ev.get("source") == "web" else 1,
            doc_order.index(ev.get("knowledge_id")) if ev.get("knowledge_id") in doc_order else 999999,
            int(ev.get("chunk_index", 0) or 0),
        ),
    ):
        chunk_id = str(item.get("chunk_id") or "")
        if not chunk_id:
            continue
        if chunk_id in added_chunk_ids:
            skipped += 1
            continue
        added_chunk_ids.add(chunk_id)
        assembled.append(item)
    return assembled, skipped


def _web_evidence(top_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
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
            included_chunk_ids=[_anchor_chunk_id(item)] if _anchor_chunk_id(item) else [],
            is_anchor=True,
            read_mode="web_snippet",
            source=str(item.get("source", "web")),
        )
        for item in top_items
        if item.get("source") == "web" and (item.get("content") or "").strip()
    ]


async def _read_from_anchors(
    *,
    clients: RAGClients,
    anchors: list[dict[str, Any]],
    selected_documents: list[dict[str, Any]] | None = None,
    top_k: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if top_k is not None:
        anchors = anchors[:top_k]

    web_evidence = _web_evidence(anchors)
    internal_anchors = [
        item for item in anchors
        if item.get("source", "internal") == "internal"
        and item.get("knowledge_id")
        and _anchor_chunk_id(item)
        and (item.get("content") or "").strip()
    ]

    selected_documents = selected_documents or []
    selected_by_id = {
        _document_id(doc): doc
        for doc in selected_documents
        if _document_id(doc)
    }
    anchors_by_doc, anchor_doc_order = _group_anchors_by_doc(internal_anchors)
    selected_order = [
        _document_id(doc)
        for doc in selected_documents
        if _document_id(doc)
    ]
    doc_order = list(dict.fromkeys([*selected_order, *anchor_doc_order]))

    pg = getattr(clients, "postgres", None)
    if pg is None or not doc_order:
        return web_evidence, {
            "doc_order": doc_order,
            "read_modes": ["web_snippet"] if web_evidence else [],
        }

    await pg.connect()

    small_doc_chunk_limit = int(os.getenv("RAG_SMALL_DOC_CHUNK_LIMIT", "50"))
    expand_min_bytes = int(os.getenv("RAG_EXPAND_MIN_BYTES", "350"))
    expand_target_bytes = int(os.getenv("RAG_EXPAND_TARGET_BYTES", "1000"))
    expand_max_chunks = int(os.getenv("RAG_EXPAND_MAX_CHUNKS", "50"))

    doc_counts, title_by_doc, kb_by_doc = await _load_document_meta(
        clients=clients,
        doc_order=doc_order,
        selected_documents=selected_documents,
    )

    small_doc_ids = [
        kid for kid in doc_order
        if 0 < doc_counts.get(kid, 0) < small_doc_chunk_limit
    ]
    large_doc_ids = [kid for kid in doc_order if kid not in small_doc_ids]

    evidence: list[dict[str, Any]] = list(web_evidence)
    evidence.extend(
        await _read_small_documents(
            pg=pg,
            small_doc_ids=small_doc_ids,
            anchors_by_doc=anchors_by_doc,
            selected_by_id=selected_by_id,
            title_by_doc=title_by_doc,
            kb_by_doc=kb_by_doc,
            chunk_limit=small_doc_chunk_limit,
        )
    )
    large_evidence, expand_stats = await _expand_large_documents(
        pg=pg,
        large_doc_ids=large_doc_ids,
        anchors_by_doc=anchors_by_doc,
        selected_by_id=selected_by_id,
        title_by_doc=title_by_doc,
        kb_by_doc=kb_by_doc,
        expand_min_bytes=expand_min_bytes,
        expand_target_bytes=expand_target_bytes,
        expand_max_chunks=expand_max_chunks,
    )
    evidence.extend(large_evidence)

    assembled, final_dedup_skipped = dedup_evidence_chunks(evidence, doc_order=doc_order)
    read_modes = sorted({
        str(item.get("read_mode", ""))
        for item in assembled
        if item.get("read_mode")
    })
    stats = {
        "doc_order": doc_order,
        "doc_counts": doc_counts,
        "small_doc_ids": small_doc_ids,
        "large_doc_ids": large_doc_ids,
        "read_modes": read_modes,
        "evidence_count": len(assembled),
        "final_dedup_skipped": final_dedup_skipped,
        **expand_stats,
    }
    logger.info(
        "[DocumentReader] docs=%d small=%d large=%d anchors=%d evidence=%d processed=%d final_dedup_skipped=%d",
        len(doc_order),
        len(small_doc_ids),
        len(large_doc_ids),
        len(internal_anchors),
        len(assembled),
        int(expand_stats.get("processed_count", 0) or 0),
        final_dedup_skipped,
    )
    return assembled, stats


async def read_ranked_context(
    *,
    clients: RAGClients,
    ranked: list[dict[str, Any]],
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Read evidence from globally ranked branch-local candidates."""
    if not ranked:
        return []

    top_k = top_k or int(os.getenv("RAG_EVIDENCE_TOP_K", os.getenv("RAG_RANK_TOP_K", "10")))
    evidence, _stats = await _read_from_anchors(
        clients=clients,
        anchors=ranked,
        top_k=top_k,
    )
    logger.info("[DocumentReader] ranked=%d evidence=%d", len(ranked), len(evidence))
    return evidence


async def read_selected_documents_context(
    *,
    clients: RAGClients,
    selected_documents: list[dict[str, Any]],
    ranked_anchors: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read evidence for per-document mode.

    Small selected documents are read in full even when there are no anchors.
    Large documents rely on per-document ranked anchors for expansion.
    """
    selected_documents = [
        doc for doc in selected_documents
        if _document_id(doc)
    ]
    if not selected_documents:
        return [], {"document_read_mode": "per_document_read", "evidence_count": 0}

    evidence, stats = await _read_from_anchors(
        clients=clients,
        anchors=ranked_anchors,
        selected_documents=selected_documents,
        top_k=None,
    )
    stats["document_read_mode"] = "per_document_read"
    logger.info(
        "[RAG_READ_STRATEGY] mode=per_document_read selected_docs=%d small=%d large=%d anchors=%d evidence=%d",
        len(selected_documents),
        len(stats.get("small_doc_ids", [])),
        len(stats.get("large_doc_ids", [])),
        len(ranked_anchors),
        len(evidence),
    )
    return evidence, stats
