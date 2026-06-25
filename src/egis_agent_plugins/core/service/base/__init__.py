"""基础设施客户端：PostgreSQL / Milvus

这些客户端被 RAG 域复用，也可被未来其它域（如 session 持久化、业务表查询）
共用，因此归入 ``service/base``。
"""

from .milvus_client import MilvusClient, MilvusSearchResult, RetrieverType
from .postgres_client import PostgresClient

__all__ = [
    "MilvusClient",
    "MilvusSearchResult",
    "RetrieverType",
    "PostgresClient",
]
