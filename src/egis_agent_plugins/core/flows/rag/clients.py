"""RAG 客户端容器 + 全局服务注册器

flow 私有的依赖注入容器，集中管理 milvus / pg / embedding / rerank 的单例生命周期。

- ``RAGClients``：强类型依赖注入容器（milvus / pg / embedding / rerank + 默认 KB）
- ``ServiceRegistry``：按 ``_config_signature(config)`` 为 RAG 相关客户端提供单例复用
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from egis_agent_plugins.core.internal.rag_config import RAGConfig, get_rag_config
from egis_agent_plugins.core.service.base import (
    MilvusClient,
    MilvusSearchResult,
    PostgresClient,
    RetrieverType,
)

if TYPE_CHECKING:
    from egis_agent_plugins.core.flows.rag.stages.recall.embedding import EmbeddingService
    from egis_agent_plugins.core.flows.rag.stages.rank.reranker import Rerank

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 依赖注入容器
# ─────────────────────────────────────────────────────

@dataclass
class RAGClients:
    """RAG 客户端集合 —— 强类型依赖注入容器

    所有 RAG 工具通过此数据类获取基础设施引用，避免松散的构造参数。
    通过 ``ServiceRegistry.build_rag_clients()`` 构建时，底层客户端自动按
    config 签名单例复用，多 agent 共用不会重复建连。
    """

    milvus: "MilvusClient"
    postgres: "PostgresClient"
    embedding: "EmbeddingService"
    rerank: "Rerank | None" = None
    knowledge_base_ids: list[str] = field(default_factory=list)
    # ── 多路召回扩展字段 ──
    summary_collection: str = "summary_knowledge_base"

    @property
    def default_kb_ids(self) -> list[str]:
        """默认知识库 ID 列表"""
        return self.knowledge_base_ids


# ─────────────────────────────────────────────────────
# 全局单例注册器
# ─────────────────────────────────────────────────────

def _config_signature(config: "RAGConfig") -> str:
    """按 RAGConfig 生成唯一签名，仅含影响连接目标的字段。"""
    return (
        f"milvus:{config.milvus_host}:{config.milvus_port}:{config.milvus_collection}"
        f"|pg:{config.db_host}:{config.db_port}:{config.db_user}:{config.db_name}"
        f"|emb:{config.embedding_provider}:{config.embedding_model}:{config.embedding_dimension}"
        f":{config.embedding_base_url}"
    )


class ServiceRegistry:
    """全局服务注册器 —— 按 config 签名单例

    1. 按 ``_config_signature()`` 去重：相同连接目标只建一组客户端
    2. 延迟初始化：首次 ``get_xxx()`` 才创建
    3. 集中回收：``close_all()`` 统一销毁
    """

    _milvus_instances: dict[str, "MilvusClient"] = {}
    _postgres_instances: dict[str, "PostgresClient"] = {}
    _embedding_instances: dict[str, "EmbeddingService"] = {}
    _rerank_instances: dict[str, "Rerank"] = {}

    @classmethod
    def get_milvus(cls, config: "RAGConfig") -> "MilvusClient":
        from egis_agent_plugins.core.service.base import MilvusClient

        sig = _config_signature(config)
        if sig not in cls._milvus_instances:
            logger.info("[ServiceRegistry] Creating new MilvusClient (sig=%s...)", sig[:60])
            cls._milvus_instances[sig] = MilvusClient(config=config)
        return cls._milvus_instances[sig]

    @classmethod
    def get_postgres(cls, config: "RAGConfig") -> "PostgresClient":
        from egis_agent_plugins.core.service.base import PostgresClient

        sig = _config_signature(config)
        if sig not in cls._postgres_instances:
            logger.info("[ServiceRegistry] Creating new PostgresClient (sig=%s...)", sig[:60])
            cls._postgres_instances[sig] = PostgresClient(config=config)
        return cls._postgres_instances[sig]

    @classmethod
    def get_embedding(cls, config: "RAGConfig") -> "EmbeddingService":
        from egis_agent_plugins.core.flows.rag.stages.recall.embedding import EmbeddingService

        sig = _config_signature(config)
        if sig not in cls._embedding_instances:
            logger.info("[ServiceRegistry] Creating new EmbeddingService (sig=%s...)", sig[:60])
            cls._embedding_instances[sig] = EmbeddingService(config=config)
        return cls._embedding_instances[sig]

    @classmethod
    def get_rerank(cls, config: "RAGConfig") -> "Rerank":
        from egis_agent_plugins.core.flows.rag.stages.rank.reranker import Rerank

        sig = _config_signature(config)
        if sig not in cls._rerank_instances:
            logger.info("[ServiceRegistry] Creating new Rerank (sig=%s...)", sig[:60])
            cls._rerank_instances[sig] = Rerank(config=config)
        return cls._rerank_instances[sig]

    @classmethod
    def build_rag_clients(
        cls,
        config: "RAGConfig",
        *,
        knowledge_base_ids: list[str] | None = None,
    ) -> RAGClients:
        """一站式构建 RAGClients，自动复用单例。"""
        milvus = cls.get_milvus(config)
        postgres = cls.get_postgres(config)
        embedding = cls.get_embedding(config)
        rerank = cls.get_rerank(config)
        kb_ids = knowledge_base_ids or config.default_knowledge_base_ids

        return RAGClients(
            milvus=milvus,
            postgres=postgres,
            embedding=embedding,
            rerank=rerank,
            knowledge_base_ids=kb_ids,
            summary_collection=config.summary_collection,
        )

    @classmethod
    async def close_all(cls) -> None:
        """关闭所有已注册的客户端连接（应在应用 shutdown 时调用）。"""
        for sig, client in cls._milvus_instances.items():
            try:
                client.close()
            except Exception as e:
                logger.warning(
                    "[ServiceRegistry] Error closing MilvusClient (sig=%s): %s", sig[:40], e
                )
        cls._milvus_instances.clear()

        for sig, client in cls._postgres_instances.items():
            try:
                await client.close()
            except Exception as e:
                logger.warning(
                    "[ServiceRegistry] Error closing PostgresClient (sig=%s): %s", sig[:40], e
                )
        cls._postgres_instances.clear()

        cls._embedding_instances.clear()
        cls._rerank_instances.clear()
        logger.info("[ServiceRegistry] All service instances closed")

    @classmethod
    def stats(cls) -> dict[str, int]:
        """返回当前已缓存的实例数量（调试用）。"""
        return {
            "milvus": len(cls._milvus_instances),
            "postgres": len(cls._postgres_instances),
            "embedding": len(cls._embedding_instances),
            "rerank": len(cls._rerank_instances),
        }
