"""选文档 stage —— 对 Milvus summary collection 做向量检索筛选候选文档。

模块级 ``run()`` 是 workflow 内部 operator 入口，返回结构化候选文档。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag._services.scope_adapter import (
    RecallScope,
    scope_plan_from_filters_or_context,
)
from egis_agent_plugins.core.flows.rag.filters import (
    ResolvedFilters,
    resolve_filters,
)
from egis_agent_plugins.core.flows.rag.stages.rank.mmr import apply_mmr
from egis_agent_plugins.core.service.base import MilvusSearchResult, RetrieverType

logger = logging.getLogger(__name__)
_filename_score_llm: Any | None = None


# ── Helpers ────────────────────────────────────────────────────────────────

SUMMARY_ANNS_FIELD = "embedding"
SUMMARY_OUTPUT_FIELDS = ["knowledge_id", "knowledge_base_id", "content", "file_name"]


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
                    return [
                        str(x).strip()
                        for x in parsed
                        if isinstance(x, str) and x.strip() and len(x.strip()) > 1
                    ] or None
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
                        out.extend(
                            str(i).strip()
                            for i in parsed
                            if isinstance(i, str) and i.strip() and len(i.strip()) > 1
                        )
                        continue
                except Exception:
                    pass
            out.append(x)
        return out or None
    return None


def _build_filter(
    *,
    kb_ids: list[str] | None,
    tag_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    file_name_keywords: list[str] | None = None,
) -> str | None:
    """构建 Milvus 标量过滤表达式。"""
    conditions: list[str] = []
    if kb_ids:
        ids_str = ", ".join(f'"{kid}"' for kid in kb_ids)
        conditions.append(f"knowledge_base_id in [{ids_str}]")
    if tag_ids:
        tids_str = ", ".join(f'"{tid}"' for tid in tag_ids)
        conditions.append(f"ARRAY_CONTAINS_ANY(tag_id, [{tids_str}])")
    if knowledge_ids:
        kids_str = ", ".join(f'"{kid}"' for kid in knowledge_ids)
        conditions.append(f"knowledge_id in [{kids_str}]")
    if file_name_keywords:
        like_parts = [f'file_name like "%{kw}%"' for kw in file_name_keywords if kw]
        if like_parts:
            conditions.append("(" + " or ".join(like_parts) + ")")
    conditions.append("is_enabled == true")
    return " and ".join(conditions) if conditions else None


def _parse_summary_results(raw_results) -> list[dict]:
    """解析 pymilvus 返回的 summary_knowledge_base 结果。"""
    documents: list[dict] = []
    if not raw_results or len(raw_results) == 0:
        return documents
    for hit in raw_results[0]:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else getattr(hit, "entity", {})
        distance = hit.get("distance", 0.0) if isinstance(hit, dict) else getattr(hit, "distance", 0.0)
        ent = entity if isinstance(entity, dict) else (dict(entity) if hasattr(entity, "keys") else {})
        knowledge_id = ent.get("knowledge_id", "")
        if not knowledge_id:
            knowledge_id = (
                str(hit.get("id", "")) if isinstance(hit, dict)
                else str(getattr(hit, "id", ""))
            )
        documents.append({
            "knowledge_id": knowledge_id,
            "knowledge_base_id": ent.get("knowledge_base_id", ""),
            "content": ent.get("content", ""),
            "file_name": ent.get("file_name", ""),
            "score": float(distance) if distance else 0.0,
        })
    return documents


def _documents_from_search_results(results: list[MilvusSearchResult]) -> list[dict]:
    """将 MilvusClient 搜索结果转换成文档摘要候选。"""
    documents: list[dict] = []
    for item in results:
        if not item.knowledge_id:
            continue
        documents.append({
            "knowledge_id": item.knowledge_id,
            "knowledge_base_id": item.knowledge_base_id,
            "content": item.content,
            "file_name": item.file_name,
            "score": float(item.score or 0.0),
        })
    return documents


def _format_output(
    documents: list[dict],
    query: str,
    strategy: dict[str, Any],
) -> str:
    weights = strategy.get("weights") if isinstance(strategy, dict) else {}
    preference = strategy.get("document_match_preference", "") if isinstance(strategy, dict) else ""
    lines = [
        "=== 文档选择结果 ===",
        f"查询: {query}",
        (
            "文档匹配权重: "
            f"preference={preference}, "
            f"filename={float((weights or {}).get('filename', 0.0)):.2f}, "
            f"summary={float((weights or {}).get('summary', 0.0)):.2f}"
        ),
        f"找到 {len(documents)} 个候选文档：",
        "",
    ]
    for i, doc in enumerate(documents, 1):
        kid = doc.get("knowledge_id", "")
        kb_id = doc.get("knowledge_base_id", "")
        score = doc.get("score", 0.0)
        match_scores = doc.get("document_match_scores") or {}
        lines.append(
            f"{i}. knowledge_id={kid} kb={kb_id} "
            f"(final={score:.3f}, filename_llm={float(match_scores.get('filename', 0.0)):.3f}, "
            f"summary_rerank={float(match_scores.get('summary', 0.0)):.3f}, "
            f"initial_rrf={float(match_scores.get('recall', 0.0)):.3f})"
        )
    lines.append("")
    lines.append("提示: 使用上述 knowledge_ids 调用 knowledge_search 做内容检索")
    return "\n".join(lines)


def _dedup_documents(documents: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for doc in sorted(documents, key=lambda d: float(d.get("score", 0.0)), reverse=True):
        kid = doc.get("knowledge_id", "")
        if not kid or kid in seen:
            continue
        seen.add(kid)
        out.append(doc)
    return out


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _document_match_strategy(hints: dict[str, Any]) -> dict[str, Any]:
    """Resolve filename/summary fusion weights for document selection."""
    preference = str(
        hints.get("document_match_preference")
        or os.getenv("RAG_DOCUMENT_MATCH_PREFERENCE")
        or "filename"
    ).strip()
    if preference == "filename":
        filename_weight, summary_weight = 0.8, 0.2
    elif preference == "balanced":
        filename_weight, summary_weight = 0.5, 0.5
    else:
        preference = "summary"
        filename_weight, summary_weight = 0.2, 0.8

    filename_weight = _env_float("RAG_DOCUMENT_SELECT_FILENAME_WEIGHT", filename_weight)
    summary_weight = _env_float("RAG_DOCUMENT_SELECT_SUMMARY_WEIGHT", summary_weight)
    total = filename_weight + summary_weight
    if total <= 0:
        filename_weight, summary_weight = 0.8, 0.2
    else:
        filename_weight = filename_weight / total
        summary_weight = summary_weight / total
    return {
        "document_match_preference": preference,
        "weights": {
            "filename": filename_weight,
            "summary": summary_weight,
        },
        "reason": str(hints.get("reason") or "").strip(),
    }


def _weights_from_strategy(strategy: dict[str, Any]) -> tuple[float, float]:
    weights = strategy.get("weights") if isinstance(strategy, dict) else {}
    if not isinstance(weights, dict):
        weights = {}
    return (
        float(weights.get("filename", 0.25) or 0.25),
        float(weights.get("summary", 0.75) or 0.75),
    )


def _filename_match_text(doc: dict[str, Any]) -> str:
    file_name = str(doc.get("file_name") or doc.get("knowledge_title") or "").strip()
    stem, _ = os.path.splitext(file_name)
    return (stem or file_name).strip()


def _summary_match_text(doc: dict[str, Any]) -> str:
    title = str(doc.get("knowledge_title") or doc.get("file_name") or "").strip()
    content = str(doc.get("content") or "").strip()
    if title and content:
        return f"文档：{title}\n摘要：{content}"
    if content:
        return f"摘要：{content}"
    return f"文档：{title}" if title else ""


def _document_diversity_text(doc: dict[str, Any]) -> str:
    """Text surface for document-level MMR diversity."""
    parts = [
        str(doc.get("knowledge_title") or "").strip(),
        str(doc.get("file_name") or "").strip(),
        str(doc.get("content") or "").strip(),
    ]
    return "\n".join(part for part in parts if part)


def _get_filename_score_llm() -> Any:
    global _filename_score_llm
    if _filename_score_llm is None:
        from ark_agentic.core.llm import create_chat_model_from_env

        _filename_score_llm = create_chat_model_from_env()
    return _filename_score_llm


def _parse_filename_score_response(raw: str, count: int) -> list[float]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    data = json.loads(raw.strip())
    if isinstance(data, dict):
        data = data.get("scores", [])
    if not isinstance(data, list):
        raise ValueError("filename score response must be a list")

    scores = [0.0 for _ in range(count)]
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", -1))
        if idx < 0 or idx >= count:
            continue
        score = float(item.get("score", 0.0))
        scores[idx] = max(0.0, min(1.0, score))
        seen.add(idx)
    if len(seen) != count:
        raise ValueError(f"filename score response missing indexes: expected={count}, got={len(seen)}")
    return scores


async def _llm_filename_scores(
    *,
    query: str,
    filenames: list[str],
) -> list[float]:
    if not filenames:
        return []

    items = [
        {"index": i, "passage": filename}
        for i, filename in enumerate(filenames)
    ]
    system = (
        "你是文档文件名语义相似度评分器。"
        "只判断 query 与 passage 是否指向同一类/同一份/同一主题文档。"
        "忽略文件后缀、年份/年度只作为区分信息；不要判断 passage 能否回答业务问题。"
        "输出 0 到 1 的分数，1 表示高度匹配，0 表示完全不相关。"
        "必须只输出 JSON，不要解释。格式："
        '{"scores":[{"index":0,"score":0.0}]}'
    )
    user = json.dumps(
        {
            "query": query,
            "items": items,
        },
        ensure_ascii=False,
    )

    timeout = float(os.getenv("RAG_DOCUMENT_FILENAME_SCORE_TIMEOUT_S", "8"))
    llm = _get_filename_score_llm()
    response = await asyncio.wait_for(
        llm.ainvoke(
            [
                SystemMessage(content=system),
                HumanMessage(content=user),
            ],
            temperature=0,
            max_tokens=min(1500, max(300, len(filenames) * 80)),
        ),
        timeout=timeout,
    )
    raw = response.content.strip() if response.content else ""
    scores = _parse_filename_score_response(raw, len(filenames))
    logger.info(
        "[SelectDocuments] filename_llm_score query=%s items=%s",
        query[:80],
        [
            {"idx": i, "score": round(score, 4), "passage": filename[:80]}
            for i, (filename, score) in enumerate(zip(filenames, scores))
        ][:10],
    )
    return scores


async def _rerank_scores(
    clients: RAGClients,
    *,
    query: str,
    passages: list[str],
    label: str,
) -> list[float]:
    if not clients.rerank or not clients.rerank.enabled:
        raise RuntimeError("文档选择需要 rerank，但 rerank 未配置")

    timeout = float(os.getenv("RAG_DOCUMENT_SELECT_RERANK_TIMEOUT_S", "8"))
    results = await asyncio.wait_for(
        clients.rerank.rerank(
            query,
            passages,
            top_k=len(passages),
            apply_threshold=False,
        ),
        timeout=timeout,
    )
    scores = [0.0 for _ in passages]
    for result in results:
        if 0 <= result.index < len(scores):
            scores[result.index] = float(result.score or 0.0)
    logger.info(
        "[SelectDocuments] rerank_%s query=%s passages=%d accepted=%d order=%s",
        label,
        query[:80],
        len(passages),
        len(results),
        [
            {
                "idx": int(result.index),
                "pos": pos,
                "score": round(float(result.score or 0.0), 4),
                "text": passages[result.index][:80] if 0 <= result.index < len(passages) else "",
            }
            for pos, result in enumerate(results[:10], 1)
        ],
    )
    return scores


async def _apply_document_strategy(
    *,
    clients: RAGClients,
    query: str,
    summary_query: str,
    documents: list[dict],
    hints: dict[str, Any],
) -> list[dict]:
    if not documents:
        return documents
    strategy = _document_match_strategy(hints)
    filename_weight, summary_weight = _weights_from_strategy(strategy)

    filename_passages = [_filename_match_text(doc) for doc in documents]
    summary_passages = [_summary_match_text(doc) for doc in documents]
    filename_scores, summary_scores = await asyncio.gather(
        _llm_filename_scores(query=query, filenames=filename_passages),
        _rerank_scores(
            clients,
            query=summary_query or query,
            passages=summary_passages,
            label="summary",
        ),
    )

    for doc, filename_text, name_score, summary_score in zip(
        documents,
        filename_passages,
        filename_scores,
        summary_scores,
    ):
        final_score = filename_weight * name_score + summary_weight * summary_score
        doc["recall_score"] = float(doc.get("score", 0.0) or 0.0)
        doc["initial_recall_score"] = doc["recall_score"]
        doc["score"] = final_score
        doc["document_match_strategy"] = strategy
        doc["document_match_scores"] = {
            "filename": name_score,
            "summary": summary_score,
            "final": final_score,
            "recall": doc["recall_score"],
            "filename_source": "llm_similarity",
            "filename_query": query,
            "filename_passage": filename_text,
            "summary_source": "rerank",
            "summary_query": summary_query or query,
            "score_source": "llm_filename_plus_summary_rerank",
        }
    return sorted(documents, key=lambda d: float(d.get("score", 0.0)), reverse=True)


def _shortlist_documents(
    documents: list[dict],
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Keep only documents that passed document selection before chunk recall."""
    if not documents:
        return [], [], {
            "max_documents": 0,
            "min_score": 0.0,
            "relative_to_best": 0.0,
            "best_score": 0.0,
        }

    max_documents = max(1, _env_int("RAG_DOCUMENT_SELECT_FINAL_TOP_K", 3))
    min_score = _env_float("RAG_DOCUMENT_SELECT_MIN_SCORE", 0.15)
    relative_to_best = _env_float("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", 0.85)
    diversity_strategy = os.getenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "mmr").strip().lower()
    mmr_lambda = max(0.0, min(1.0, _env_float("RAG_DOCUMENT_SELECT_MMR_LAMBDA", 0.7)))
    best_score = max(float(doc.get("score", 0.0) or 0.0) for doc in documents)
    cutoff = max(min_score, best_score * relative_to_best)

    eligible = [
        doc for doc in documents
        if float(doc.get("score", 0.0) or 0.0) >= cutoff
    ]
    if diversity_strategy == "mmr" and len(eligible) > max_documents:
        selected = apply_mmr(
            eligible,
            relevance_fn=lambda doc: float(doc.get("score", 0.0) or 0.0),
            content_fn=_document_diversity_text,
            k=max_documents,
            lambda_=mmr_lambda,
        )
    else:
        diversity_strategy = "score"
        selected = eligible[:max_documents]

    selected_ids = {id(doc) for doc in selected}
    rejected = [doc for doc in documents if id(doc) not in selected_ids]
    thresholds = {
        "max_documents": max_documents,
        "min_score": min_score,
        "relative_to_best": relative_to_best,
        "best_score": best_score,
        "cutoff": cutoff,
        "eligible_documents": len(eligible),
        "diversity_strategy": diversity_strategy,
        "mmr_lambda": mmr_lambda if diversity_strategy == "mmr" else None,
        "recall_source": "summary_milvus_rrf",
        "score_source": "llm_filename_plus_summary_rerank",
    }
    return selected, rejected, thresholds


def _excluded_file_names(hints: dict[str, Any]) -> list[str]:
    raw = hints.get("excluded_file_names") or hints.get("exclude_file_names") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [
        str(item).strip().casefold()
        for item in raw
        if str(item).strip()
    ]


def _apply_excluded_files(
    documents: list[dict],
    hints: dict[str, Any],
) -> tuple[list[dict], list[dict]]:
    excluded_names = _excluded_file_names(hints)
    if not excluded_names:
        return documents, []

    kept: list[dict] = []
    excluded: list[dict] = []
    for doc in documents:
        title = str(doc.get("knowledge_title") or "").casefold()
        file_name = str(doc.get("file_name") or "").casefold()
        haystack = f"{title} {file_name}"
        if any(name and name in haystack for name in excluded_names):
            doc["excluded_by_hint"] = True
            excluded.append(doc)
            continue
        kept.append(doc)
    return kept, excluded


def _doc_log_item(doc: dict[str, Any]) -> dict[str, Any]:
    scores = doc.get("document_match_scores") or {}
    return {
        "title": doc.get("knowledge_title") or doc.get("file_name") or doc.get("knowledge_id"),
        "kid": doc.get("knowledge_id", ""),
        "score": round(float(doc.get("score", 0.0) or 0.0), 4),
        "filename": round(float(scores.get("filename", 0.0) or 0.0), 4),
        "summary": round(float(scores.get("summary", 0.0) or 0.0), 4),
        "initial_recall": round(float(scores.get("recall", 0.0) or 0.0), 4),
        "initial_recall_components": doc.get("initial_recall_components", {}),
        "filename_source": scores.get("filename_source", ""),
        "summary_source": scores.get("summary_source", ""),
        "score_source": scores.get("score_source", ""),
    }


async def _fill_document_titles(clients: RAGClients, documents: list[dict]) -> None:
    """Fill document titles from PostgreSQL for display and downstream ranking."""
    knowledge_ids = list({doc.get("knowledge_id", "") for doc in documents if doc.get("knowledge_id")})
    if not knowledge_ids:
        return
    try:
        await clients.postgres.connect()
        knowledges = await clients.postgres.get_knowledges_by_ids(knowledge_ids)
    except Exception as e:
        logger.warning("[SelectDocuments] fill document titles failed: %s", e)
        return
    knowledge_map = {k.id: k for k in knowledges}
    for doc in documents:
        meta = knowledge_map.get(doc.get("knowledge_id", ""))
        if not meta:
            continue
        doc["knowledge_title"] = meta.title
        if not doc.get("file_name"):
            doc["file_name"] = meta.file_name or meta.title


# ── Stage entrypoint ───────────────────────────────────────────────────────


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select-documents 业务核心。返回结构化 dict（不含 a2ui digest）。"""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query 参数不能为空"}
    summary_query = (args.get("summary_query") or args.get("analysis_query") or query).strip()
    hints = args.get("hints") if isinstance(args.get("hints"), dict) else {}
    document_match_strategy = _document_match_strategy(hints)

    scope_plan = scope_plan_from_filters_or_context(args, ctx)
    scoped_kb_ids = scope_plan.flat_kb_ids() if scope_plan.has_scopes else []

    kb_ids_raw = scoped_kb_ids or clients.default_kb_ids
    kb_ids_in = _ensure_str_list(kb_ids_raw) or kb_ids_raw
    legacy_top_k = args.get("top_k")
    try:
        legacy_top_k_int = int(legacy_top_k) if legacy_top_k is not None else _env_int("RAG_DOCUMENT_SELECT_TOP_K", 20)
    except (TypeError, ValueError):
        legacy_top_k_int = _env_int("RAG_DOCUMENT_SELECT_TOP_K", 20)
    recall_top_k = args.get("recall_top_k")
    try:
        recall_top_k_int = int(recall_top_k) if recall_top_k is not None else _env_int("RAG_DOCUMENT_SELECT_RECALL_TOP_K", 60)
    except (TypeError, ValueError):
        recall_top_k_int = _env_int("RAG_DOCUMENT_SELECT_RECALL_TOP_K", 60)
    final_top_k = _env_int("RAG_DOCUMENT_SELECT_FINAL_TOP_K", 3)
    top_k = max(1, legacy_top_k_int, recall_top_k_int, final_top_k)

    # 名称 → ID 解析
    _t_resolve = time.perf_counter()
    try:
        await clients.postgres.connect()
        resolved = await resolve_filters(
            clients.postgres,
            kb_ids=kb_ids_in,
        )
    except Exception as e:
        logger.error("[SelectDocuments] resolve_filters failed: %s", e)
        return {"error": f"过滤条件解析失败: {e}"}
    logger.info("[SelectDocuments] stage=resolve_filters cost_ms=%d", int((time.perf_counter() - _t_resolve) * 1000))

    summary_collection = clients.summary_collection
    logger.info(
        "[SelectDocuments] query=%s, collection=%s, kb_ids=%s, tag_ids=%s, knowledge_ids=%s",
        query[:60], summary_collection, resolved.kb_ids, resolved.tag_ids, resolved.knowledge_ids,
    )

    try:
        _t_embed = time.perf_counter()
        query_embedding = await clients.embedding.embed_query(query)
        logger.info("[SelectDocuments] stage=embed cost_ms=%d", int((time.perf_counter() - _t_embed) * 1000))

        scopes = scope_plan.scopes if scope_plan.has_scopes else [RecallScope()]
        if not scopes:
            scopes = [RecallScope()]

        clients.milvus.ensure_collection_loaded(summary_collection)

        _t_milvus = time.perf_counter()
        rrf_k = int(os.getenv("RAG_DOCUMENT_SELECT_RRF_K", "60"))

        sem = asyncio.Semaphore(int(os.getenv("RAG_SCOPE_SELECT_CONCURRENCY", "3")))

        async def _search_scope(scope: RecallScope) -> list[dict]:
            filter_expr = scope.to_filter_expr(include_enabled=True)
            async with sem:
                search_results = await asyncio.to_thread(
                    clients.milvus.search,
                    query_embedding=query_embedding,
                    query_text=query,
                    retriever_type=RetrieverType.HYBRID,
                    collection_name=summary_collection,
                    filter_expr=filter_expr,
                    top_k=top_k,
                    output_fields=SUMMARY_OUTPUT_FIELDS,
                    hybrid_ranker="rrf",
                    rrf_k=rrf_k,
                )
            docs = _documents_from_search_results(search_results)
            for doc in docs:
                doc["initial_recall_components"] = {
                    "hybrid_ranker": "rrf",
                    "rrf_k": rrf_k,
                    "score": float(doc.get("score", 0.0) or 0.0),
                }
                doc["scope"] = {
                    "kb_id": scope.kb_id,
                    "kb_name": scope.kb_name,
                    "source": scope.source,
                }
            logger.info(
                "[SelectDocuments] scope=%s mode=hybrid_rrf docs=%d filter=%s rrf_k=%d",
                scope.kb_id or "*",
                len(docs),
                filter_expr,
                rrf_k,
            )
            return docs

        scope_results = await asyncio.gather(
            *(_search_scope(scope) for scope in scopes),
            return_exceptions=True,
        )
        logger.info("[SelectDocuments] stage=milvus cost_ms=%d", int((time.perf_counter() - _t_milvus) * 1000))

        documents: list[dict] = []
        for result in scope_results:
            if isinstance(result, Exception):
                logger.warning("[SelectDocuments] scoped search failed: %s", result)
                continue
            documents.extend(result)
        documents = _dedup_documents(documents)
        await _fill_document_titles(clients, documents)
        documents, excluded_by_hint = _apply_excluded_files(documents, hints)
        documents = await _apply_document_strategy(
            clients=clients,
            query=query,
            summary_query=summary_query,
            documents=documents,
            hints=hints,
        )
        selected_documents, rejected_documents, select_thresholds = _shortlist_documents(documents)
        logger.info(
            "[SelectDocuments] strategy=%s thresholds=%s candidates=%d selected=%d rejected=%d excluded=%d selected=%s rejected=%s excluded_docs=%s",
            document_match_strategy,
            select_thresholds,
            len(documents) + len(excluded_by_hint),
            len(selected_documents),
            len(rejected_documents),
            len(excluded_by_hint),
            [_doc_log_item(doc) for doc in selected_documents[:8]],
            [_doc_log_item(doc) for doc in rejected_documents[:8]],
            [_doc_log_item(doc) for doc in excluded_by_hint[:8]],
        )

        if not selected_documents:
            return {
                "query": query,
                "documents": [],
                "count": 0,
                "document_match_strategy": document_match_strategy,
                "document_select_thresholds": select_thresholds,
                "rejected_documents": rejected_documents,
                "excluded_documents": excluded_by_hint,
                "knowledge_base_ids": resolved.kb_ids,
                "tag_ids": resolved.tag_ids,
                "summary": f"未在摘要库中找到匹配文档（查询: {query}）",
                "display_type": "document_shortlist",
            }

        output = _format_output(selected_documents, query, document_match_strategy)
        return {
            "query": query,
            "documents": selected_documents,
            "count": len(selected_documents),
            "document_match_strategy": document_match_strategy,
            "document_select_thresholds": select_thresholds,
            "rejected_documents": rejected_documents,
            "excluded_documents": excluded_by_hint,
            "knowledge_base_ids": resolved.kb_ids,
            "tag_ids": resolved.tag_ids,
            "knowledge_ids": [d["knowledge_id"] for d in selected_documents],
            "summary": output,
            "display_type": "document_shortlist",
        }
    except Exception as e:
        logger.error("[SelectDocuments] 检索失败: %s", e)
        return {"error": f"文档选择失败: {e}"}
