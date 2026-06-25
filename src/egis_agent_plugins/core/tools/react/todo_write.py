"""任务规划工具

- 参数: task (string) + steps ([{id, description, status}])
- status: pending / in_progress / blocked / completed
- 输出: 格式化计划文本 + 进度统计
- 作为多步任务和跨 skill workflow 的计划状态源
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest,
    MinimalView,
    DetailedView,
    ViewSection,
    ToolDisplayType,
    apply_dual_layer,
    attach_frontend_digest,
    resolve_display_mode,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 公开 API：planning_state 更新双模式
#
# 模式 1（模型更新计划）：LLM 主动调 todo_write 工具 → TodoWriteTool.execute
# 模式 2（工具更新计划）：任意工具内部修改 planning_state 后，调用
#   emit_planning_state_refresh(result, planning_state) 同步刷新前端 todoCard
#
# 两种模式产出的 frontend_digest payload 结构一致（tool_name=todo_write +
# checklist），前端无需区分源头。
# ────────────────────────────────────────────


def build_todo_digest(
    task: str,
    steps: list[dict[str, str]],
    history: list[dict] | None = None,
) -> FrontendDigest:
    """构建 todo_write 的前端展示摘要（参考 PaiPai 层级风格）

    显示结构：
      ✓ 历史计划A（已完成 group）
        · 子步骤1
        · 子步骤2
      ✓ 历史计划B（已完成 group）
      ○ 当前步骤1
      ◎ 当前步骤2（进行中）
    """
    total = len(steps)
    completed = sum(1 for s in steps if s["status"] == "completed")
    in_progress = sum(1 for s in steps if s["status"] == "in_progress")

    # 自然语言进度描述
    if total == 0:
        summary_text = "暂未规划任何步骤"
        status = "warning"
    elif completed == total:
        summary_text = f"全部 {total} 个步骤已完成"
        status = "success"
    elif in_progress > 0:
        summary_text = f"已完成 {completed}/{total} 步，正在执行中"
        status = "loading"
    else:
        summary_text = f"已完成 {completed}/{total} 步，等待继续"
        status = "info"

    # 标题：直接用任务描述（截断到 60 字符）
    title = task[:60] + ("…" if len(task) > 60 else "")

    # 构建完整步骤列表（只显示当前计划步骤，不显示历史归档，避免重复）
    all_items: list[dict] = []

    # 当前计划步骤
    for s in steps:
        all_items.append({
            "id": s["id"], "text": s["description"], "status": s["status"],
        })

    sections: list[ViewSection] = []
    if all_items:
        sections.append(
            ViewSection(heading="", content_type="checklist", data=all_items)
        )

    return FrontendDigest(
        tool_name="todo_write",
        display_type=ToolDisplayType.CHECKLIST,
        minimal=MinimalView(
            title=title,
            summary=summary_text,
            icon="todo",
            status=status,
        ),
        detailed=DetailedView(title=title, sections=sections),
    )


class TodoWriteTool(AgentTool):
    """任务规划工具

    - task: 总体任务描述
    - steps: 里程碑步骤列表，每项含 id/description/status
    - 支持检索、多 skill 编排、生成/交付类任务追踪
    """

    name = "todo_write"
    description = """创建/更新结构化任务计划。详细使用规则（何时调、状态机、续接行为、跨 skill 编排）参考 react skill。

参数：
- task：任务总体描述
- steps：[{id, description, status}]
  - status 取值必须为：pending | in_progress | blocked | completed
  - description 用用户能看懂的自然语言，严禁加内部标签前缀。
  - steps 是一维平铺执行序列，不存在父子层级。同一时刻只允许 1 个
    in_progress；若需表达“调用某技能”及其内部步骤，请拆成独立的平铺步骤，
    并只把当前真正在做的那一步标为 in_progress，其余保持 pending/completed。
  - 【占位粗步骤原地细化】进入具体 skill 后的首次更新，必须用该 skill 的细步骤
    【整体替换】之前的占位粗步骤（如“调用 PPT Master 技能生成 N 页”），
    不允许粗步骤与细步骤并列保留。已 completed 的前置步骤原样保留。"""

    parameters = [
        ToolParameter(
            name="task",
            type="string",
            description="需要制定计划的复杂任务或问题",
            required=True,
        ),
        ToolParameter(
            name="steps",
            type="array",
            description=(
                "计划步骤数组，每项含状态追踪。"
                "格式：{id: 字符串, description: 字符串, "
                "status: 'pending'|'in_progress'|'blocked'|'completed'}。"
                "约束：同一时刻只能有 1 个 in_progress（平铺序列，无父子层级）。"
            ),
            required=True,
            items={"type": "object"},
        ),
    ]

    thinking_hint = "正在制定任务计划…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        args = tool_call.arguments or {}
        task = args.get("task", "").strip() or "No task description provided"
        steps_input = args.get("steps", [])

        # ── LLM 可能传 JSON 字符串而不是数组，做防御性解析 ──
        if isinstance(steps_input, str):
            steps_str = steps_input.strip()
            if steps_str.startswith("["):
                try:
                    parsed = json.loads(steps_str)
                    if isinstance(parsed, list):
                        steps_input = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
            if isinstance(steps_input, str):
                return AgentToolResult.error_result(
                    tool_call.id,
                    "steps 参数必须是列表，不能是字符串",
                )

        # 规范化步骤
        valid_statuses = {"pending", "in_progress", "blocked", "completed"}
        steps: list[dict[str, str]] = []
        for i, item in enumerate(steps_input):
            if not isinstance(item, dict):
                continue
            status = _normalize_status(item.get("status", "pending"))
            if status not in valid_statuses:
                status = "pending"
            steps.append({
                "id": str(item.get("id", f"step{i + 1}")),
                "description": str(item.get("description", "")),
                "status": status,
            })

        # ── 单一 in_progress 兜底：保留第一个，其余降级为 pending ──
        in_progress_count = sum(1 for s in steps if s["status"] == "in_progress")
        multi_in_progress_hint = ""
        if in_progress_count > 1:
            kept = False
            for s in steps:
                if s["status"] == "in_progress":
                    if kept:
                        s["status"] = "pending"
                    else:
                        kept = True
            multi_in_progress_hint = (
                f"\n⚠️ 检测到 {in_progress_count} 个 in_progress，"
                "已保留第一个，其余降级为 pending。同一时刻只允许 1 个 in_progress。\n"
            )
            logger.warning(
                "[TodoWrite] 多个 in_progress 被兜底降级: count=%d",
                in_progress_count,
            )

        # ── 计划历史管理：task 变更时归档旧计划 ──
        ctx = context or {}
        prev_state = (
            ctx.get("user:planning_state")
            or ctx.get("planning_state")
            or {}
        )
        prev_task = prev_state.get("task", "") if isinstance(prev_state, dict) else ""
        prev_steps = prev_state.get("steps", []) if isinstance(prev_state, dict) else []
        history: list[dict] = list(
            prev_state.get("history", []) if isinstance(prev_state, dict) else []
        )

        # 任务名称变更 → 旧计划归档到 history
        if prev_task and prev_task != task and prev_steps:
            history.append({"task": prev_task, "steps": prev_steps})
            history = history[-3:]  # 最多保留最近 3 条历史计划
            logger.info(
                "[TodoWrite] Archived previous plan: %s (%d steps)",
                prev_task[:60], len(prev_steps),
            )

        # 生成格式化输出
        output = _generate_plan_output(task, steps)
        if multi_in_progress_hint:
            output += multi_in_progress_hint
        logger.info("[TodoWrite] task=%s steps=%d history=%d", task[:80], len(steps), len(history))

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=output,
            is_error=False,
            metadata={
                "task": task,
                "steps": steps,
                "steps_json": json.dumps(steps, ensure_ascii=False),
                "total_steps": len(steps),
                "plan_created": True,
                "display_type": "plan",
                "state_delta": {
                    "user:planning_state": {
                        "task": task,
                        "steps": steps,
                        "total_steps": len(steps),
                        "history": history,
                    },
                },
            },
            events=[],
        )

        # 双层返回: llm_digest + frontend_digest（含历史计划）
        digest = build_todo_digest(task, steps, history)
        mode = resolve_display_mode("todo_write", context)
        apply_dual_layer(result, digest, output, mode)

        return result


def emit_planning_state_refresh(
    result: AgentToolResult,
    planning_state: dict[str, Any] | None,
) -> None:
    """工具内部更新 planning_state 后，刷新前端 todoCard 的统一入口。

    适用场景：任意工具（如 final_answer）通过 metadata.state_delta 修改了
    user:planning_state，需要让前端 todoCard 同步显示最新状态。

    实现：以 tool_name='todo_write' 名义生成 checklist frontend_digest 并
    attach 到 result.events，前端 streamParser 按 tool_name 路由到 todoCard，
    无需感知事件来源。

    Args:
        result: 当前工具的 AgentToolResult，事件会 append 到 result.events
        planning_state: 完整的 planning_state dict（含 task/steps/history）；
            若为 None 或缺字段则跳过（无副作用）
    """
    if not isinstance(planning_state, dict) or not planning_state:
        return
    task = planning_state.get("task", "")
    steps = planning_state.get("steps", []) or []
    history = planning_state.get("history", []) or []
    if not isinstance(steps, list):
        return
    digest = build_todo_digest(task, list(steps), list(history))
    attach_frontend_digest(result, digest)


# ────────────────────────────────────────────
# 辅助函数
# ────────────────────────────────────────────

_STATUS_EMOJI = {
    "pending": "⏳",
    "in_progress": "🔄",
    "blocked": "⛔",
    "completed": "✅",
    "skipped": "⏭️",
}

_STATUS_LABEL = {
    "pending": "待处理",
    "in_progress": "进行中",
    "blocked": "等待中",
    "completed": "已完成",
    "skipped": "已跳过",
}


_STATUS_ALIASES = {
    "待处理": "pending",
    "未开始": "pending",
    "进行中": "in_progress",
    "处理中": "in_progress",
    "等待中": "blocked",
    "阻塞": "blocked",
    "已阻塞": "blocked",
    "等待用户": "blocked",
    "waiting": "blocked",
    "waiting_user": "blocked",
    "已完成": "completed",
    "完成": "completed",
}


def _normalize_status(status: Any) -> str:
    """Normalize model-provided status labels to canonical English values."""
    raw = str(status or "pending").strip()
    return _STATUS_ALIASES.get(raw, raw)


def _format_plan_step(index: int, step: dict[str, str]) -> str:
    """格式化单个步骤"""
    status = step["status"]
    emoji = _STATUS_EMOJI.get(status, "⏳")
    label = _STATUS_LABEL.get(status, status)
    return f"  {index}. {emoji} [{label}] {step['description']}\n"


def _generate_plan_output(task: str, steps: list[dict[str, str]]) -> str:
    """生成计划输出文本"""
    output = "计划已创建\n\n"
    output += f"**任务**: {task}\n\n"

    if not steps:
        output += "注意：未提供具体步骤。建议创建 3-7 个可验证的执行里程碑。\n\n"
        output += "示例：\n"
        output += "1. 检索并读取目标资料\n"
        output += "2. 基于资料生成初稿并处理确认点\n"
        output += "3. 生成最终交付物或回答\n"
        return output

    # 统计状态
    pending_count = sum(1 for s in steps if s["status"] == "pending")
    in_progress_count = sum(1 for s in steps if s["status"] == "in_progress")
    blocked_count = sum(1 for s in steps if s["status"] == "blocked")
    completed_count = sum(1 for s in steps if s["status"] == "completed")
    total_count = len(steps)

    output += "**计划步骤**:\n\n"
    for i, step in enumerate(steps):
        output += _format_plan_step(i + 1, step)

    output += f"\n=== 任务进度 === 总计: {total_count} | ✅ {completed_count} | 🔄 {in_progress_count} | ⛔ {blocked_count} | ⏳ {pending_count}\n"

    return output
