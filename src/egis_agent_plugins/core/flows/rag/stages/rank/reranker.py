"""Rerank 服务 —— 严格模式，单一实现

设计取舍：
- 合并 ``BaseReranker`` / ``DashScopeReranker`` / ``RerankService`` 三类为单一 ``Rerank`` 类
- 删除 ``LLMReranker`` 及多级降级链（违背「必须用配置的 rerank 模型」的技术策略）
- 失败策略：HTTP 调用失败 → raise ``RerankError``（不静默吞错、不返原序兜底）
- 阈值过滤 + top1 safety net 保留（业务侧常见需求）
- 凭证任一缺失（``RERANK_BASE_URL`` / ``RERANK_API_KEY`` / ``RERANK_MODEL``）时返原序，非失败降级

双 provider：
- ``openai``: Bearer 鉴权（``Authorization: Bearer {api_key}``）
- ``pa_jt``:  复用 ark-agentic 的 ``PinganEAGWHeaderAsyncTransport``（RSA + HMAC 双签名）

HTTP schema（OpenAI 兼容 Rerank API，如 DashScope / 智谱 / PA）::

    POST {rerank_base_url}
    Body:   {"model": "...",
             "input": {"query": "...", "documents": [...]},
             "parameters": {"return_documents": true, "top_n": N}}
    Resp:   {"output": {"results": [{"index": 0, "relevance_score": 0.95,
                                     "document": {"text": "..."}}]}}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from egis_agent_plugins.core.internal.rag_config import RAGConfig

logger = logging.getLogger(__name__)

__all__ = ["RerankError", "RerankResult", "Rerank"]


class RerankError(Exception):
    """Rerank 调用失败。

    严格模式下任何 HTTP 失败（超时 / 非 200 / 解析失败）都会抛出此异常，
    由上层工具决定是否将工具调用标记为失败。
    """


@dataclass
class RerankResult:
    """Rerank 返回的单条结果"""

    index: int
    score: float
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "score": self.score,
            "content": self.content,
        }


class Rerank:
    """Rerank 服务（严格模式，单一实现）

    使用方式::

        rerank = Rerank(config)
        if rerank.enabled:
            results = await rerank.rerank(query, passages, top_k=10)
    """

    _SAFETY_NET_MIN_SCORE = 0.15
    _DEFAULT_TIMEOUT = 30.0

    def __init__(self, config: RAGConfig) -> None:
        self._config = config

    # ── 公开接口 ──

    @property
    def enabled(self) -> bool:
        """是否启用 rerank：仅由凭证齐全性决定。

        ``RERANK_BASE_URL`` / ``RERANK_API_KEY`` / ``RERANK_MODEL`` 任一缺失即视为未启用，
        由业务侧（如 ``knowledge_search``）在调用前判一次，避免无谓空转。
        """
        cfg = self._config
        return bool(
            cfg.rerank_base_url
            and cfg.rerank_model
            and cfg.rerank_api_key
        )

    async def rerank(
        self,
        query: str,
        passages: list[str],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        """对 ``passages`` 执行 rerank（严格模式）。

        - 未启用 → 返原序（score=1.0，让上层下游逻辑无感切换）
        - 启用但空输入 → 返 ``[]``
        - 启用且调用成功 → 阈值过滤 + top1 safety net 后返回
        - 启用但调用失败 → raise ``RerankError``
        """
        if not self.enabled:
            logger.debug("[Rerank] Disabled, returning original order")
            return [
                RerankResult(index=i, score=1.0, content=p)
                for i, p in enumerate(passages)
            ]

        if not passages:
            return []

        # 过滤空文档，保留原始 index 映射
        clean: list[tuple[int, str]] = [
            (i, p) for i, p in enumerate(passages) if p and p.strip()
        ]
        if not clean:
            return []

        original_indices = [idx for idx, _ in clean]
        doc_texts = [p for _, p in clean]

        raw_results = await self._call_http(
            query=query,
            documents=doc_texts,
            top_n=top_k or len(doc_texts),
        )

        # 映射回原始 index
        results: list[RerankResult] = []
        for r in raw_results:
            idx_in_clean = int(r.get("index", -1))
            if idx_in_clean < 0 or idx_in_clean >= len(original_indices):
                continue
            mapped_index = original_indices[idx_in_clean]
            doc_text = (r.get("document") or {}).get("text") or passages[mapped_index]
            results.append(
                RerankResult(
                    index=mapped_index,
                    score=float(r.get("relevance_score", 0.0)),
                    content=doc_text,
                )
            )

        logger.info(
            f"[Rerank] provider={self._config.rerank_provider} "
            f"model={self._config.rerank_model} "
            f"input={len(doc_texts)} → output={len(results)}"
        )

        filtered = self._apply_threshold(results)

        # safety net: 阈值过滤全部淘汰时，保留 score 足够高的 top1
        if not filtered and results and results[0].score >= self._SAFETY_NET_MIN_SCORE:
            logger.info(
                f"[Rerank] All below threshold ({self._config.rerank_threshold}), "
                f"keeping top1 (score={results[0].score:.4f})"
            )
            filtered = [results[0]]

        return filtered

    # ── 内部实现 ──

    def _build_http_client(self) -> httpx.AsyncClient:
        """按 provider 构造 httpx 异步客户端。"""
        provider = (self._config.rerank_provider or "openai").lower()

        if provider == "pa_jt":
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
            return httpx.AsyncClient(transport=transport, timeout=self._DEFAULT_TIMEOUT)

        return httpx.AsyncClient(timeout=self._DEFAULT_TIMEOUT)

    def _build_headers(self) -> dict[str, str]:
        """按 provider 构造请求头（PA-JT 鉴权由 Transport 注入，此处不加 Auth）。"""
        provider = (self._config.rerank_provider or "openai").lower()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if provider != "pa_jt":
            headers["Authorization"] = f"Bearer {self._config.rerank_api_key}"
        return headers

    async def _call_http(
        self,
        *,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[dict[str, Any]]:
        """HTTP 调用 rerank 接口，失败 → raise ``RerankError``。"""
        body = {
            "model": self._config.rerank_model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": True, "top_n": top_n},
        }
        headers = self._build_headers()

        try:
            async with self._build_http_client() as client:
                resp = await client.post(
                    self._config.rerank_base_url,
                    json=body,
                    headers=headers,
                )
        except httpx.TimeoutException as e:
            raise RerankError(f"Rerank API timeout: {e}") from e
        except httpx.HTTPError as e:
            raise RerankError(f"Rerank API network error: {e}") from e

        if resp.status_code != 200:
            snippet = resp.text[:500] if resp.text else ""
            raise RerankError(
                f"Rerank API HTTP {resp.status_code}: {snippet}"
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise RerankError(f"Rerank API returned non-JSON: {e}") from e

        output = data.get("output") if isinstance(data, dict) else None
        if not isinstance(output, dict):
            raise RerankError(f"Rerank API response missing 'output': {data}")

        results = output.get("results")
        if not isinstance(results, list):
            raise RerankError(f"Rerank API response 'output.results' not a list: {output}")

        return results

    def _apply_threshold(self, results: list[RerankResult]) -> list[RerankResult]:
        """阈值过滤：只保留 ``score >= rerank_threshold``。"""
        threshold = self._config.rerank_threshold
        filtered = [r for r in results if r.score >= threshold]
        if len(filtered) < len(results):
            logger.debug(
                f"[Rerank] Threshold filter: {len(results)} → {len(filtered)} "
                f"(threshold={threshold})"
            )
        return filtered
