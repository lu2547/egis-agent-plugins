"""Inject frontend RAG scope into model-produced tool calls.

The trace span for a tool call is created before ``RagTool.execute`` runs.
To make traces reflect the real frontend scope, the model response must be
patched in an after-model callback, before ark enters the tool phase.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ark_agentic.core.types import AgentMessage, ToolCall

from .scope_adapter import read_rag_state

logger = logging.getLogger(__name__)


def _parse_mapping(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _read_rag_filter_from_callback_ctx(ctx: Any) -> list[dict[str, Any]]:
    """Read ``rag_filter`` from both request context and session state."""
    candidates: list[Any] = []
    input_context = getattr(ctx, "input_context", None)
    if isinstance(input_context, dict):
        candidates.append(input_context)
        candidates.append(input_context.get("user:rag_state"))
        candidates.append(input_context.get("rag_state"))

    state = getattr(getattr(ctx, "session", None), "state", None)
    if isinstance(state, dict):
        candidates.append(state)
        candidates.append(state.get("user:rag_state"))
        candidates.append(state.get("rag_state"))

    for candidate in candidates:
        mapping = read_rag_state(candidate) if isinstance(candidate, dict) else {}
        if not mapping and isinstance(candidate, dict):
            mapping = candidate
        raw = mapping.get("rag_filter") or mapping.get("rag_filters")
        if isinstance(raw, list):
            scopes = [item for item in raw if isinstance(item, dict)]
            if scopes:
                return scopes
    return []


def _arguments_mapping(tool_call: ToolCall) -> dict[str, Any] | None:
    args = getattr(tool_call, "arguments", None)
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        parsed = _parse_mapping(args)
        if parsed:
            tool_call.arguments = parsed
            return parsed
    return None


def _inject_tool_call(tool_call: ToolCall, rag_filter: list[dict[str, Any]]) -> bool:
    if getattr(tool_call, "name", "") != "rag":
        return False

    args = _arguments_mapping(tool_call)
    if args is None:
        args = {}
        tool_call.arguments = args

    filters = _parse_mapping(args.get("filters"))
    if filters.get("rag_filter") == rag_filter:
        return False

    # Frontend selection is authoritative. Do not preserve model-guessed flat
    # filters such as {"kb_id": "..."} because they lose tag/file hierarchy.
    args["filters"] = {"rag_filter": rag_filter}
    return True


def inject_rag_scope_into_tool_calls(ctx: Any, response: AgentMessage) -> bool:
    """Patch ``rag`` tool calls with frontend ``rag_filter`` before tool phase."""
    rag_filter = _read_rag_filter_from_callback_ctx(ctx)
    if not rag_filter:
        return False

    changed = False
    for tool_call in response.tool_calls or []:
        changed = _inject_tool_call(tool_call, rag_filter) or changed

    if changed:
        logger.info(
            "[RagRetrieval] injected frontend rag_filter into model tool_calls: scopes=%d",
            len(rag_filter),
        )
    return changed
