"""Query Rewrite Stage —— LLM 意图识别 + 查询改写。

模块级 ``run()`` 是 workflow / 单测的唯一入口。
"""

from __future__ import annotations

import logging
from typing import Any

from .service import QueryRewriteService, RewriteResult

logger = logging.getLogger(__name__)


# ── 模块级缓存：LLM service 单例（避免每次 run 都 create_chat_model_from_env）──
_service_singleton: QueryRewriteService | None = None


def _get_service() -> QueryRewriteService:
    global _service_singleton
    if _service_singleton is None:
        from ark_agentic.core.llm import create_chat_model_from_env

        llm = create_chat_model_from_env()
        _service_singleton = QueryRewriteService(llm=llm)
    return _service_singleton


async def run(
    *,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    clients: Any | None = None,  # noqa: ARG001 — rewrite 阶段不消费 clients
) -> dict[str, Any]:
    """Query rewrite 业务核心。

    Returns dict: ``{"intent", "keywords", "sub_queries", "rewrite_query"}``
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query 不能为空"}

    history = args.get("history")
    language = args.get("language") or "zh-CN"
    pinned = args.get("pinned_knowledge_ids")

    try:
        service = _get_service()
    except Exception as e:
        logger.warning("[QueryRewrite] LLM 初始化失败，回落原始 query: %s", e)
        return RewriteResult(
            rewrite_query=query, sub_queries=[query], intent="kb_search", keywords=[],
        ).to_dict()

    try:
        result = await service.rewrite(
            query, history=history, language=language, pinned_knowledge_ids=pinned,
        )
    except Exception as e:
        logger.warning("[QueryRewrite] rewrite 失败，回落原始 query: %s", e)
        result = RewriteResult(
            rewrite_query=query, sub_queries=[query], intent="kb_search", keywords=[],
        )

    return result.to_dict()
