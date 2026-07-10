"""选文档 stage —— 对 Milvus summary collection 做向量检索筛选候选文档。

模块级 ``run()`` 是 workflow 内部 operator 入口，返回结构化候选文档。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date
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

# summary_knowledge_base 同时存储摘要与 metadata 的两套 dense/sparse 表征。
# 文档选择会分别检索两路，再在应用侧以 knowledge_id 做 RRF。
SUMMARY_DENSE_FIELD = "embedding"
SUMMARY_SPARSE_FIELD = "content_sparse"
METADATA_DENSE_FIELD = "metadata_embedding"
METADATA_SPARSE_FIELD = "metadata_sparse"
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


def _normalize_document_queries(query: str, value: Any) -> list[str]:
    """Normalize the rewrite stage's document-query plan without truncating it."""
    raw = value if isinstance(value, list) else []
    queries: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        queries.append(text)
    return queries or [query]


def _rrf_merge_documents(
    ranked_lists: list[tuple[str, list[dict]]],
    *,
    rrf_k: int,
    query: str = "",
    scope: RecallScope | None = None,
) -> list[dict]:
    """Fuse summary and metadata rankings for one independent query."""
    merged: dict[str, dict[str, Any]] = {}
    for source, ranked in ranked_lists:
        for rank, original in enumerate(ranked):
            knowledge_id = str(original.get("knowledge_id") or "")
            if not knowledge_id:
                continue
            item = merged.get(knowledge_id)
            if item is None:
                item = dict(original)
                item["score"] = 0.0
                item["initial_recall_components"] = {
                    "hybrid_ranker": "rrf",
                    "rrf_k": rrf_k,
                    "retrieval_query": query,
                    "routes": {},
                }
                merged[knowledge_id] = item
            contribution = 1.0 / (rrf_k + rank + 1)
            item["score"] = float(item["score"]) + contribution
            components = item["initial_recall_components"]
            routes = components["routes"]
            routes.setdefault(source, []).append({"rank": rank + 1, "rrf_score": contribution})
            if scope:
                item["scope"] = {
                    "kb_id": scope.kb_id,
                    "kb_name": scope.kb_name,
                    "source": scope.source,
                }

    documents = sorted(merged.values(), key=lambda item: float(item["score"]), reverse=True)
    for document in documents:
        components = document["initial_recall_components"]
        components["score"] = float(document["score"])
    return documents


def _union_query_candidates(
    query_candidates: list[tuple[str, list[dict]]],
) -> tuple[list[dict], dict[str, list[str]]]:
    """Union independently selected query candidates by knowledge_id.

    A document occurring in several query lists keeps its best query-document
    score. Scores from different queries are never added together.
    """
    merged: dict[str, dict[str, Any]] = {}
    coverage: dict[str, list[str]] = {}
    for query_text, documents in query_candidates:
        coverage[query_text] = []
        for rank, original in enumerate(documents, 1):
            knowledge_id = str(original.get("knowledge_id") or "")
            if not knowledge_id:
                continue
            coverage[query_text].append(knowledge_id)
            match = {
                "query": query_text,
                "rank": rank,
                "score": float(original.get("score", 0.0) or 0.0),
                "document_match_scores": original.get("document_match_scores", {}),
                "initial_recall_components": original.get("initial_recall_components", {}),
            }
            existing = merged.get(knowledge_id)
            if existing is None:
                existing = dict(original)
                existing["query_matches"] = [match]
                merged[knowledge_id] = existing
            else:
                existing.setdefault("query_matches", []).append(match)
                if match["score"] > float(existing.get("score", 0.0) or 0.0):
                    preserved_matches = existing["query_matches"]
                    existing = dict(original)
                    existing["query_matches"] = preserved_matches
                    merged[knowledge_id] = existing

    selected = sorted(
        merged.values(),
        key=lambda document: float(document.get("score", 0.0) or 0.0),
        reverse=True,
    )
    return selected, coverage


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


def _collect_file_name_map(
    args: dict[str, Any],
    ctx: dict[str, Any] | None,
) -> dict[str, str]:
    """从 rag_filter 里提取 file_id -> file_name 映射，供短路分支展示使用。

    scope_adapter 只把 knowledge_ids 展开到 RecallScope，实际 file_name 保留在
    原始 rag_filter 结构里。直接短路时不再跑 Milvus，需手动从原结构里拿。
    """
    candidates: list[Any] = []
    filters = args.get("filters")
    if isinstance(filters, dict):
        candidates.append(filters.get("rag_filter") or filters.get("rag_filters"))
    candidates.append(args.get("rag_filter") or args.get("rag_filters"))
    if isinstance(ctx, dict):
        for key in ("user:rag_state", "rag_state"):
            state = ctx.get(key)
            if isinstance(state, dict):
                candidates.append(state.get("rag_filter") or state.get("rag_filters"))

    result: dict[str, str] = {}
    for raw in candidates:
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            for tag in item.get("tags") or []:
                if isinstance(tag, dict):
                    for f in tag.get("files") or []:
                        _record_file_name(result, f)
            for f in item.get("files") or []:
                _record_file_name(result, f)
    return result


def _record_file_name(target: dict[str, str], file_obj: Any) -> None:
    if not isinstance(file_obj, dict):
        return
    fid = str(
        file_obj.get("id")
        or file_obj.get("knowledge_id")
        or file_obj.get("file_id")
        or ""
    ).strip()
    if not fid:
        return
    name = str(file_obj.get("name") or file_obj.get("file_name") or "").strip()
    if name and not target.get(fid):
        target[fid] = name


def _build_user_selected_result(
    *,
    query: str,
    document_queries: list[str],
    document_match_strategy: dict[str, Any],
    scope_plan: Any,
    user_selected_ids: list[str],
    file_name_by_id: dict[str, str],
) -> dict[str, Any]:
    """短路:用户已指定文件时直接构造 selected_documents，跳过 LLM 打分与 constraint。

    返回结构与正常路径保持一致，下游 workflow 无需适配。
    """
    kb_ids = scope_plan.flat_kb_ids() if scope_plan.has_scopes else []
    default_kb = kb_ids[0] if kb_ids else ""
    selected_documents = [
        {
            "knowledge_id": kid,
            "knowledge_base_id": default_kb,
            "knowledge_title": file_name_by_id.get(kid, ""),
            "file_name": file_name_by_id.get(kid, ""),
            "score": 1.0,
            "document_match_scores": {
                "user_directly_selected": True,
                "filename": 0.0,
                "summary": 0.0,
                "recall": 0.0,
            },
            "document_match_strategy": {
                **document_match_strategy,
                "user_directly_selected": True,
            },
        }
        for kid in user_selected_ids
    ]
    output = _format_output(selected_documents, query, document_match_strategy)
    logger.info(
        "[RAG][select] %s",
        json.dumps(
            {
                "mode": "user_selected_direct",
                "queries": document_queries,
                "selected_count": len(selected_documents),
                "selected": [
                    {
                        "rank": rank,
                        "kid": kid,
                        "title": file_name_by_id.get(kid, ""),
                    }
                    for rank, kid in enumerate(user_selected_ids, 1)
                ],
            },
            ensure_ascii=False,
            default=str,
        ),
    )
    return {
        "query": query,
        "documents": selected_documents,
        "count": len(selected_documents),
        "document_match_strategy": document_match_strategy,
        "document_select_thresholds": {
            "mode": "user_selected_direct",
            "max_documents": len(selected_documents),
            "reason": "user explicitly selected files, skip LLM scoring",
        },
        "rejected_documents": [],
        "excluded_documents": [],
        "knowledge_base_ids": kb_ids,
        "tag_ids": scope_plan.flat_tag_ids() if scope_plan.has_scopes else [],
        "knowledge_ids": user_selected_ids,
        "summary": output,
        "display_type": "document_shortlist",
    }


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


def _parse_filename_score_response(raw: str, count: int) -> list[dict[str, Any]]:
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

    matches: list[dict[str, Any] | None] = [None for _ in range(count)]
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        idx = int(item.get("index", -1))
        if idx < 0 or idx >= count:
            continue
        score = float(item.get("score", 0.0))
        if "constraint_applies" not in item or "constraint_matched" not in item:
            raise ValueError("filename score response missing constraint decision")
        if not isinstance(item.get("constraint_applies"), bool) or not isinstance(
            item.get("constraint_matched"), bool
        ):
            raise ValueError("filename constraint decisions must be booleans")
        constraint_applies = item["constraint_applies"]
        constraint_matched = item["constraint_matched"]
        matches[idx] = {
            "score": max(0.0, min(1.0, score)),
            "constraint_applies": constraint_applies,
            "constraint_matched": constraint_matched if constraint_applies else True,
            "constraint_reason": str(item.get("constraint_reason") or "").strip(),
        }
        seen.add(idx)
    if len(seen) != count:
        raise ValueError(f"filename score response missing indexes: expected={count}, got={len(seen)}")
    return [item for item in matches if item is not None]


async def _llm_filename_scores(
    *,
    query: str,
    filenames: list[str],
) -> list[dict[str, Any]]:
    if not filenames:
        return []

    items = [
        {"index": i, "passage": filename}
        for i, filename in enumerate(filenames)
    ]
    system = (
        "你是文档来源约束判断与文件名相关性评分器。"
        "先判断 query 是否明确限定了要查阅的来源文档，例如文档类型/标题主题、时间或期间、"
        "实体、产品、地区、版本；仅仅询问某个业务主题不算限定来源文档。"
        "constraint_applies 表示存在这类来源硬限制。存在时，passage 必须满足文件名中可核验的全部限制；"
        "逐项比较 query 的显式来源约束与 passage，任一约束冲突或无法从 passage 验证时，"
        "constraint_matched 必须为 false；不能因为共享部分主题词或实体词而忽略其他约束。"
        "【聚合文档豁免】若 passage 包含‘全国/全行业/行业/合集/汇编/摘要/年鉴/统计/白皮书/概览’等聚合类关键词，"
        "其内部通常按机构/公司/地区/期间拆分列数据，query 中的公司名/机构名/产品名等主体约束不视为对该 passage 的硬冲突"
        "（时间/期间/地区/文档类型仍需满足）；此时 constraint_matched 保持 true，由下游读取实际内容判断，"
        "不得仅凭 passage 主体名与 query 主体名不同就判 false。"
        "然后只判断 query 与 passage 是否指向同一类/同一份/同一主题文档并输出 score。"
        "忽略文件后缀；限制不满足时 score 必须接近 0，不能只按共享主题词给高分。"
        "但聚合文档豁免下，该实体/产品约束不拖低 score，仍按主题/类型/时间匹配度打分。"
        "query 含相对时间时使用输入中的 current_date 判断。"
        "不要判断 passage 能否回答业务问题。"
        "输出 0 到 1 的分数，1 表示高度匹配，0 表示完全不相关。"
        "必须只输出 JSON，不要解释。格式："
        '{"scores":[{"index":0,"score":0.0,"constraint_applies":true,'
        '"constraint_matched":false,"constraint_reason":"文档类型不匹配"}]}'
    )
    user = json.dumps(
        {
            "current_date": date.today().isoformat(),
            "query": query,
            "items": items,
        },
        ensure_ascii=False,
    )

    # 下游 LLM 遇到限流/拖延时 8s 过紧，默认 30s，可通过环境变量调节。
    timeout = float(os.getenv("RAG_DOCUMENT_FILENAME_SCORE_TIMEOUT_S", "30"))
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
    matches = _parse_filename_score_response(raw, len(filenames))
    logger.debug(
        "[SelectDocuments] filename constraints query=%s items=%s",
        query[:80],
        [
            {
                "idx": i,
                "score": round(float(match["score"]), 4),
                "constraint_applies": match["constraint_applies"],
                "constraint_matched": match["constraint_matched"],
                "passage": filename[:80],
            }
            for i, (filename, match) in enumerate(zip(filenames, matches))
        ][:10],
    )
    return matches


async def _rerank_scores(
    clients: RAGClients,
    *,
    query: str,
    passages: list[str],
    label: str,
) -> list[float]:
    if not clients.rerank or not clients.rerank.enabled:
        raise RuntimeError("文档选择需要 rerank，但 rerank 未配置")

    # rerank provider（阿里云百炼等）限流重试会拖长单次耗时，8s 容易假超时，默认 30s。
    timeout = float(os.getenv("RAG_DOCUMENT_SELECT_RERANK_TIMEOUT_S", "30"))
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
    logger.debug(
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
    filename_matches, summary_scores = await asyncio.gather(
        _llm_filename_scores(query=query, filenames=filename_passages),
        _rerank_scores(
            clients,
            query=summary_query or query,
            passages=summary_passages,
            label="summary",
        ),
    )

    for doc, filename_text, filename_match, summary_score in zip(
        documents,
        filename_passages,
        filename_matches,
        summary_scores,
    ):
        name_score = float(filename_match["score"])
        final_score = filename_weight * name_score + summary_weight * summary_score
        # ── 分数字段写入约定 ──
        # 内部变量 `score` 在本行之前是“补召 RRF 归一化后的初始分”，
        # 本行之后将被覆盖为“filename_weight×name_score + summary_weight×summary_score”的融合分，
        # 供下游 shortlist / MMR / 排序使用；真实召回分以 `recall_score` 为准（不再修改）。
        # `document_match_scores` 中同时写入细分项作为“单一事实源”，下游尽量从里面取。
        doc["recall_score"] = float(doc.get("score", 0.0) or 0.0)
        doc["score"] = final_score
        doc["document_match_strategy"] = strategy
        doc["document_match_scores"] = {
            "filename": name_score,
            "summary": summary_score,
            "final": final_score,
            "recall": doc["recall_score"],
            "constraint_applies": bool(filename_match["constraint_applies"]),
            "constraint_matched": bool(filename_match["constraint_matched"]),
            "constraint_reason": filename_match["constraint_reason"],
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
    *,
    preference: str = "filename",
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Keep only documents that passed document selection before chunk recall.

    ``preference`` 控制 constraint 判定的强度：
    - ``filename``（默认）：用户明确点名文件/报告名，constraint_applies=True
      且 constraint_matched=False 时硬踢，避免卡入类型/年份不匹配的文档。
    - ``summary`` / ``balanced``：用户只描述主题，实体/产品/地区等词不应把
      “行业合集”这类天然不带实体名的文档硬踢。此时 constraint 只作为观测字段
      透传，不参与去留决策，避免主题分析场景全满贯。
    """
    if not documents:
        return [], [], {
            "max_documents": 0,
            "min_score": 0.0,
            "relative_to_best": 0.0,
            "best_score": 0.0,
            "constraint_mode": "hard" if preference == "filename" else "soft",
        }

    max_documents = max(1, _env_int("RAG_DOCUMENT_SELECT_FINAL_TOP_K", 3))
    min_score = _env_float("RAG_DOCUMENT_SELECT_MIN_SCORE", 0.15)
    relative_to_best = _env_float("RAG_DOCUMENT_SELECT_RELATIVE_SCORE", 0.85)
    diversity_strategy = os.getenv("RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY", "mmr").strip().lower()
    mmr_lambda = max(0.0, min(1.0, _env_float("RAG_DOCUMENT_SELECT_MMR_LAMBDA", 0.7)))
    # 只有 filename 档把 constraint 当硬门禁，主题分析场景不宜因实体未命中文件名而全卡。
    constraint_hard_gate = preference == "filename"
    constraint_passed: list[dict] = []
    constraint_rejected = 0
    for doc in documents:
        scores = doc.get("document_match_scores") or {}
        constraint_ok = (
            not bool(scores.get("constraint_applies"))
            or bool(scores.get("constraint_matched"))
        )
        if constraint_hard_gate and not constraint_ok:
            doc["document_constraint_rejected"] = True
            constraint_rejected += 1
            continue
        constraint_passed.append(doc)

    best_score = max(
        (float(doc.get("score", 0.0) or 0.0) for doc in constraint_passed),
        default=0.0,
    )
    cutoff = max(min_score, best_score * relative_to_best)
    eligible = [
        doc
        for doc in constraint_passed
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
        "constraint_rejected_documents": constraint_rejected,
        "constraint_mode": "hard" if constraint_hard_gate else "soft",
        "diversity_strategy": diversity_strategy,
        "mmr_lambda": mmr_lambda if diversity_strategy == "mmr" else None,
        "recall_source": "summary_metadata_multi_query_rrf",
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
        "constraint_applies": bool(scores.get("constraint_applies")),
        "constraint_matched": bool(scores.get("constraint_matched", True)),
        "constraint_reason": scores.get("constraint_reason", ""),
        "query_matches": [
            {
                "query": match.get("query", ""),
                "rank": int(match.get("rank", 0) or 0),
                "score": round(float(match.get("score", 0.0) or 0.0), 4),
            }
            for match in doc.get("query_matches", [])
            if isinstance(match, dict)
        ],
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
    document_queries = _normalize_document_queries(query, args.get("doc_queries"))
    summary_query = (args.get("summary_query") or args.get("analysis_query") or query).strip()
    hints = args.get("hints") if isinstance(args.get("hints"), dict) else {}
    document_match_strategy = _document_match_strategy(hints)
    # 开发时确认 hints 是否由 skill 层 LLM 正确传入：同时拍下原始 hint / env 覆盖 /
    # 最终选档，方便一眼定位权重为何落到 filename/summary/balanced。
    logger.info(
        "[RAG][select][hints] raw_pref=%r env_pref=%r final=%s weights=%s reason=%s",
        hints.get("document_match_preference"),
        os.getenv("RAG_DOCUMENT_MATCH_PREFERENCE"),
        document_match_strategy.get("document_match_preference"),
        document_match_strategy.get("weights"),
        document_match_strategy.get("reason") or "",
    )

    scope_plan = scope_plan_from_filters_or_context(args, ctx)
    scoped_kb_ids = scope_plan.flat_kb_ids() if scope_plan.has_scopes else []

    # 方案 B 短路:用户已在前端显式圈定文件时，直接透传，跳过 LLM
    # 文件名打分 / summary rerank / constraint kill。
    # 用户圈定的语义就是“就在这几份里找”，再跑 filename 硬门禁会把文件
    # 名不命中 query 实体的那些全部踢掉，造成 selected_count=0。
    user_selected_ids = scope_plan.flat_knowledge_ids() if scope_plan.has_scopes else []
    max_direct = _env_int("RAG_USER_SELECTED_MAX_DIRECT", 20)
    if user_selected_ids and len(user_selected_ids) <= max_direct:
        file_name_by_id = _collect_file_name_map(args, ctx or {})
        return _build_user_selected_result(
            query=query,
            document_queries=document_queries,
            document_match_strategy=document_match_strategy,
            scope_plan=scope_plan,
            user_selected_ids=user_selected_ids,
            file_name_by_id=file_name_by_id,
        )

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
        # 包含异常类型：TimeoutError/CancelledError 字面量为空，只打 %s 会丢失关键信息。
        logger.error(
            "[SelectDocuments] resolve_filters failed: %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return {"error": f"过滤条件解析失败: {type(e).__name__}: {e}"}
    logger.debug("[SelectDocuments] stage=resolve_filters cost_ms=%d", int((time.perf_counter() - _t_resolve) * 1000))

    summary_collection = clients.summary_collection
    logger.debug(
        "[SelectDocuments] query=%s, document_queries=%s, collection=%s, kb_ids=%s, tag_ids=%s, knowledge_ids=%s",
        query[:60], document_queries, summary_collection, resolved.kb_ids, resolved.tag_ids, resolved.knowledge_ids,
    )

    try:
        _t_embed = time.perf_counter()
        embeddings = await asyncio.gather(*(
            clients.embedding.embed_query(query_text)
            for query_text in document_queries
        ))
        query_embeddings = {
            query_text: embedding
            for query_text, embedding in zip(document_queries, embeddings)
        }
        logger.debug("[SelectDocuments] stage=embed cost_ms=%d", int((time.perf_counter() - _t_embed) * 1000))

        scopes = scope_plan.scopes if scope_plan.has_scopes else [RecallScope()]
        if not scopes:
            scopes = [RecallScope()]

        clients.milvus.ensure_collection_loaded(summary_collection)

        _t_milvus = time.perf_counter()
        rrf_k = int(os.getenv("RAG_DOCUMENT_SELECT_RRF_K", "60"))

        default_concurrency = os.getenv("RAG_RETRIEVAL_CONCURRENCY", "6")
        sem = asyncio.Semaphore(max(
            1,
            int(os.getenv("RAG_SCOPE_SELECT_CONCURRENCY", default_concurrency)),
        ))

        async def _search_scope_query(
            scope: RecallScope,
            query_text: str,
        ) -> tuple[str, list[dict]]:
            filter_expr = scope.to_filter_expr(include_enabled=True)
            async with sem:
                summary_result, metadata_result = await asyncio.gather(
                    asyncio.to_thread(
                        clients.milvus.search,
                        query_embedding=query_embeddings[query_text],
                        query_text=query_text,
                        retriever_type=RetrieverType.HYBRID,
                        collection_name=summary_collection,
                        filter_expr=filter_expr,
                        top_k=top_k,
                        output_fields=SUMMARY_OUTPUT_FIELDS,
                        hybrid_ranker="rrf",
                        rrf_k=rrf_k,
                        anns_field=SUMMARY_DENSE_FIELD,
                        sparse_anns_field=SUMMARY_SPARSE_FIELD,
                    ),
                    asyncio.to_thread(
                        clients.milvus.search,
                        query_embedding=query_embeddings[query_text],
                        query_text=query_text,
                        retriever_type=RetrieverType.HYBRID,
                        collection_name=summary_collection,
                        filter_expr=filter_expr,
                        top_k=top_k,
                        output_fields=SUMMARY_OUTPUT_FIELDS,
                        hybrid_ranker="rrf",
                        rrf_k=rrf_k,
                        anns_field=METADATA_DENSE_FIELD,
                        sparse_anns_field=METADATA_SPARSE_FIELD,
                    ),
                )
            docs = _rrf_merge_documents(
                [
                    ("summary", _documents_from_search_results(summary_result)),
                    ("metadata", _documents_from_search_results(metadata_result)),
                ],
                rrf_k=rrf_k,
                query=query_text,
                scope=scope,
            )
            logger.debug(
                "[SelectDocuments] scope=%s query=%s mode=summary_metadata_rrf docs=%d filter=%s rrf_k=%d",
                scope.kb_id or "*",
                query_text[:80],
                len(docs),
                filter_expr,
                rrf_k,
            )
            return query_text, docs

        scope_results = await asyncio.gather(
            *(
                _search_scope_query(scope, query_text)
                for scope in scopes
                for query_text in document_queries
            ),
            return_exceptions=True,
        )
        logger.debug("[SelectDocuments] stage=milvus cost_ms=%d", int((time.perf_counter() - _t_milvus) * 1000))

        query_results: dict[str, list[dict]] = {}
        for result in scope_results:
            if isinstance(result, Exception):
                logger.warning("[SelectDocuments] scoped search failed: %s", result)
                continue
            query_text, result_documents = result
            if not result_documents:
                continue
            current = query_results.setdefault(query_text, [])
            current.extend(result_documents)

        # 各 query 的候选独立精排、独立过阈值。不同 query 之间只取并集，
        # 不累加 RRF 分数，避免“每条 query 都泛化命中”的文档获得虚假优势。
        for result_documents in query_results.values():
            result_documents[:] = _dedup_documents(result_documents)
        all_documents = [
            document
            for result_documents in query_results.values()
            for document in result_documents
        ]
        await _fill_document_titles(clients, all_documents)

        excluded_by_id: dict[str, dict] = {}
        per_query_rerank_top_k = max(
            1,
            _env_int("RAG_DOCUMENT_SELECT_PER_QUERY_RERANK_TOP_K", 20),
        )

        async def _score_query_documents(
            query_text: str,
            result_documents: list[dict],
        ) -> tuple[str, list[dict], list[dict], dict[str, Any]]:
            included, excluded = _apply_excluded_files(result_documents, hints)
            for document in excluded:
                knowledge_id = str(document.get("knowledge_id") or "")
                if knowledge_id:
                    excluded_by_id[knowledge_id] = document
            included = included[:per_query_rerank_top_k]
            scored = await _apply_document_strategy(
                clients=clients,
                query=query_text,
                summary_query=query_text,
                documents=included,
                hints=hints,
            )
            selected, rejected, thresholds = _shortlist_documents(
                scored,
                preference=str(
                    document_match_strategy.get("document_match_preference")
                    or "filename"
                ),
            )
            return query_text, selected, rejected, thresholds

        rerank_sem = asyncio.Semaphore(max(
            1,
            int(os.getenv("RAG_DOCUMENT_RERANK_CONCURRENCY", default_concurrency)),
        ))

        async def _score_with_limit(
            query_text: str,
            result_documents: list[dict],
        ) -> tuple[str, list[dict], list[dict], dict[str, Any]]:
            async with rerank_sem:
                return await _score_query_documents(query_text, result_documents)

        scored_query_results = await asyncio.gather(*(
            _score_with_limit(query_text, result_documents)
            for query_text, result_documents in query_results.items()
        ))
        query_candidates = [
            (query_text, selected)
            for query_text, selected, _rejected, _thresholds in scored_query_results
        ]
        selected_documents, query_coverage = _union_query_candidates(query_candidates)
        selected_ids = {
            str(document.get("knowledge_id") or "")
            for document in selected_documents
        }
        rejected_by_id: dict[str, dict] = {}
        per_query_thresholds: dict[str, dict[str, Any]] = {}
        for query_text, _selected, rejected, thresholds in scored_query_results:
            per_query_thresholds[query_text] = thresholds
            for document in rejected:
                knowledge_id = str(document.get("knowledge_id") or "")
                if knowledge_id and knowledge_id not in selected_ids:
                    rejected_by_id[knowledge_id] = document
        rejected_documents = sorted(
            rejected_by_id.values(),
            key=lambda document: float(document.get("score", 0.0) or 0.0),
            reverse=True,
        )
        excluded_by_hint = list(excluded_by_id.values())
        select_thresholds = {
            "max_documents": max(final_top_k, len(selected_documents)),
            "query_coverage": query_coverage,
            "per_query": per_query_thresholds,
            "recall_source": "summary_metadata_rrf_per_query_union",
        }
        documents = all_documents
        selected_log = []
        for rank, document in enumerate(selected_documents, 1):
            item = _doc_log_item(document)
            item["rank"] = rank
            selected_log.append(item)
        logger.info(
            "[RAG][select] %s",
            json.dumps(
                {
                    "queries": document_queries,
                    "selected": selected_log,
                    "selected_count": len(selected_log),
                },
                ensure_ascii=False,
                default=str,
            ),
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
                "summary": f"未在文档摘要或元数据中找到匹配文档（查询: {query}）",
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
        # 同上：TimeoutError/CancelledError 字面量为空，必须带上类型名和 traceback。
        logger.error(
            "[SelectDocuments] 检索失败: %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        return {"error": f"文档选择失败: {type(e).__name__}: {e}"}
