"""MMR (Maximal Marginal Relevance) 多样性挑选

纯泛型实现：调用方自带候选类型、relevance_fn、content_fn，
算法内部仅做 token 化 + Jaccard 冗余度计算，
在 relevance 与多样性之间按 ``lambda_`` 权衡。

典型用法::

    from egis_agent_plugins.core.flows.rag.stages.rank.mmr import apply_mmr

    selected = apply_mmr(
        candidates,
        relevance_fn=lambda x: x.score,
        content_fn=lambda x: x.content,
        k=10,
        lambda_=0.7,
    )
"""

from __future__ import annotations

import re
from typing import Callable, TypeVar

T = TypeVar("T")

# 简单 token 化：中文 1~2 字一组 + 英文单词整体切分
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> set[str]:
    """把文本切成小写 token 集合，用于 Jaccard 相似度。"""
    return {tok.lower() for tok in _TOKEN_RE.findall(text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：交集 / 并集。两边为空返回 0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def apply_mmr(
    items: list[T],
    *,
    relevance_fn: Callable[[T], float],
    content_fn: Callable[[T], str],
    k: int,
    lambda_: float = 0.7,
) -> list[T]:
    """MMR 选 top-k：按 ``lambda * relevance - (1 - lambda) * max(jaccard_to_selected)``。

    Args:
        items:        候选列表，顺序不限
        relevance_fn: 候选 → 相关性分数（越大越好）
        content_fn:   候选 → 文本内容，用于冗余度评估
        k:            目标数量，<=0 或空候选返回 ``[]``
        lambda_:      权衡系数，1.0 纯 relevance，0.0 纯多样性

    Returns:
        按 MMR 依次选出的 candidate 列表（长度 ≤ k）
    """
    if k <= 0 or not items:
        return []

    selected: list[T] = []
    candidates = list(items)
    token_sets = [_tokenize(content_fn(c)) for c in candidates]
    selected_token_sets: list[set[str]] = []

    while len(selected) < k and candidates:
        best_idx = 0
        best_score = -1e9
        for i, cand in enumerate(candidates):
            relevance = relevance_fn(cand)
            redundancy = max(
                (_jaccard(token_sets[i], s) for s in selected_token_sets),
                default=0.0,
            )
            mmr = lambda_ * relevance - (1 - lambda_) * redundancy
            if mmr > best_score:
                best_score = mmr
                best_idx = i

        selected.append(candidates[best_idx])
        selected_token_sets.append(token_sets[best_idx])
        candidates.pop(best_idx)
        token_sets.pop(best_idx)

    return selected
