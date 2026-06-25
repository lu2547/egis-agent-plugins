"""RAG 范围硬覆盖：从工具 ctx 中读取前端注入的 user:rag_state，
作为强制过滤条件覆盖 LLM 提供的工具入参。

约定（前端 → ark_agentic chat.py → session.state → tool ctx）：
    ChatRequest.context.rag_state = {
        "knowledge_base_ids": [...],   # 限定知识库范围
        "tag_ids":            [...],   # 限定标签范围
        "file_ids":           [...],   # 限定文档范围（映射到工具内部的 knowledge_ids）
        "hash":               "...",   # 前端用于增量同步的指纹，工具不消费
    }

ark_agentic 的 chat.py 会将 context 中的每个 key 自动加 "user:" 前缀写入
``input_context``，再由 runner._merge_input_context 合并到 ``session.state``。
工具 execute 收到的 ctx 即 ``{**session.state, "session_id": ...}`` 的浅拷贝。
"""

from __future__ import annotations

from typing import Any

# session.state 中的键名（前端字段名 "rag_state" 被 ark_agentic 自动加前缀）
_KEY = "user:rag_state"


def _norm(v: Any) -> list[str] | None:
    """空值/非列表 → None；非空列表 → 标准化为 list[str]。

    前端清空选择 = 不约束（返回 None 让工具回落到 LLM args / 全局默认）。
    """
    if not v:
        return None
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x]
    return None


def read_forced_filters(
    ctx: dict[str, Any] | None,
) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
    """从工具 ctx 读取硬覆盖过滤项。

    Returns:
        (forced_kb_ids, forced_tag_ids, forced_file_ids)
        每个维度为 None 时表示前端未约束，工具应回落到 args 或全局默认值。
    """
    rs = (ctx or {}).get(_KEY) or {}
    if not isinstance(rs, dict):
        return None, None, None
    return (
        _norm(rs.get("knowledge_base_ids")),
        _norm(rs.get("tag_ids")),
        _norm(rs.get("file_ids")),
    )
