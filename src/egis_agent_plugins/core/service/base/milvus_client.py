"""Milvus 客户端封装

提供向量检索和 BM25 关键词检索能力。
支持多 collection（按 dimension 切分）。

企业级增强：
- ``ensure_collection_loaded()``: 冷启动时自动加载 collection 到内存
- 优雅的错误恢复和重连机制
- 统一的搜索入口，支持 ``RetrieverType`` 枚举
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pymilvus import (
    AnnSearchRequest,
    MilvusClient as PyMilvusClient,
    MilvusException,
    RRFRanker,
)
try:
    from pymilvus import WeightedRanker
except ImportError:  # pragma: no cover - depends on pymilvus version
    WeightedRanker = None  # type: ignore[assignment]

from egis_agent_plugins.core.internal.rag_config import RAGConfig

logger = logging.getLogger(__name__)


class RetrieverType(str, Enum):
    """检索类型枚举"""
    VECTOR = "vector"
    KEYWORDS = "keywords"
    HYBRID = "hybrid"


@dataclass
class MilvusSearchResult:
    """Milvus 搜索结果（对齐线上 schema：id/embedding/chunk_id/
    knowledge_id/knowledge_base_id/tag_id(Array)/is_enabled/content/content_sparse）。
    """

    id: str
    content: str
    chunk_id: str
    knowledge_id: str
    knowledge_base_id: str
    score: float
    tag_id: list[str] | None = None
    is_enabled: bool = True
    file_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "chunk_id": self.chunk_id,
            "knowledge_id": self.knowledge_id,
            "knowledge_base_id": self.knowledge_base_id,
            "score": self.score,
            "tag_id": self.tag_id or [],
            "is_enabled": self.is_enabled,
            "file_name": self.file_name,
        }


class MilvusClient:
    """Milvus 客户端

    支持:
    - 向量语义检索 (``RetrieverType.VECTOR``)
    - BM25 关键词检索 (``RetrieverType.KEYWORDS``)
    - 混合检索 (``RetrieverType.HYBRID``)
    - 多 collection 支持 (按 dimension 切分)
    - 冷启动自动 load collection
    """

    # Milvus 字段名常量（与 personal/public/enterprise/summary collection schema 严格一致）
    FIELD_ID = "id"
    FIELD_CONTENT = "content"
    FIELD_CHUNK_ID = "chunk_id"
    FIELD_KNOWLEDGE_ID = "knowledge_id"
    FIELD_KNOWLEDGE_BASE_ID = "knowledge_base_id"
    FIELD_TAG_ID = "tag_id"
    FIELD_EMBEDDING = "embedding"
    FIELD_IS_ENABLED = "is_enabled"
    FIELD_CONTENT_SPARSE = "content_sparse"
    FIELD_FILE_NAME = "file_name"

    OUTPUT_FIELDS = [
        FIELD_ID,
        FIELD_CONTENT,
        FIELD_CHUNK_ID,
        FIELD_KNOWLEDGE_ID,
        FIELD_KNOWLEDGE_BASE_ID,
        FIELD_TAG_ID,
        FIELD_IS_ENABLED,
        FIELD_FILE_NAME,
    ]

    def __init__(self, config: RAGConfig) -> None:
        self._config = config
        self._client: PyMilvusClient | None = None
        self._connected = False
        self._loaded_collections: set[str] = set()
        self._connect_retries = 3
        self._connect_retry_delay = 1.0

    def connect(self) -> None:
        """连接 Milvus（带重试）"""
        if self._connected and self._client:
            return

        last_error: Exception | None = None
        for attempt in range(1, self._connect_retries + 1):
            try:
                self._client = PyMilvusClient(
                    uri=f"http://{self._config.milvus_host}:{self._config.milvus_port}",
                )
                self._connected = True
                logger.info(
                    f"[Milvus] Connected to {self._config.milvus_host}:{self._config.milvus_port}"
                )
                return
            except Exception as e:
                last_error = e
                if attempt < self._connect_retries:
                    delay = self._connect_retry_delay * attempt
                    logger.warning(
                        f"[Milvus] Connect attempt {attempt}/{self._connect_retries} failed: {e}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)

        logger.error(f"[Milvus] All connect attempts failed: {last_error}")
        raise last_error  # type: ignore[misc]

    def close(self) -> None:
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._connected = False
            self._loaded_collections.clear()
            logger.info("[Milvus] Connection closed")

    @property
    def client(self) -> PyMilvusClient:
        """获取客户端实例（延迟连接）"""
        if not self._client or not self._connected:
            self.connect()
        assert self._client is not None
        return self._client

    def get_collection_name(self, dimension: int | None = None) -> str:
        """获取 collection 名称"""
        return self._config.get_collection_name(dimension)

    def ensure_collection_loaded(self, collection_name: str | None = None) -> None:
        """确保 collection 已加载到内存

        Milvus 冷启动时 collection 可能未 load 到查询节点，
        此方法确保 collection 可用。已加载的 collection 会缓存避免重复操作。

        使用 ``PyMilvusClient`` 自身的 API（而非 ORM Collection），
        避免两套 API 的连接管理不兼容导致 ``ConnectionNotExistException``。
        """
        if collection_name is None:
            collection_name = self.get_collection_name()

        if collection_name in self._loaded_collections:
            return

        try:
            if not self.client.has_collection(collection_name):
                logger.warning(f"[Milvus] Collection '{collection_name}' does not exist")
                return

            # load_collection() 是幂等操作
            self.client.load_collection(collection_name)

            self._loaded_collections.add(collection_name)
            logger.info(f"[Milvus] Collection '{collection_name}' loaded")

        except MilvusException as e:
            error_msg = str(e)
            if "already loaded" in error_msg.lower():
                self._loaded_collections.add(collection_name)
                logger.debug(f"[Milvus] Collection '{collection_name}' already loaded")
            else:
                logger.warning(f"[Milvus] Failed to load collection '{collection_name}': {e}")
        except Exception as e:
            logger.warning(f"[Milvus] Unexpected error loading collection '{collection_name}': {e}")

    def search(
        self,
        query_embedding: list[float] | None = None,
        query_text: str | None = None,
        *,
        retriever_type: RetrieverType | str = RetrieverType.VECTOR,
        knowledge_base_ids: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        top_k: int = 10,
        dimension: int | None = None,
        collection_name: str | None = None,
        filter_expr: str | None = None,
        anns_field: str | None = None,
        output_fields: list[str] | None = None,
        vector_weight: float = 0.7,
        keywords_weight: float = 0.3,
        hybrid_ranker: str = "weighted",
        rrf_k: int = 60,
    ) -> list[MilvusSearchResult]:
        """执行搜索（统一入口）

        Args:
            collection_name: 可选，显式指定 collection（如 summary collection）。
                             留空则自动根据 dimension 确定。
            tag_ids: 可选，tag ID 过滤列表（多 tag OR 语义）。
            filter_expr: 可选，外部传入的过滤表达式，优先级高于内部构建。
            anns_field: 可选，向量字段名（默认 'embedding'）。
            output_fields: 可选，输出字段列表（默认 OUTPUT_FIELDS）。
            vector_weight: 混合检索中的向量权重，默认 0.7。
            keywords_weight: 混合检索中的 BM25 权重，默认 0.3。
            hybrid_ranker: hybrid_search 融合方式，weighted 或 rrf。
            rrf_k: 使用 RRF 时的 k 值。
        """
        if isinstance(retriever_type, str):
            retriever_type = RetrieverType(retriever_type)

        if not collection_name:
            collection_name = self.get_collection_name(dimension or self._config.embedding_dimension)

        try:
            if not self.client.has_collection(collection_name):
                logger.warning("[Milvus] Collection '%s' does not exist, skip search", collection_name)
                return []
        except Exception as e:
            logger.warning("[Milvus] Failed to check collection '%s': %s", collection_name, e)
            return []

        self.ensure_collection_loaded(collection_name)

        if not filter_expr:
            filter_expr = self._build_filter_expr(
                knowledge_base_ids=knowledge_base_ids,
                knowledge_ids=knowledge_ids,
                tag_ids=tag_ids,
            )

        if retriever_type == RetrieverType.VECTOR:
            if not query_embedding:
                raise ValueError("query_embedding is required for vector search")
            return self._vector_search(
                collection_name=collection_name,
                query_embedding=query_embedding,
                filter_expr=filter_expr,
                top_k=top_k,
                anns_field=anns_field,
                output_fields=output_fields,
            )

        elif retriever_type == RetrieverType.KEYWORDS:
            if not query_text:
                raise ValueError("query_text is required for keywords search")
            return self._keywords_search(
                collection_name=collection_name,
                query_text=query_text,
                filter_expr=filter_expr,
                top_k=top_k,
                output_fields=output_fields,
            )

        elif retriever_type == RetrieverType.HYBRID:
            if not query_embedding or not query_text:
                raise ValueError(
                    "Both query_embedding and query_text are required for hybrid search"
                )
            return self._hybrid_search_on_collection(
                collection_name=collection_name,
                query_embedding=query_embedding,
                query_text=query_text,
                filter_expr=filter_expr,
                top_k=top_k,
                vector_weight=vector_weight,
                keywords_weight=keywords_weight,
                hybrid_ranker=hybrid_ranker,
                rrf_k=rrf_k,
                output_fields=output_fields,
            )

        else:
            raise ValueError(f"Unsupported retriever_type: {retriever_type}")

    def _vector_search(
        self,
        collection_name: str,
        query_embedding: list[float],
        filter_expr: str | None,
        top_k: int,
        anns_field: str | None = None,
        output_fields: list[str] | None = None,
    ) -> list[MilvusSearchResult]:
        """向量语义检索"""
        try:
            search_params = {
                "metric_type": self._config.milvus_metric_type,
                "params": {"nprobe": 10},
            }

            res = self.client.search(
                collection_name=collection_name,
                data=[query_embedding],
                anns_field=anns_field or self.FIELD_EMBEDDING,
                limit=top_k,
                output_fields=output_fields or self.OUTPUT_FIELDS,
                search_params=search_params,
                filter=filter_expr,
            )

            return self._parse_search_results(res)
        except MilvusException as e:
            logger.error(f"[Milvus] Vector search failed: {e}")
            self._loaded_collections.discard(collection_name)
            return []

    def _keywords_search(
        self,
        collection_name: str,
        query_text: str,
        filter_expr: str | None,
        top_k: int,
        output_fields: list[str] | None = None,
    ) -> list[MilvusSearchResult]:
        """BM25 关键词检索

        Milvus 2.4+ 支持 BM25 全文检索，需要 collection 启用 BM25 function。
        参考: https://milvus.io/docs/full-text-search.md
        """
        try:
            search_params = {
                "params": {"drop_ratio_search": 0.2},
            }

            res = self.client.search(
                collection_name=collection_name,
                data=[query_text],
                anns_field=self.FIELD_CONTENT_SPARSE,
                limit=top_k,
                output_fields=output_fields or self.OUTPUT_FIELDS,
                search_params=search_params,
                filter=filter_expr,
            )

            return self._parse_search_results(res)
        except MilvusException as e:
            logger.warning(
                f"[Milvus] Keywords search failed: {e}, falling back to empty results"
            )
            self._loaded_collections.discard(collection_name)
            return []

    def _build_filter_expr(
        self,
        knowledge_base_ids: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
    ) -> str | None:
        """构建过滤表达式"""
        conditions: list[str] = []

        # 只查询启用的 chunk
        conditions.append(f"{self.FIELD_IS_ENABLED} == true")

        if knowledge_base_ids:
            kb_ids_str = ", ".join(f'"{kb_id}"' for kb_id in knowledge_base_ids)
            conditions.append(f"{self.FIELD_KNOWLEDGE_BASE_ID} in [{kb_ids_str}]")

        if knowledge_ids:
            k_ids_str = ", ".join(f'"{k_id}"' for k_id in knowledge_ids)
            conditions.append(f"{self.FIELD_KNOWLEDGE_ID} in [{k_ids_str}]")

        if tag_ids:
            # tag_id 是 Array<VarChar>（线上 schema），必须用 ARRAY_CONTAINS_ANY，不能用 `in`
            t_ids_str = ", ".join(f'"{t_id}"' for t_id in tag_ids)
            conditions.append(f"ARRAY_CONTAINS_ANY({self.FIELD_TAG_ID}, [{t_ids_str}])")

        if not conditions:
            return None

        return " and ".join(conditions)

    def _parse_search_results(
        self,
        search_res: list[list[dict[str, Any]]],
    ) -> list[MilvusSearchResult]:
        """解析搜索结果"""
        results: list[MilvusSearchResult] = []

        if not search_res or not search_res[0]:
            return results

        for hit in search_res[0]:
            entity = hit.get("entity", hit)
            tag_val = entity.get(self.FIELD_TAG_ID)
            # tag_id 是 Array，pymilvus 返回 list；防御性兼容字符串旧数据
            if isinstance(tag_val, list):
                tag_id = tag_val
            elif isinstance(tag_val, str) and tag_val:
                tag_id = [tag_val]
            else:
                tag_id = []
            results.append(
                MilvusSearchResult(
                    id=entity.get(self.FIELD_ID, ""),
                    content=entity.get(self.FIELD_CONTENT, ""),
                    chunk_id=entity.get(self.FIELD_CHUNK_ID, ""),
                    knowledge_id=entity.get(self.FIELD_KNOWLEDGE_ID, ""),
                    knowledge_base_id=entity.get(self.FIELD_KNOWLEDGE_BASE_ID, ""),
                    score=hit.get("distance", hit.get("score", 0.0)),
                    tag_id=tag_id,
                    is_enabled=entity.get(self.FIELD_IS_ENABLED, True),
                    file_name=entity.get(self.FIELD_FILE_NAME, ""),
                )
            )

        return results

    def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        knowledge_base_ids: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        top_k: int = 10,
        dimension: int | None = None,
        collection_name: str | None = None,
        vector_weight: float = 0.7,
        keywords_weight: float = 0.3,
        output_fields: list[str] | None = None,
    ) -> list[MilvusSearchResult]:
        """混合检索（向量 + BM25，加权融合）

        Args:
            collection_name: 可选，显式指定 collection（跨三级库路由场景下使用）。
                留空则根据 dimension / 默认 collection 推导。
        """
        if not collection_name:
            collection_name = self.get_collection_name(
                dimension or self._config.embedding_dimension
            )

        filter_expr = self._build_filter_expr(
            knowledge_base_ids=knowledge_base_ids,
            knowledge_ids=knowledge_ids,
            tag_ids=tag_ids,
        )

        return self._hybrid_search_on_collection(
            collection_name=collection_name,
            query_embedding=query_embedding,
            query_text=query_text,
            filter_expr=filter_expr,
            top_k=top_k,
            vector_weight=vector_weight,
            keywords_weight=keywords_weight,
            output_fields=output_fields,
        )

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
    ) -> list[MilvusSearchResult]:
        """在指定 collection 上执行混合检索（Milvus 原生 hybrid_search）。

        使用 Milvus 2.4+ 原生 ``hybrid_search``：向量 + BM25 两路
        ``AnnSearchRequest`` 一次 RPC 在服务端完成检索与加权融合，
        避免客户端两次往返再手写融合。

        默认内容检索使用 WeightedRanker，调用方也可以指定 ``hybrid_ranker="rrf"``
        使用 Milvus 原生 RRFRanker。
        """
        self.ensure_collection_loaded(collection_name)

        try:
            vector_req = AnnSearchRequest(
                data=[query_embedding],
                anns_field=self.FIELD_EMBEDDING,
                param={
                    "metric_type": self._config.milvus_metric_type,
                    "params": {"nprobe": 10},
                },
                limit=top_k * 2,
                expr=filter_expr,
            )

            keywords_req = AnnSearchRequest(
                data=[query_text],
                anns_field=self.FIELD_CONTENT_SPARSE,
                param={"params": {"drop_ratio_search": 0.2}},
                limit=top_k * 2,
                expr=filter_expr,
            )

            if (hybrid_ranker or "").lower() == "rrf":
                ranker = RRFRanker(rrf_k)
            elif WeightedRanker is not None:
                ranker = WeightedRanker(vector_weight, keywords_weight)
            else:
                ranker = RRFRanker(rrf_k)
                logger.warning(
                    "[Milvus] WeightedRanker unavailable; fallback to RRFRanker(%d)",
                    rrf_k,
                )

            res = self.client.hybrid_search(
                collection_name=collection_name,
                reqs=[vector_req, keywords_req],
                ranker=ranker,
                limit=top_k,
                output_fields=output_fields or self.OUTPUT_FIELDS,
            )

            return self._parse_search_results(res)
        except Exception as e:
            logger.error(
                f"[Milvus] Hybrid search failed (collection={collection_name}): {e}"
            )
            return []

    def search_across_collections(
        self,
        *,
        kb_meta_groups: dict[str, list[str]],
        query_embedding: list[float] | None = None,
        query_text: str | None = None,
        retriever_type: RetrieverType | str = RetrieverType.VECTOR,
        knowledge_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        filter_expr: str | None = None,
        top_k: int = 10,
        vector_weight: float = 0.7,
        keywords_weight: float = 0.3,
    ) -> list[MilvusSearchResult]:
        """跨多 collection 检索 + RRF 聚合

        三级知识库（personal/public/enterprise/summary）数据分散在不同 Milvus collection，
        本方法按 ``kb_meta_groups`` 路由到对应 collection 顺序查询，
        每路结果取 ``top_k * 2`` 候选，最后用 RRF（k=60）聚合并返回前 ``top_k``。

        并发提示：pymilvus 2.x 的 ``MilvusClient`` 是线程安全的，
        调用者可以通过 ``asyncio.to_thread`` + ``gather`` 在线程池内
        多查询并发调用同一 client。

        Args:
            kb_meta_groups: ``{collection_name: [kb_id, ...]}``，由
                ``collection_resolver.group_by_collection`` 产出。
            query_embedding: 向量检索 / 混合检索必填。
            query_text: 关键词检索 / 混合检索必填。
            retriever_type: 检索类型。
            knowledge_ids: 文件级精确过滤（跨 collection 共用）。
            tag_ids: 标签 OR 过滤（跨 collection 共用）。
            filter_expr: 完整 Milvus 过滤表达式。提供时优先使用，用于
                ``kb AND (tag OR file)`` 这类结构化前端范围。
            top_k: 最终返回条数。
            vector_weight: 混合检索中的向量权重。
            keywords_weight: 混合检索中的 BM25 权重。

        Returns:
            按 RRF 分数降序排列的 ``MilvusSearchResult`` 列表，长度 ≤ ``top_k``。
            若 ``kb_meta_groups`` 为空则退化为默认 collection 单次 ``search``。
        """
        if isinstance(retriever_type, str):
            retriever_type = RetrieverType(retriever_type)

        # 退化：未提供 collection 分组，按默认 collection 走单次搜索
        if not kb_meta_groups:
            return self.search(
                query_embedding=query_embedding,
                query_text=query_text,
                retriever_type=retriever_type,
                knowledge_ids=knowledge_ids,
                tag_ids=tag_ids,
                filter_expr=filter_expr,
                top_k=top_k,
                vector_weight=vector_weight,
                keywords_weight=keywords_weight,
            )

        per_collection_topk = max(top_k * 2, top_k)
        ranked_lists: list[list[MilvusSearchResult]] = []
        collection_items = [
            (collection_name, kb_ids)
            for collection_name, kb_ids in kb_meta_groups.items()
            if collection_name
        ]

        def _search_one(
            collection_name: str,
            kb_ids: list[str],
        ) -> list[MilvusSearchResult]:
            return self.search(
                query_embedding=query_embedding,
                query_text=query_text,
                retriever_type=retriever_type,
                knowledge_base_ids=kb_ids or None,
                knowledge_ids=knowledge_ids,
                tag_ids=tag_ids,
                filter_expr=filter_expr,
                top_k=per_collection_topk,
                collection_name=collection_name,
                vector_weight=vector_weight,
                keywords_weight=keywords_weight,
            )

        max_workers = max(
            1,
            min(
                len(collection_items),
                int(os.getenv("RAG_MILVUS_COLLECTION_WORKERS", "4")),
            ),
        )

        if max_workers <= 1:
            for collection_name, kb_ids in collection_items:
                try:
                    results = _search_one(collection_name, kb_ids)
                except Exception as e:
                    logger.warning(
                        f"[Milvus] search_across_collections: collection='{collection_name}' failed: {e}"
                    )
                    continue
                if results:
                    ranked_lists.append(results)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_search_one, collection_name, kb_ids): collection_name
                    for collection_name, kb_ids in collection_items
                }
                for future in as_completed(futures):
                    collection_name = futures[future]
                    try:
                        results = future.result()
                    except Exception as e:
                        logger.warning(
                            f"[Milvus] search_across_collections: collection='{collection_name}' failed: {e}"
                        )
                        continue
                    if results:
                        ranked_lists.append(results)

        if not ranked_lists:
            return []

        return self._rrf_merge(ranked_lists, top_k=top_k)

    def _rrf_merge(
        self,
        ranked_lists: list[list[MilvusSearchResult]],
        *,
        top_k: int,
        rrf_k: int = 60,
    ) -> list[MilvusSearchResult]:
        """对多个排名列表执行 RRF 聚合

        ``rrf_score = sum(1 / (rrf_k + rank + 1))``，按 ``chunk_id`` 去重。
        """
        score_map: dict[str, tuple[MilvusSearchResult, float]] = {}

        for ranked in ranked_lists:
            for rank, result in enumerate(ranked):
                key = result.chunk_id or result.id
                if not key:
                    continue
                rrf_score = 1.0 / (rrf_k + rank + 1)
                if key in score_map:
                    existing_result, existing_score = score_map[key]
                    score_map[key] = (existing_result, existing_score + rrf_score)
                else:
                    score_map[key] = (result, rrf_score)

        sorted_results = sorted(score_map.values(), key=lambda x: x[1], reverse=True)

        final_results: list[MilvusSearchResult] = []
        for result, score in sorted_results[:top_k]:
            result.score = score
            final_results.append(result)

        return final_results
