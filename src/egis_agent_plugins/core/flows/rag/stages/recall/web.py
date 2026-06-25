"""Web 搜索 stage（Stub）— 本期返回空结果占位。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run(
    *,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,  # noqa: ARG001
    clients: Any | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Web 搜索业务核心 — Stub 返回空结果。"""
    query = args.get("query", "")
    logger.info("[WebSearch][Stub] query=%s", query[:80])
    return {
        "query": query,
        "results": [],
        "count": 0,
        "summary": "Web 搜索服务暂未配置，请使用知识库检索代替。",
        "display_type": "web_search",
        "_stub": True,
    }
