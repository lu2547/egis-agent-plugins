"""RAG Workflow Guards

每个 guard 接收 ``InstanceCtx``，满足条件 → return None；
不满足 → raise ``WorkflowRejection``。

Guard 执行顺序 = Transition 声明顺序（first-match-wins），
同一 action 的多条 Transition 共享 args_schema。
"""

from __future__ import annotations

import logging
import os

from ark_agentic.core.workflow.errors import WorkflowRejection
from ark_agentic.core.workflow.protocol import InstanceCtx

logger = logging.getLogger(__name__)

# ── 闲聊 / 无检索意图 判定 ──────────────────────────────────────────────

_CHITCHAT_KEYWORDS = frozenset({
    "你好", "hello", "hi", "嗨", "在吗", "在不在",
    "谢谢", "thanks", "thank you", "再见", "bye", "goodbye",
    "好的", "ok", "嗯", "嗯嗯", "收到", "了解", "明白了",
})


def _is_obvious_no_retrieval(ictx: InstanceCtx) -> None:
    """判断用户问题是否无需检索（纯闲聊/问候）。

    从 ``ictx.args["query"]`` 读取原始 query。
    如果 query 极短（≤15 字符）且完全匹配闲聊关键词，则放行。
    """
    if ictx.probing:
        return
    query = (ictx.args.get("query") or "").strip().lower()
    if not query:
        return  # 空 query 不拦截，让 effect 去处理
    if len(query) <= 15 and query in _CHITCHAT_KEYWORDS:
        return
    raise WorkflowRejection(
        code="guard",
        message=f"query '{query[:40]}' 需要检索，非闲聊",
    )


# ── Route 三分支 Guards ─────────────────────────────────────────────────

def _web_requested_but_disabled(ictx: InstanceCtx) -> None:
    """intent=web_search 且 web 搜索服务未配置。"""
    if ictx.probing:
        return
    rewrite = ictx.instance_data.get("rewrite") or {}
    intent = rewrite.get("intent", "")
    source = ictx.instance_data.get("source", "auto")

    wants_web = intent == "web_search" or source == "web"
    if not wants_web:
        raise WorkflowRejection(
            code="guard",
            message="not web intent and not web source",
        )

    # 判断 web search 是否已配置
    if _is_web_search_available():
        raise WorkflowRejection(
            code="guard",
            message="web search is available, should take web route",
        )

    # web wanted but not available → pass (route to no_evidence)


def _route_web(ictx: InstanceCtx) -> None:
    """intent=web_search 或 source=web 且 web 搜索可用。"""
    if ictx.probing:
        return
    rewrite = ictx.instance_data.get("rewrite") or {}
    intent = rewrite.get("intent", "")
    source = ictx.instance_data.get("source", "auto")

    wants_web = intent == "web_search" or source == "web"
    if not wants_web:
        raise WorkflowRejection(
            code="guard",
            message="not web intent and not web source",
        )
    if not _is_web_search_available():
        raise WorkflowRejection(
            code="guard",
            message="web search not available",
        )


def _route_internal(ictx: InstanceCtx) -> None:
    """默认兜底路由 — 无 guard，始终放行。

    作为 ``route`` 的最后一条 Transition，保证至少有一条能通过。
    """
    # 无 guard — 永远放行
    return None


# ── Recall Guards ────────────────────────────────────────────────────────

def _has_selected_docs(ictx: InstanceCtx) -> None:
    """``selected_knowledge_ids`` 非空，或允许 full-scope → 可以 recall。"""
    if ictx.probing:
        return
    ids = ictx.instance_data.get("selected_knowledge_ids") or []
    if ids or ictx.instance_data.get("allow_full_scope_recall"):
        return
    raise WorkflowRejection(
        code="guard",
        message="no selected_knowledge_ids, cannot do scoped recall",
    )


def _no_selected_docs(ictx: InstanceCtx) -> None:
    """``selected_knowledge_ids`` 为空且不允许 full-scope → insufficient。"""
    if ictx.probing:
        return
    ids = ictx.instance_data.get("selected_knowledge_ids") or []
    if not ids and not ictx.instance_data.get("allow_full_scope_recall"):
        return
    raise WorkflowRejection(
        code="guard",
        message=f"has {len(ids)} selected docs, not empty",
    )


# ── Rank Guards ──────────────────────────────────────────────────────────

def _has_candidates(ictx: InstanceCtx) -> None:
    """``candidates`` 非空 → 可以 rank。"""
    if ictx.probing:
        return
    cands = ictx.instance_data.get("candidates") or []
    if cands:
        return
    raise WorkflowRejection(
        code="guard",
        message="no candidates to rank",
    )


def _no_candidates(ictx: InstanceCtx) -> None:
    """``candidates`` 为空 → 标记 insufficient。"""
    if ictx.probing:
        return
    cands = ictx.instance_data.get("candidates") or []
    if not cands:
        return
    raise WorkflowRejection(
        code="guard",
        message=f"has {len(cands)} candidates, not empty",
    )


# ── Decide Guards ────────────────────────────────────────────────────────

def _evidence_sufficient(ictx: InstanceCtx) -> None:
    """证据充分 → emit references。"""
    if ictx.probing:
        return
    if ictx.instance_data.get("evidence_sufficient"):
        return
    raise WorkflowRejection(
        code="guard",
        message="evidence not sufficient",
    )


def _evidence_insufficient(ictx: InstanceCtx) -> None:
    """证据不足 → 进入 insufficient。"""
    if ictx.probing:
        return
    if not ictx.instance_data.get("evidence_sufficient"):
        return
    raise WorkflowRejection(
        code="guard",
        message="evidence is sufficient",
    )


# ── Retry Guards ─────────────────────────────────────────────────────────

def _can_retry(ictx: InstanceCtx) -> None:
    """``attempt < max_retries`` → 可以重试。"""
    if ictx.probing:
        return
    attempt = ictx.instance_data.get("attempt", 0)
    max_retries = ictx.instance_data.get("max_retries", 1)
    if attempt < max_retries:
        return
    raise WorkflowRejection(
        code="guard",
        message=f"retry exhausted: attempt={attempt}, max={max_retries}",
    )


def _retry_exhausted_has_partial(ictx: InstanceCtx) -> None:
    """重试耗尽但 ranked 非空 → 用部分结果。"""
    if ictx.probing:
        return
    attempt = ictx.instance_data.get("attempt", 0)
    max_retries = ictx.instance_data.get("max_retries", 1)
    if attempt >= max_retries:
        ranked = ictx.instance_data.get("ranked") or []
        if ranked:
            return
    raise WorkflowRejection(
        code="guard",
        message="retry not exhausted or no partial results",
    )


def _retry_exhausted_no_evidence(ictx: InstanceCtx) -> None:
    """重试耗尽且 ranked 为空 → no_evidence。"""
    if ictx.probing:
        return
    attempt = ictx.instance_data.get("attempt", 0)
    max_retries = ictx.instance_data.get("max_retries", 1)
    if attempt >= max_retries:
        ranked = ictx.instance_data.get("ranked") or []
        if not ranked:
            return
    raise WorkflowRejection(
        code="guard",
        message="retry not exhausted or has ranked results",
    )


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_web_search_available() -> bool:
    """判断 Web 搜索服务是否已配置。

    当前 web stage 是 stub，默认返回 False。
    后续接入搜索 API 时，可检查环境变量如 ``WEB_SEARCH_PROVIDER``。
    """
    provider = os.getenv("WEB_SEARCH_PROVIDER", "").strip()
    return bool(provider)
