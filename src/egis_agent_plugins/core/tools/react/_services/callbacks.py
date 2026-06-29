"""ReAct lifecycle callbacks.
1. build_react_callbacks — Agent 构造时调用一次
ark BaseAgent.__init__()
  └─ line 246: self._callbacks = self.build_callbacks() or RunnerCallbacks()
                                          ↓
                              EgisBaseAgent.build_callbacks()  ← override
                                          ↓
                              build_react_callbacks(agent_id=...)
                                          ↓
                              返回 RunnerCallbacks(before_agent=..., after_model=..., after_agent=...)

2.self._callbacks 里的函数在 ReAct 循环的特定位置被调用：
agent.run(user_message)
  │
  ├─ before_agent 钩子  ← line 673: run_hooks(self._callbacks.before_agent, ...)
  │    └─ _inject_run_mode()        # 设置 ContextVar
  │    └─ _emit_react_status()      # 推送 mode-card
  │    └─ _bootstrap_plan_if_needed() # flash 注入引导
  │
  ├─ ── ReAct Loop ──
  │    │
  │    ├─ _model_phase()  ← 每轮调用
  │    │    │
  │    │    ├─ before_model 钩子  ← line 1020
  │    │    │
  │    │    ├─ filter_tool_schemas_for_run_mode()  ← ★ 这里！每轮 LLM 调用前
  │    │    │
  │    │    ├─ LLM 调用
  │    │    │
  │    │    └─ after_model 钩子  ← line 1049
  │    │         └─ planning_guard()   # 检查 LLM 是否以 todo_write 开头
  │    │
  │    └─ _tool_phase()  ← 每轮工具执行
  │         └─ TodoWriteTool / FinalAnswerTool / 业务工具
  │
  └─ after_agent 钩子  ← line 723
       └─ _cleanup_plan_state()  # 清理 planning_state
"""

from __future__ import annotations

from typing import Any

from ark_agentic.core.runtime.callbacks import CallbackResult, RunnerCallbacks
from ark_agentic.core.types import AgentMessage

from egis_agent_plugins.core.flows.rag._services.tool_call_scope import (
    inject_rag_scope_into_tool_calls,
)

from .planning_guard import build_planning_callbacks
from .run_mode import (
    RunMode,
    build_run_context_updates,
    clear_current_run_mode,
    resolve_run_mode,
    set_current_run_mode,
)


def build_react_callbacks(
    *,
    agent_id: str = "",
    plan_template: Any | None = None,
) -> RunnerCallbacks:
    """Build request-switchable ReAct callbacks.

    The callback set is static, while run mode is resolved per request from
    context/env. This keeps one Agent instance able to serve flash and pro.
    """
    planning_callbacks = build_planning_callbacks(plan_template=plan_template)

    async def _inject_run_mode(ctx, **_: Any) -> CallbackResult:
        mode = resolve_run_mode(ctx.input_context, agent_id=agent_id)
        set_current_run_mode(mode)
        return CallbackResult(context_updates=build_run_context_updates(mode))

    async def _guard_by_mode(
        ctx,
        *,
        turn: int,
        response: AgentMessage,
        **kwargs: Any,
    ) -> CallbackResult | None:
        mode = resolve_run_mode(ctx.input_context, agent_id=agent_id)
        set_current_run_mode(mode)
        inject_rag_scope_into_tool_calls(ctx, response)
        if mode == RunMode.FLASH:
            return None

        for guard in planning_callbacks.after_model:
            result = await guard(ctx, turn=turn, response=response, **kwargs)
            if result is not None:
                return result
        return None

    async def _clear_run_mode(ctx, **_: Any) -> None:
        clear_current_run_mode()
        return None

    return RunnerCallbacks(
        before_agent=[_inject_run_mode],
        after_model=[_guard_by_mode],
        after_agent=[_clear_run_mode],
    )


__all__ = ["build_react_callbacks"]
