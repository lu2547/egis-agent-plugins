"""RAG 范围硬覆盖 helpers。

约定（前端 → ark_agentic chat.py → session.state → tool ctx）：
    ChatRequest.context.rag_state = {
        "rag_filter": [
            {
                "kb_id": "...",
                "kb_name": "...",
                "tags": [
                    {"tag_id": "...", "tag_name": "...", "files": [{"id": "..."}]}
                ],
                "files": [{"id": "..."}],
            }
        ]
    }

ark_agentic 的 chat.py 会将 context 中的每个 key 自动加 "user:" 前缀写入
``input_context``，再由 runner._merge_input_context 合并到 ``session.state``。
工具 execute 收到的 ctx 即 ``{**session.state, "session_id": ...}`` 的浅拷贝。
"""

from __future__ import annotations


def read_forced_filters(
    ctx: dict[str, object] | None,
) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
    """Legacy flat overrides are no longer read from ``rag_state``.

    ``rag_state`` now carries only hierarchical ``rag_filter``. Flattening it
    here would break per-KB scope semantics, so stages must read the scope plan
    from ``filters.rag_filter`` / ``ctx.user:rag_state.rag_filter`` instead.
    """
    return None, None, None
