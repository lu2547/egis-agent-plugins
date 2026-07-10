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
FRONTEND_DIGEST_EVENT = "frontend_digest"

_TOOL_LABELS = {
    "query_rewrite": "理解问题",
    "route": "选择检索路线",
    "document_select": "选择相关文档",
    "knowledge_search": "检索知识库片段",
    "web_search": "检索网络资料",
    "rank": "融合与精排",
    "read": "读取证据上下文",
    "quality_evaluate": "评估证据质量",
    "references": "整理引用来源",
}

_STATUS_LABELS = {
    "pending": "进行中",
    "done": "已完成",
    "error": "失败",
    "skipped": "已跳过",
}


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

    frontend_payload = _build_frontend_progress_payload(event)

    if emitter is not None:
        try:
            emitter(event)
            emitter({
                "type": "custom",
                "custom_type": FRONTEND_DIGEST_EVENT,
                "custom_data": frontend_payload,
            })
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
            emitter({
                "type": "custom",
                "custom_type": FRONTEND_DIGEST_EVENT,
                "custom_data": _build_frontend_progress_payload({
                    "type": PROGRESS_EVENT,
                    "tool": "references",
                    "status": "done",
                    "count": len(references),
                    "references": references[:5],
                }),
            })
        except Exception:
            logger.debug("[RAG Progress] emit references failed", exc_info=True)
    else:
        logger.debug("[RAG Progress] no emitter; references=%d", len(references))


def _build_frontend_progress_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Build a frontend_digest-compatible custom payload for Studio."""
    tool = str(event.get("tool") or "rag")
    status = str(event.get("status") or "pending")
    label = str(event.get("label") or _TOOL_LABELS.get(tool, tool))
    detail = str(event.get("detail") or _default_detail(event))
    count = event.get("count")
    step = {
        "key": str(event.get("key") or tool),
        "tool": tool,
        "label": label,
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status),
        "detail": detail,
        "count": count,
        "meta": {
            k: v
            for k, v in event.items()
            if k not in {"type", "tool", "status", "label", "detail", "count"}
        },
    }
    return {
        "tool_name": "rag_progress",
        "display_type": "search",
        "display_mode": "minimal",
        "view": {
            "title": "知识库检索过程",
            "summary": detail or label,
            "status": status,
            "step": step,
        },
        "sections": [],
        "step": step,
    }


def _default_detail(event: dict[str, Any]) -> str:
    tool = str(event.get("tool") or "")
    status = str(event.get("status") or "")
    count = event.get("count")
    if tool == "query_rewrite" and status == "done":
        rewrite = event.get("rewrite") or event.get("query") or ""
        route = event.get("route") or event.get("intent") or ""
        return f"改写为：{rewrite}；路线：{route}" if rewrite else "完成问题理解"
    if tool == "route":
        route = event.get("route") or ""
        return f"选择路线：{route}" if route else "完成路线选择"
    if tool == "document_select" and status == "done":
        return f"选中 {count or 0} 个候选文档"
    if tool == "knowledge_search" and status == "done":
        return f"召回 {count or 0} 个候选片段"
    if tool == "rank" and status == "done":
        return f"保留 {count or 0} 个精排结果"
    if tool == "read" and status == "done":
        return f"读取 {count or 0} 个证据块"
    if tool == "references" and status == "done":
        return f"整理 {count or 0} 条引用"
    if count is not None:
        return f"{_TOOL_LABELS.get(tool, tool)}：{count}"
    return _TOOL_LABELS.get(tool, tool)
