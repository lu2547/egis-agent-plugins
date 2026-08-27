"""基础设施客户端：PostgreSQL / Milvus

这些客户端被 RAG 域复用，也可被未来其它域（如 session 持久化、业务表查询）
共用，因此归入 ``service/base``。
"""

from .milvus_client import MilvusClient, MilvusSearchResult, RetrieverType
from .postgres_client import PostgresClient


def __getattr__(name: str):
    # llama_index 后端按需加载：未安装 llama-index 依赖的环境（如未 sync 的下游 venv）
    # 在 legacy 模式下不应因顶层导入而崩溃。
    if name == "LlamaIndexMilvusClient":
        from .llama_index_clients import LlamaIndexMilvusClient

        return LlamaIndexMilvusClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MilvusClient",
    "MilvusSearchResult",
    "RetrieverType",
    "PostgresClient",
    "LlamaIndexMilvusClient",
]
