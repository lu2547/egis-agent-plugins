"""选文档 stage —— 对 Milvus summary collection 做向量检索筛选候选文档。

模块级 ``run()`` 是 workflow 内部 operator 入口，返回结构化候选文档。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag.filters import (
    ResolvedFilters,
    resolve_filters,
)
from egis_agent_plugins.core.flows.rag.state import read_forced_filters

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

SUMMARY_ANNS_FIELD = "embedding"
SUMMARY_OUTPUT_FIELDS = ["knowledge_id", "knowledge_base_id"]


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
            "score": float(distance) if distance else 0.0,
        })
    return documents


def _format_output(documents: list[dict], query: str) -> str:
    lines = [
        "=== 文档选择结果 ===",
        f"查询: {query}",
        f"找到 {len(documents)} 个候选文档：",
        "",
    ]
    for i, doc in enumerate(documents, 1):
        kid = doc.get("knowledge_id", "")
        kb_id = doc.get("knowledge_base_id", "")
        score = doc.get("score", 0.0)
        lines.append(f"{i}. knowledge_id={kid} kb={kb_id} (相关度: {score:.3f})")
    lines.append("")
    lines.append("提示: 使用上述 knowledge_ids 调用 knowledge_search 做内容检索")
    return "\n".join(lines)


# ── Stage entrypoint ───────────────────────────────────────────────────────


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select-documents 业务核心。返回结构化 dict（不含 a2ui digest）。"""
    forced_kbs, forced_tags, forced_files = read_forced_filters(ctx)
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query 参数不能为空"}

    kb_ids_raw = forced_kbs if forced_kbs is not None else (args.get("knowledge_base_ids") or clients.default_kb_ids)
    kb_ids_in = _ensure_str_list(kb_ids_raw) or kb_ids_raw
    kb_names = None if forced_kbs is not None else (args.get("kb_names") or None)
    file_name_keywords = args.get("file_name_keywords") or None
    forced_knowledge_ids = _ensure_str_list(forced_files) or forced_files
    file_names = None if forced_files is not None else (args.get("file_names") or None)
    tag_ids_raw = forced_tags if forced_tags is not None else (args.get("tag_ids") or None)
    tag_ids_in = _ensure_str_list(tag_ids_raw) or tag_ids_raw
    tag_names = None if forced_tags is not None else (args.get("tag_names") or None)
    top_k = args.get("top_k", 10)

    # 名称 → ID 解析
    _t_resolve = time.perf_counter()
    try:
        await clients.postgres.connect()
        resolved = await resolve_filters(
            clients.postgres,
            kb_ids=kb_ids_in,
            kb_names=kb_names,
            tag_ids=tag_ids_in,
            tag_names=tag_names,
            knowledge_ids=forced_knowledge_ids,
            file_names=file_names,
        )
    except Exception as e:
        logger.warning("[SelectDocuments] resolve_filters failed: %s, fallback to raw ids", e)
        resolved = ResolvedFilters(
            kb_ids=list(kb_ids_in or []),
            tag_ids=list(tag_ids_in or []),
            knowledge_ids=list(forced_knowledge_ids or []),
        )
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

        filter_expr = _build_filter(
            kb_ids=resolved.kb_ids or None,
            tag_ids=resolved.tag_ids or None,
            knowledge_ids=resolved.knowledge_ids or None,
            file_name_keywords=file_name_keywords,
        )

        clients.milvus.ensure_collection_loaded(summary_collection)

        _t_milvus = time.perf_counter()
        search_params = {
            "metric_type": clients.milvus._config.milvus_metric_type,
            "params": {"nprobe": 10},
        }
        raw_results = clients.milvus.client.search(
            collection_name=summary_collection,
            data=[query_embedding],
            anns_field=SUMMARY_ANNS_FIELD,
            limit=top_k,
            output_fields=SUMMARY_OUTPUT_FIELDS,
            search_params=search_params,
            filter=filter_expr,
        )
        logger.info("[SelectDocuments] stage=milvus cost_ms=%d", int((time.perf_counter() - _t_milvus) * 1000))

        documents = _parse_summary_results(raw_results)
        logger.info("[SelectDocuments] DIAG: parsed docs=%d, filter=%s", len(documents), filter_expr)

        if not documents:
            return {
                "query": query,
                "documents": [],
                "count": 0,
                "knowledge_base_ids": resolved.kb_ids,
                "tag_ids": resolved.tag_ids,
                "summary": f"未在摘要库中找到匹配文档（查询: {query}）",
                "display_type": "document_shortlist",
            }

        output = _format_output(documents, query)
        return {
            "query": query,
            "documents": documents,
            "count": len(documents),
            "knowledge_base_ids": resolved.kb_ids,
            "tag_ids": resolved.tag_ids,
            "knowledge_ids": [d["knowledge_id"] for d in documents],
            "summary": output,
            "display_type": "document_shortlist",
        }
    except Exception as e:
        logger.error("[SelectDocuments] 检索失败: %s", e)
        return {"error": f"文档选择失败: {e}"}

