"""最终回答工具

- LLM 调用此工具提交最终回答，触发 STOP 终止 ReAct loop
- answer 参数包含完整的 Markdown 格式回答
- is_blocking 参数显式声明本次是否为阻塞型确认点（而非最终交付）

工作机制:
- AgentToolResult + loop_action=STOP → Runner 终止 loop
- is_blocking=True 时由 final_answer 自身写 planning_state 的 state_delta：
  - 若存在 in_progress step → 改为 blocked
  - 若无 in_progress 但有 completed → 把最后一个 completed 回滚为 blocked
  - 否则不动 state（罕见，避免误伤）
- is_blocking=False 不动 state；前端 finalizeTodoCard 在 run_finished 时归一
  （in_progress → completed，blocked/pending 保留）
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolLoopAction, ToolResultType

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest, MinimalView, DetailedView, ViewSection,
    ToolDisplayType, apply_dual_layer,
)
from .todo_write import emit_planning_state_refresh

logger = logging.getLogger(__name__)


# ── 引用映射: [N] → <kb> ──────────────────────────────────────────────

_KB_TAG_RE = re.compile(r'<kb\b([^>]*)/\s*>')
_CITE_RE = re.compile(r'\[(\d+)\]')  # 匹配 [1] [2] 等编号引用



def _sanitize_kb_tags(
    answer: str,
    valid_refs: list[dict[str, Any]],
    evidence: list[dict[str, Any]] | None = None,
) -> str:
    """将 LLM 回答中的 [N] 编号引用转换为真实 <kb> 标签。

    1. 构建 evidence_index → chunk_id / doc_title 映射
    2. 将 [N] 替换为 <kb doc="..." chunk_id="..." />
       - 同一 chunk_id 复用同一编号（去重）
       - 无效编号保留原文 [N]
    3. 删除 LLM 可能幻觉的 <kb> 标签
    """
    if not valid_refs or not evidence:
        # 没有证据映射 → 只删除残留 <kb> 标签
        return _KB_TAG_RE.sub("", answer)

    valid_chunk_ids = {
        ref.get("chunk_id", "") for ref in valid_refs
        if ref.get("chunk_id", "")
    }

    idx_to_chunk: dict[int, str] = {}
    idx_to_doc: dict[int, str] = {}
    for i, ev in enumerate(evidence, 1):
        chunk_id = ev.get("chunk_id", "")
        doc = ev.get("doc_title") or ev.get("knowledge_title", "")
        if chunk_id and doc and chunk_id in valid_chunk_ids:
            idx_to_chunk[i] = chunk_id
            idx_to_doc[i] = doc

    # ── 替换 [N] → <kb> 标签（去重） ──
    seen_chunks: dict[str, int] = {}  # chunk_id → first citation index
    dedup_map: dict[int, int] = {}     # original_idx → display_idx

    def _replace_cite(m: re.Match) -> str:
        n = int(m.group(1))
        chunk_id = idx_to_chunk.get(n)
        doc = idx_to_doc.get(n)
        if not chunk_id or not doc:
            return m.group(0)  # 无效编号，保留原文
        # 去重：同一 chunk_id 复用首次出现的编号
        if chunk_id in seen_chunks:
            display = seen_chunks[chunk_id]
        else:
            display = len(seen_chunks) + 1
            seen_chunks[chunk_id] = display
        dedup_map[n] = display
        return f'<kb doc="{doc}" chunk_id="{chunk_id}" />'

    # ── 先删除 LLM 可能幻觉的 <kb> 标签 ──
    cleaned = _KB_TAG_RE.sub("", answer)

    # ── 再将 [N] 替换为真实 <kb> 标签 ──
    cleaned = _CITE_RE.sub(_replace_cite, cleaned)

    return cleaned


def _parse_bool(value: Any) -> bool:
    """严格 bool 解析：防止 "false" 字符串被 bool() 当成 True。

    - None → False
    - True / False → 直接返回
    - "true" / "1" → True
    - "false" / "0" / "" → False
    - 其他值 → False + warning
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("true", "1"):
            return True
        if lower in ("false", "0", ""):
            return False
        logger.warning("[FinalAnswer] 无法解析的 is_blocking 值: %r, 归一为 False", value)
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    logger.warning("[FinalAnswer] 非预期的 is_blocking 类型: %s, 归一为 False", type(value).__name__)
    return False


def _apply_blocking_to_planning_state(
    context: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """根据 is_blocking=True 语义计算 planning_state 的 state_delta。

    规则：
    - 存在 in_progress step → 把首个 in_progress 改为 blocked
    - 无 in_progress 但有 completed → 把最后一个 completed 回滚为 blocked
      （贴合「还没真完成就等确认」的事实，避免前端「全部完成 + 等待确认」矛盾）
    - 既无 in_progress 又无 completed（罕见）→ 返回 (None, None)，不动 state
    - 无 planning_state → 返回 (None, None)

    返回：(state_delta, refreshed_planning_state)
      - state_delta：供 runner 应用到 session.state
      - refreshed_planning_state：供后续生成 frontend_digest 刷新前端 todoCard
    """
    ctx = context or {}
    ps = ctx.get("user:planning_state") or {}
    if not isinstance(ps, dict) or not ps:
        return None, None
    raw_steps = ps.get("steps", []) or []
    if not isinstance(raw_steps, list):
        return None, None

    new_steps: list[dict[str, Any]] = [
        dict(s) for s in raw_steps if isinstance(s, dict)
    ]
    if not new_steps:
        return None, None

    flipped = False
    # 1) 优先把首个 in_progress → blocked
    for s in new_steps:
        if s.get("status") == "in_progress":
            s["status"] = "blocked"
            flipped = True
            break

    # 2) 无 in_progress → 回滚最后一个 completed → blocked
    if not flipped:
        last_completed_idx = -1
        for idx, s in enumerate(new_steps):
            if s.get("status") == "completed":
                last_completed_idx = idx
        if last_completed_idx >= 0:
            new_steps[last_completed_idx]["status"] = "blocked"
            flipped = True

    if not flipped:
        # 3) 既无 in_progress 又无 completed → 不动 state
        return None, None

    refreshed = {
        "task": ps.get("task", ""),
        "steps": new_steps,
        "total_steps": len(new_steps),
        "history": list(ps.get("history", []) or []),
    }
    state_delta = {"user:planning_state": refreshed}
    return state_delta, refreshed


class FinalAnswerTool(AgentTool):
    """最终回答工具

    - LLM 必须在完成所有检索和推理后调用此工具
    - answer 参数包含完整回答（Markdown 格式，含引用和格式化）
    - is_blocking 显式声明本次是终结交付还是阻塞型确认
    - 调用此工具后 agent loop 自动终止（loop_action=STOP）
    """

    name = "final_answer"
    description = """提交最终回答给用户，触发本轮 ReAct loop 终止。详细调用时机参考 react skill。

参数：
- answer：Markdown 格式完整回答，包含所有引用、结构与格式化。
- is_blocking（默认 false）：是否为阻塞型确认点。
  - true：本次是等待用户确认/输入（如大纲、参数确认）。final_answer 会自动把当前 in_progress step 标 blocked；若无 in_progress 则把最后一个 completed 回滚为 blocked，让前端正确显示「等待中」。
  - false：本次是最终交付，不动 state；前端在 run_finished 时把 in_progress 归一为 completed。
- 严禁用关键字（如「确认」「是否」「？」）猜测意图，必须显式传 is_blocking。"""

    parameters = [
        ToolParameter(
            name="answer",
            type="string",
            description="完整的最终回答，Markdown 格式。包含所有引用、结构、和格式化。",
            required=True,
        ),
        ToolParameter(
            name="is_blocking",
            type="boolean",
            description=(
                "是否为阻塞型确认点。true=等待用户确认/输入（自动把 in_progress 标 blocked，"
                "或回滚最后一个 completed 为 blocked）；false=最终交付，不动 state。"
                "默认 false。严禁基于关键字猜测，必须由 LLM 显式声明。"
            ),
            required=False,
        ),
    ]

    thinking_hint = "正在组织最终回答…"

    async def execute(
        self,
        tool_call,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        """执行最终回答

        - 验证 answer 非空
        - 根据 is_blocking 决定是否更新 planning_state
        - 返回成功结果 + STOP 信号终止 loop
        """
        args = tool_call.arguments or {}
        answer = args.get("answer", "").strip()
        # 防御：LLM 有时误传 content 而非 answer
        if not answer:
            answer = args.get("content", "").strip()
        is_blocking = _parse_bool(args.get("is_blocking", False))

        # 引用映射: [N] → <kb> 标签
        valid_refs = (context or {}).get("_valid_kb_refs")
        evidence_refs = (context or {}).get("_rag_evidence_refs")
        if valid_refs:
            before = answer
            answer = _sanitize_kb_tags(answer, valid_refs, evidence_refs or [])
            if answer != before:
                logger.info(
                    "[FinalAnswer] mapped [N] → <kb> (refs=%d, evidence=%d)",
                    len(valid_refs), len(evidence_refs or []),
                )
        else:
            # 没有 RAG 证据时，仍清掉模型伪造的 <kb> 标签。
            answer = _sanitize_kb_tags(answer, [], [])
            logger.debug("[FinalAnswer] no _valid_kb_refs in context, stripped any <kb> tags")

        if not answer:
            if is_blocking:
                # BLOCKING 步骤（卡片自解释）允许 answer 为空
                logger.info("[FinalAnswer] is_blocking=True with empty answer — allowed")
            else:
                return AgentToolResult.error_result(
                    tool_call.id,
                    "answer 参数不能为空",
                )

        metadata: dict[str, Any] = {"is_blocking": is_blocking}
        refreshed_ps: dict[str, Any] | None = None
        if is_blocking:
            delta, refreshed_ps = _apply_blocking_to_planning_state(context)
            if delta:
                metadata["state_delta"] = delta
                logger.info(
                    "[FinalAnswer] is_blocking=True applied state_delta (planning_state updated)"
                )
            else:
                logger.info(
                    "[FinalAnswer] is_blocking=True but no actionable step found; state untouched"
                )

        logger.info(
            f"[FinalAnswer] Answer length: {len(answer)} characters | is_blocking={is_blocking}"
        )

        result = AgentToolResult(
            tool_call_id=tool_call.id,
            result_type=ToolResultType.TEXT,
            content=answer,
            is_error=False,
            metadata=metadata,
            loop_action=ToolLoopAction.STOP,
            events=[],
        )
        preview = answer[:60].replace("\n", " ")
        title_preview = answer.replace("\n", " ")[:50]
        digest = FrontendDigest(
            tool_name="final_answer",
            display_type=ToolDisplayType.TEXT,
            minimal=MinimalView(
                title=title_preview or "最终回答",
                summary=f"已生成 {len(answer)} 字的完整回复",
                icon="answer",
                status="success",
            ),
            detailed=DetailedView(title="最终回答", sections=[
                ViewSection(heading="回答预览", content_type="text", data=preview + "..."),
            ]),
        )
        apply_dual_layer(result, digest, answer)

        # is_blocking=True 且 planning_state 被刷新了 → 调用 todo_write 公开的
        # emit_planning_state_refresh 同步前端 todoCard，避免「全部完成 + 等待确认」矛盾。
        if refreshed_ps is not None:
            emit_planning_state_refresh(result, refreshed_ps)

        return result
