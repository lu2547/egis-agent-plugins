"""Embedding 服务封装

支持两种 provider：
- ``openai``: OpenAI 兼容接口（默认 Bearer 鉴权）
- ``pa_jt``:  平安 PA 网关，复用 ark-agentic 的 ``PinganEAGWHeaderAsyncTransport``
              （RSA-SHA256 + HMAC-SHA1 双签名，仅注入 Header、不改 body）

两种 provider 都走 ``AsyncOpenAI``，唯一区别是构造时是否注入自定义 ``http_client``。
"""

from __future__ import annotations

import logging

import httpx
from openai import AsyncOpenAI

from egis_agent_plugins.core.internal.rag_config import RAGConfig

logger = logging.getLogger(__name__)

__all__ = ["EmbeddingService"]


class EmbeddingService:
    """Embedding 服务（OpenAI / PA-JT 双 provider）"""

    def __init__(self, config: RAGConfig) -> None:
        self._config = config
        self._client: AsyncOpenAI | None = None

    def _build_pa_jt_http_client(self) -> httpx.AsyncClient:
        """构造带 PA-JT 鉴权 Transport 的 httpx 异步客户端。"""
        from ark_agentic.core.llm.pa_jt_llm import PinganEAGWHeaderAsyncTransport

        cfg = self._config
        transport = PinganEAGWHeaderAsyncTransport(
            base_transport=httpx.AsyncHTTPTransport(retries=3),
            api_code=cfg.pa_jt_open_api_code,
            gateway_credential=cfg.pa_jt_open_api_credential,
            gateway_key=cfg.pa_jt_rsa_private_key,
            app_key=cfg.pa_jt_gpt_app_key,
            app_secret=cfg.pa_jt_gpt_app_secret,
            scene_id=cfg.pa_jt_scene_id,
        )
        return httpx.AsyncClient(transport=transport)

    def _get_client(self) -> AsyncOpenAI:
        """延迟构造 AsyncOpenAI，按 provider 决定 Transport。"""
        if self._client is not None:
            return self._client

        cfg = self._config
        provider = (cfg.embedding_provider or "openai").lower()

        if provider == "pa_jt":
            http_client = self._build_pa_jt_http_client()
            self._client = AsyncOpenAI(
                api_key="EMPTY",  # PA-JT 鉴权走 Header，不用 Bearer
                base_url=cfg.embedding_base_url or None,
                http_client=http_client,
            )
            logger.debug(
                "[Embedding] Using PA-JT provider (model=%s, base_url=%s)",
                cfg.embedding_model,
                cfg.embedding_base_url,
            )
        else:
            self._client = AsyncOpenAI(
                api_key=cfg.embedding_api_key,
                base_url=cfg.embedding_base_url or None,
            )
            logger.debug(
                "[Embedding] Using OpenAI provider (model=%s, base_url=%s)",
                cfg.embedding_model,
                cfg.embedding_base_url,
            )

        return self._client

    async def embed_query(self, query: str) -> list[float]:
        """生成单条查询文本的 embedding。"""
        if not query or not query.strip():
            raise ValueError("query cannot be empty")

        client = self._get_client()

        try:
            response = await client.embeddings.create(
                model=self._config.embedding_model,
                input=query,
                dimensions=self._config.embedding_dimension,
            )
            embedding = response.data[0].embedding
            logger.debug(
                f"[Embedding] Generated embedding (len={len(query)}, dim={len(embedding)})"
            )
            return embedding
        except Exception as e:
            logger.error(f"[Embedding] Failed to generate embedding: {e}")
            raise

    async def embed_queries(self, queries: list[str]) -> list[list[float]]:
        """批量生成 embedding。"""
        if not queries:
            return []

        client = self._get_client()

        try:
            response = await client.embeddings.create(
                model=self._config.embedding_model,
                input=queries,
                dimensions=self._config.embedding_dimension,
            )
            # 按 index 排序确保顺序
            embeddings = [d.embedding for d in sorted(response.data, key=lambda x: x.index)]
            logger.debug(
                f"[Embedding] Generated {len(embeddings)} embeddings "
                f"(dim={len(embeddings[0]) if embeddings else 0})"
            )
            return embeddings
        except Exception as e:
            logger.error(f"[Embedding] Failed to generate batch embeddings: {e}")
            raise

    @property
    def dimension(self) -> int:
        return self._config.embedding_dimension

    @property
    def model_name(self) -> str:
        return self._config.embedding_model
