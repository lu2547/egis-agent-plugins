"""llama_index 后端的 Milvus 检索客户端。

继承 legacy ``MilvusClient``，完整复用其上层编排逻辑
（collection 路由、filter 构建、跨 collection RRF 聚合），
仅把三个检索原语替换为 llama_index ``MilvusVectorStore.query``：

- ``_vector_search``            → VectorStoreQueryMode.DEFAULT
- ``_keywords_search``          → VectorStoreQueryMode.TEXT_SEARCH（Milvus BM25 built-in）
- ``_hybrid_search_on_collection`` → VectorStoreQueryMode.HYBRID（WeightedRanker / RRFRanker）

原始 Milvus filter 表达式通过 ``string_expr`` 透传，行为与 legacy 对齐；
由 ``RAG_BACKEND=llama_index`` 开关启用（见 ``ServiceRegistry.get_milvus``）。
"""

from __future__ import annotations

import logging
from typing import Any

from llama_index.core.vector_stores.types import (
    VectorStoreQuery,
    VectorStoreQueryMode,
)
from llama_index.vector_stores.milvus import MilvusVectorStore

from egis_agent_plugins.core.internal.rag_config import RAGConfig
from egis_agent_plugins.core.service.base.milvus_client import (
    MilvusClient,
    MilvusSearchResult,
)

logger = logging.getLogger(__name__)


class LlamaIndexMilvusClient(MilvusClient):
    """以 llama_index MilvusVectorStore 为执行层的 Milvus 客户端。"""

    def __init__(self, config: RAGConfig) -> None:
        super().__init__(config)
        # (collection, ranker, vw, kw) → MilvusVectorStore；ranker 是构造期参数，按组合缓存
        self._stores: dict[tuple[str, str, float, float], MilvusVectorStore] = {}

    # ── store 构建 ──

    def _get_store(
        self,
        collection_name: str,
        *,
        hybrid_ranker: str = "weighted",
        vector_weight: float = 0.7,
        keywords_weight: float = 0.3,
        rrf_k: int = 60,
    ) -> MilvusVectorStore:
        key = (collection_name, hybrid_ranker, vector_weight, keywords_weight)
        if key in self._stores:
            return self._stores[key]

        if (hybrid_ranker or "").lower() == "rrf":
            ranker_name, ranker_params = "RRFRanker", {"k": rrf_k}
        else:
            ranker_name = "WeightedRanker"
            ranker_params = {"weights": [vector_weight, keywords_weight]}

        store = MilvusVectorStore(
            uri=f"http://{self._config.milvus_host}:{self._config.milvus_port}",
            collection_name=collection_name,
            dim=self._config.embedding_dimension,
            embedding_field=self.FIELD_EMBEDDING,
            sparse_embedding_field=self.FIELD_CONTENT_SPARSE,
            text_key=self.FIELD_CONTENT,
            output_fields=self.OUTPUT_FIELDS,
            enable_sparse=True,  # WeKnora collection 带 BM25 built-in function，自动检测
            hybrid_ranker=ranker_name,
            hybrid_ranker_params=ranker_params,
            similarity_metric=self._config.milvus_metric_type,
            search_config={"nprobe": 10},
            overwrite=False,
        )
        self._stores[key] = store
        logger.info(
            "[LlamaIndexMilvus] store created: collection=%s ranker=%s",
            collection_name,
            ranker_name,
        )
        return store

    # ── 结果映射 ──

    def _parse_query_result(self, result: Any) -> list[MilvusSearchResult]:
        """VectorStoreQueryResult → MilvusSearchResult（字段对齐 _parse_search_results）。"""
        nodes = result.nodes or []
        similarities = result.similarities or []
        ids = result.ids or []
        out: list[MilvusSearchResult] = []
        for i, node in enumerate(nodes):
            meta = node.metadata or {}
            tag_val = meta.get(self.FIELD_TAG_ID)
            if isinstance(tag_val, list):
                tag_id = tag_val
            elif isinstance(tag_val, str) and tag_val:
                tag_id = [tag_val]
            else:
                tag_id = []
            out.append(
                MilvusSearchResult(
                    id=str(ids[i]) if i < len(ids) else "",
                    content=node.text or "",
                    chunk_id=str(meta.get(self.FIELD_CHUNK_ID, "")),
                    knowledge_id=str(meta.get(self.FIELD_KNOWLEDGE_ID, "")),
                    knowledge_base_id=str(meta.get(self.FIELD_KNOWLEDGE_BASE_ID, "")),
                    score=float(similarities[i]) if i < len(similarities) else 0.0,
                    tag_id=tag_id,
                    is_enabled=bool(meta.get(self.FIELD_IS_ENABLED, True)),
                    file_name=str(meta.get(self.FIELD_FILE_NAME, "")),
                )
            )
        return out

    # ── 检索原语（替换 legacy 的 pymilvus 直连实现）──

    def _vector_search(
        self,
        collection_name: str,
        query_embedding: list[float],
        filter_expr: str | None,
        top_k: int,
        anns_field: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[MilvusSearchResult]:
        """向量语义检索（llama_index DEFAULT 模式）。"""
        try:
            store = self._get_store(collection_name)
            result = store.query(
                VectorStoreQuery(
                    query_embedding=query_embedding,
                    similarity_top_k=top_k,
                    mode=VectorStoreQueryMode.DEFAULT,
                ),
                string_expr=filter_expr or "",
            )
            return self._parse_query_result(result)
        except Exception as e:
            logger.error("[LlamaIndexMilvus] Vector search failed: %s", e)
            return []

    def _keywords_search(
        self,
        collection_name: str,
        query_text: str,
        filter_expr: str | None,
        top_k: int,
        output_fields: list[str] | None = None,
    ) -> list[MilvusSearchResult]:
        """BM25 关键词检索（llama_index TEXT_SEARCH 模式，Milvus built-in BM25）。"""
        try:
            store = self._get_store(collection_name)
            result = store.query(
                VectorStoreQuery(
                    query_str=query_text,
                    similarity_top_k=top_k,
                    mode=VectorStoreQueryMode.TEXT_SEARCH,
                ),
                string_expr=filter_expr or "",
            )
            return self._parse_query_result(result)
        except Exception as e:
            logger.warning(
                "[LlamaIndexMilvus] Keywords search failed: %s, falling back to empty results",
                e,
            )
            return []

    def _hybrid_search_on_collection(
        self,
        *,
        collection_name: str,
        query_embedding: list[float],
        query_text: str,
        filter_expr: str | None,
        top_k: int,
        vector_weight: float = 0.7,
        keywords_weight: float = 0.3,
        hybrid_ranker: str = "weighted",
        rrf_k: int = 60,
        output_fields: list[str] | None = None,
        dense_anns_field: str | None = None,
        sparse_anns_field: str | None = None,
    ) -> list[MilvusSearchResult]:
        """混合检索（llama_index HYBRID 模式，服务端 WeightedRanker/RRFRanker 融合）。"""
        self.ensure_collection_loaded(collection_name)
        try:
            store = self._get_store(
                collection_name,
                hybrid_ranker=hybrid_ranker,
                vector_weight=vector_weight,
                keywords_weight=keywords_weight,
                rrf_k=rrf_k,
            )
            result = store.query(
                VectorStoreQuery(
                    query_embedding=query_embedding,
                    query_str=query_text,
                    similarity_top_k=top_k,
                    mode=VectorStoreQueryMode.HYBRID,
                ),
                string_expr=filter_expr or "",
            )
            return self._parse_query_result(result)
        except Exception as e:
            logger.error(
                "[LlamaIndexMilvus] Hybrid search failed (collection=%s): %s",
                collection_name,
                e,
            )
            return []

    def close(self) -> None:
        """关闭：清 store 缓存（底层 pymilvus client 由父类统一管理）。"""
        self._stores.clear()
        super().close()
