"""RAG 排序算法集合

提供纯内存、无 I/O 的可复用算法：MMR 多样性挑选等。
所有函数以泛型签名对外，tool 传入自己的 relevance_fn / content_fn 即可适配。
"""

from .mmr import apply_mmr
from .stage import run

__all__ = ["apply_mmr", "run"]
