"""SSE 进度事件 emit 辅助

在 RAG workflow 的关键 effect 中 emit 进度事件，
与 ReAct loop 解耦，通过 ``context["emit_event"]`` 回调推送。

事件格式::

    {"type": "rag_progress", "tool": "query_rewrite", "status": "pending"}
    {"type": "rag_progress", "tool": "knowledge_search", "status": "done", "count": 12}
    {"type": "references", "data": [...]}
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Event types ──────────────────────────────────────────────────────────

PROGRESS_EVENT = "rag_progress"
REFERENCES_EVENT = "references"


def _get_emitter(context: dict[str, Any] | None) -> Callable[[dict[str, Any]], None] | None:
    """从 context 中取 emit_event 回调（可选）。"""
    if context is None:
        return None
    fn = context.get("emit_event")
    if callable(fn):
        return fn
    return None


def emit_progress(
    context: dict[str, Any] | None,
    *,
    tool: str,
    status: str,
    count: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit RAG 进度事件。静默失败（无 emitter 时 log debug）。"""
    emitter = _get_emitter(context)
    event: dict[str, Any] = {
        "type": PROGRESS_EVENT,
        "tool": tool,
        "status": status,
    }
    if count is not None:
        event["count"] = count
    if extra:
        event.update(extra)

    if emitter is not None:
        try:
            emitter(event)
        except Exception:
            logger.debug("[RAG Progress] emit_event failed for %s", event, exc_info=True)
    else:
        logger.debug("[RAG Progress] no emitter; event=%s", event)


def emit_references(
    context: dict[str, Any] | None,
    references: list[dict[str, Any]],
) -> None:
    """Emit references 事件（前置推送，不等答案写完）。"""
    emitter = _get_emitter(context)
    event: dict[str, Any] = {
        "type": REFERENCES_EVENT,
        "data": references,
    }
    if emitter is not None:
        try:
            emitter(event)
        except Exception:
            logger.debug("[RAG Progress] emit references failed", exc_info=True)
    else:
        logger.debug("[RAG Progress] no emitter; references=%d", len(references))
