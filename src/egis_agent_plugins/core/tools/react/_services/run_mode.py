"""ReAct run mode policy.

Run mode is request-scoped:
- flash: skip planning, hide todo_write from the model, force minimal frontend digest.
- pro: use planning guard + todo_write + final_answer.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from enum import Enum
from typing import Any


class RunMode(str, Enum):
    FLASH = "flash"
    PRO = "pro"


DEFAULT_RUN_MODE = RunMode.PRO
RUN_MODE_CONTEXT_KEY = "user:run_mode"
DISPLAY_MODE_CONTEXT_KEY = "user:display_mode"
FLASH_DISPLAY_MODE = "minimal"
PRO_DISPLAY_MODE = "detailed"

_CURRENT_RUN_MODE: ContextVar[RunMode | None] = ContextVar(
    "egis_react_run_mode",
    default=None,
)
_CURRENT_DISPLAY_MODE: ContextVar[str | None] = ContextVar(
    "egis_react_display_mode",
    default=None,
)


def normalize_run_mode(value: Any, default: RunMode = DEFAULT_RUN_MODE) -> RunMode:
    """Normalize user/env input to a supported ReAct run mode."""
    if isinstance(value, RunMode):
        return value
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return default
    if raw in {"flash", "fast", "quick"}:
        return RunMode.FLASH
    if raw in {"pro", "professional", "plan", "planning"}:
        return RunMode.PRO
    return default


def resolve_run_mode(
    context: dict[str, Any] | None = None,
    *,
    agent_id: str = "",
    default: RunMode = DEFAULT_RUN_MODE,
) -> RunMode:
    """Resolve run mode from request context, then environment, then default."""
    ctx = context or {}
    for key in (RUN_MODE_CONTEXT_KEY, "run_mode", "react:run_mode"):
        if ctx.get(key):
            return normalize_run_mode(ctx[key], default=default)

    env_keys: list[str] = []
    if agent_id:
        env_keys.append(f"{agent_id.upper()}_REACT_RUN_MODE")
    env_keys.extend(["EGIS_REACT_RUN_MODE", "REACT_RUN_MODE"])
    for key in env_keys:
        value = os.getenv(key)
        if value:
            return normalize_run_mode(value, default=default)

    current = _CURRENT_RUN_MODE.get()
    return current or default


def build_run_context_updates(mode: RunMode) -> dict[str, Any]:
    """Context updates merged into session state before the ReAct loop."""
    display_mode = FLASH_DISPLAY_MODE if mode == RunMode.FLASH else PRO_DISPLAY_MODE
    return {
        RUN_MODE_CONTEXT_KEY: mode.value,
        DISPLAY_MODE_CONTEXT_KEY: display_mode,
    }


def set_current_run_mode(mode: RunMode) -> None:
    """Expose request mode to helpers that do not receive tool context."""
    _CURRENT_RUN_MODE.set(mode)
    _CURRENT_DISPLAY_MODE.set(
        FLASH_DISPLAY_MODE if mode == RunMode.FLASH else PRO_DISPLAY_MODE
    )


def clear_current_run_mode() -> None:
    _CURRENT_RUN_MODE.set(None)
    _CURRENT_DISPLAY_MODE.set(None)


def get_current_display_mode() -> str | None:
    return _CURRENT_DISPLAY_MODE.get()


def filter_tool_schemas_for_run_mode(
    tool_schemas: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    *,
    agent_id: str = "",
) -> list[dict[str, Any]]:
    """Apply ReAct mode policy to mounted LLM tool schemas."""
    mode = resolve_run_mode(context, agent_id=agent_id)
    set_current_run_mode(mode)
    if mode != RunMode.FLASH:
        return tool_schemas
    return [
        schema for schema in tool_schemas
        if _tool_schema_name(schema) != "todo_write"
    ]


def _tool_schema_name(schema: dict[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(schema.get("name") or "")


__all__ = [
    "DISPLAY_MODE_CONTEXT_KEY",
    "FLASH_DISPLAY_MODE",
    "PRO_DISPLAY_MODE",
    "RUN_MODE_CONTEXT_KEY",
    "RunMode",
    "build_run_context_updates",
    "clear_current_run_mode",
    "filter_tool_schemas_for_run_mode",
    "get_current_display_mode",
    "normalize_run_mode",
    "resolve_run_mode",
    "set_current_run_mode",
]
