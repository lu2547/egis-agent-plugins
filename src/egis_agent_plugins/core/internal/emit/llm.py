"""LLM 通道 — 把工具结果的精简摘要写入 ``result.llm_digest``。

设置后，runner 会用 ``llm_digest`` 替代 ``content`` 传入 LLM 上下文，
减少 token 浪费。
"""

from __future__ import annotations

from ark_agentic.core.types import AgentToolResult


def set_llm_digest(result: AgentToolResult, summary: str) -> None:
    """设置工具结果的 LLM 摘要。"""
    result.llm_digest = summary


__all__ = ["set_llm_digest"]
