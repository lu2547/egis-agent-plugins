"""ReAct pro 模式的规划护栏回调。
触发：
1、Agent 初始化时，EgisBaseAgent.build_callbacks() 挂载 build_react_callbacks()。
2、每次用户请求开始时，before_agent 先执行 _inject_run_mode()，解析本轮是 flash 还是 pro。
3、每一轮 LLM 返回之后、工具执行之前，触发 after_model 里的 _guard_by_mode()。
4、如果是 flash，直接跳过 planning guard。
5、如果是 pro，才调用 planning_guard 检查这一轮 LLM 返回的 tool_calls。
规则：
1、没有 tool call：注入一个启动计划。
2、第一个就是 todo_write：放行。
3、只有一个 final_answer：放行。
4、以业务工具开头：前面 prepend 一个 todo_write，同时保留原业务工具继续执行。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from ark_agentic.core.runtime.callbacks import CallbackResult, HookAction, RunnerCallbacks
from ark_agentic.core.types import AgentMessage, ToolCall

logger = logging.getLogger(__name__)

_DIAG_LOG_PATH = os.environ.get("PLANNING_GUARD_DIAG_LOG", "").strip()


def _diag(msg: str) -> None:
    """诊断日志：始终输出到 stdlib logger；设置 PLANNING_GUARD_DIAG_LOG 时才追加文件。"""
    logger.info(msg)
    if _DIAG_LOG_PATH:
        try:
            with open(_DIAG_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass

PLANNING_TOOL_NAME = "todo_write"
TERMINAL_TOOL_NAME = "final_answer"

# ── 默认引导计划模板（通用，无领域假设） ────────────────────────

_DEFAULT_STEPS: list[dict[str, str]] = [
    {"id": "s1", "description": "分析用户请求，确定需要调用的技能和执行路径", "status": "pending"},
    {"id": "s2", "description": "按选定技能执行任务并交付结果", "status": "pending"},
]


def _build_steps(user_input: str, plan_template: Any | None) -> list[dict[str, Any]]:
    """根据计划模板解析步骤列表。

    ``plan_template`` 可为：
    - ``None`` → 使用 ``_DEFAULT_STEPS``
    - ``list[dict]`` → 直接返回
    - ``Callable[[str], list[dict]]`` → 以 *user_input* 调用生成
    """
    if plan_template is None:
        return [dict(s) for s in _DEFAULT_STEPS]
    if callable(plan_template):
        result = plan_template(user_input)
        if isinstance(result, list):
            return result
        return [dict(s) for s in _DEFAULT_STEPS]
    if isinstance(plan_template, list):
        return list(plan_template)
    return [dict(s) for s in _DEFAULT_STEPS]


def _bootstrap_plan(
    user_input: str,
    *,
    plan_template: Any | None = None,
) -> ToolCall:
    """模型跳过规划时，创建一个启动用的 todo_write 计划。"""
    task = user_input.strip() or "处理用户请求"
    steps = _build_steps(user_input, plan_template)
    return ToolCall.create(PLANNING_TOOL_NAME, {"task": task, "steps": steps})


def _passthrough_plan(planning_state: dict) -> ToolCall:
    """已有计划时的装配注入：原样复制 steps，不修改任何 status。

    设计意图：guard 不猜 LLM 意图，只把当前状态原样护送一轮，
    LLM 下轮看到现状后自己决定怎么推进。
    """
    task = planning_state.get("task", "处理用户请求")
    steps = planning_state.get("steps", [])
    return ToolCall.create(
        PLANNING_TOOL_NAME,
        {"task": task, "steps": [dict(s) for s in steps if isinstance(s, dict)]},
    )


def build_planning_callbacks(
    *,
    plan_template: Any | None = None,
) -> RunnerCallbacks:
    """构建 ReAct 规划回调，可选注入领域特定的计划模板。

    Args:
        plan_template: 可选步骤模板。
            - ``None``: 使用通用 2 步模板。
            - ``list[dict]``: 静态步骤列表。
            - ``Callable[[str], list[dict]]``: 以用户输入动态生成步骤。
    """
    _tmpl = plan_template  # 闭包捕获

    async def _guard(ctx, *, turn: int, response: AgentMessage) -> CallbackResult | None:
        """确保每轮第一个工具调用是 todo_write（纯对话除外）。

        状态判定完全基于：
        - has_planning_state：session state 中是否存在 planning_state
        - tool_calls 批次内容（不依赖 turn）

        guard 不改 step status，不猜 LLM 意图。
        """
        state = ctx.session.state or {}
        tool_calls = response.tool_calls or []
        tool_names = [tc.name for tc in tool_calls]
        has_planning_state = bool(state.get("user:planning_state"))
        state_keys = sorted(state.keys()) if isinstance(state, dict) else []
        _diag(
            f"[planning_guard] turn={turn} has_planning_state={has_planning_state} "
            f"tool_calls={tool_names} state_keys={state_keys}"
        )

        # 规则1：空批次 → bootstrap
        if not tool_calls:
            response.tool_calls = [_bootstrap_plan(ctx.user_input, plan_template=_tmpl)]
            response.content = None
            _diag("[planning_guard] empty tool_calls -> bootstrap todo_write")
            return CallbackResult(action=HookAction.PASS, response=response)

        # 规则2：第一个工具是 todo_write → 放行（同批含 final_answer 也不干预）
        if tool_calls[0].name == PLANNING_TOOL_NAME:
            return None

        # 规则3：单独 final_answer → 一律放行
        # final_answer 自己负责 planning_state 更新（通过 is_blocking 参数语义内聚）：
        #   - is_blocking=True  → 在其 metadata.state_delta 中把 in_progress 改 blocked，
        #     或回滚最后一个 completed 为 blocked
        #   - is_blocking=False → 不动 state，前端 finalizeTodoCard 在 run_finished 时归一
        # guard 不再为 final_answer 越俎代庖。
        if (
            len(tool_calls) == 1
            and tool_calls[0].name == TERMINAL_TOOL_NAME
        ):
            return None

        # 规则4：其他场景——前置注入 todo_write，保留原工具调用
        # 旧实现采用「替换」策略丢弃原 tool_calls，期望 LLM 下轮重新决策；
        # 但 LLM 常被 skill「续接先 todo_write」规则裹挟反复只调 todo_write，
        # 导致 plan ↔ 业务工具翻烫硬币的死循环。改为 prepend 后既保住
        # planning_state（todo_write 透传），又让业务工具实际执行，强制前进。
        if has_planning_state:
            ps = state.get("user:planning_state", {}) or {}
            response.tool_calls = [_passthrough_plan(ps), *tool_calls]
            _diag(
                f"[planning_guard] prepend passthrough todo_write (state preserved, "
                f"original tools kept: {tool_names})"
            )
        else:
            response.tool_calls = [
                _bootstrap_plan(ctx.user_input, plan_template=_tmpl),
                *tool_calls,
            ]
            _diag(
                f"[planning_guard] prepend bootstrap todo_write (no state, "
                f"original tools kept: {tool_names})"
            )
        return CallbackResult(action=HookAction.PASS, response=response)

    return RunnerCallbacks(after_model=[_guard])
