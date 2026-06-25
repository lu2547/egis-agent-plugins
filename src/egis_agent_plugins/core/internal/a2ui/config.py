"""
Display Config — 从 .env 读取显示模式配置

配置优先级链（从高到低）：
1. 请求级覆盖: ChatRequest.context.display_mode / flash run_mode → user:display_mode
2. DISPLAY_TOOL_OVERRIDES: 工具级强制模式
3. Per-agent .env: {AGENT_ID}_DISPLAY_MODE
4. 全局 .env: DISPLAY_MODE

.env 配置项示例：
    DISPLAY_MODE=detailed
    DISPLAY_ALLOW_MODE_SWITCH=true
    INTELLIGENT_QA_DISPLAY_MODE=detailed
    ANNUITY_QUERY_DISPLAY_MODE=minimal
    DISPLAY_TOOL_OVERRIDES=todo_write:minimal
"""

from __future__ import annotations

import os
from typing import Any

from .types import DisplayMode


def get_display_mode(agent_id: str = "") -> DisplayMode:
    """读取 agent 的默认 display mode

    优先 per-agent env（如 INTELLIGENT_QA_DISPLAY_MODE），fallback 全局 DISPLAY_MODE。
    """
    if agent_id:
        env_key = f"{agent_id.upper()}_DISPLAY_MODE"
        val = os.getenv(env_key)
        if val:
            try:
                return DisplayMode(val.lower())
            except ValueError:
                pass
    # global fallback
    global_val = os.getenv("DISPLAY_MODE", "detailed")
    try:
        return DisplayMode(global_val.lower())
    except ValueError:
        return DisplayMode.DETAILED


def get_tool_overrides() -> dict[str, DisplayMode]:
    """读取工具级模式覆盖（DISPLAY_TOOL_OVERRIDES）

    格式：逗号分隔的 tool_name:mode 对
    示例：todo_write:minimal
    """
    raw = os.getenv("DISPLAY_TOOL_OVERRIDES", "")
    overrides: dict[str, DisplayMode] = {}
    for item in raw.split(","):
        item = item.strip()
        if ":" in item:
            tool, mode = item.split(":", 1)
            try:
                overrides[tool.strip()] = DisplayMode(mode.strip().lower())
            except ValueError:
                pass
    return overrides


def allow_mode_switch() -> bool:
    """前端是否允许用户动态切换显示模式"""
    return os.getenv("DISPLAY_ALLOW_MODE_SWITCH", "true").lower() == "true"


def resolve_display_mode(
    tool_name: str,
    context: dict[str, Any] | None = None,
    agent_id: str = "",
) -> DisplayMode:
    """解析当前生效的显示模式（完整优先级链）

    优先级：
    1. 请求级覆盖 (context 中的 user:display_mode / react flash)
    2. 工具级覆盖 (DISPLAY_TOOL_OVERRIDES)
    3. Per-agent .env (如 INTELLIGENT_QA_DISPLAY_MODE)
    4. 全局 .env (DISPLAY_MODE)

    Args:
        tool_name: 工具名称
        context: 工具执行上下文（含 user:display_mode）
        agent_id: Agent ID（用于 per-agent env 查找）
    """
    # 1. 请求级覆盖
    if context:
        ctx_mode = context.get("user:display_mode") or context.get("display_mode")
        if ctx_mode:
            try:
                return DisplayMode(str(ctx_mode).lower())
            except ValueError:
                pass

    try:
        from egis_agent_plugins.core.tools.react._services import get_current_display_mode

        current_mode = get_current_display_mode()
    except Exception:
        current_mode = None
    if current_mode:
        try:
            return DisplayMode(str(current_mode).lower())
        except ValueError:
            pass

    # 2. 工具级覆盖
    overrides = get_tool_overrides()
    if tool_name in overrides:
        return overrides[tool_name]

    # 3 & 4. agent 级 / 全局
    return get_display_mode(agent_id)
