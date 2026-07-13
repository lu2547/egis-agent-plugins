from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest

from egis_agent_plugins.core.flows.rag.stages.rank.reranker import Rerank, RerankError
from egis_agent_plugins.core.internal.rag_config import RAGConfig, get_rag_config


def _config(**overrides: object) -> RAGConfig:
    values = {
        "rerank_model": "test-rerank",
        "rerank_api_key": "test-key",
        "rerank_base_url": "https://rerank.test/v1/rerank",
    }
    values.update(overrides)
    return RAGConfig(**values)


def test_rerank_timeout_env_is_the_http_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_RERANK_TIMEOUT_S", "12.5")
    rerank = Rerank(get_rag_config())

    assert rerank.timeout_seconds == 12.5
    client = rerank._build_http_client()
    try:
        assert client.timeout.connect == 12.5
        assert client.timeout.read == 12.5
        assert client.timeout.write == 12.5
        assert client.timeout.pool == 12.5
    finally:
        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_http_error_body_is_logged_and_preserved(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = '{"code":"BadRequest","message":"invalid rerank input"}'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=response_body, request=request)

    rerank = Rerank(_config(rerank_timeout_s=9.0))
    monkeypatch.setattr(
        rerank,
        "_build_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=rerank.timeout_seconds),
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RerankError) as exc_info:
        await rerank.rerank("query", ["passage"])

    assert response_body in str(exc_info.value)
    assert response_body in caplog.text


@pytest.mark.asyncio
async def test_rank_timeout_has_non_empty_error_body() -> None:
    from egis_agent_plugins.core.flows.rag.stages.rank.stage import _rerank

    class SlowRerank:
        enabled = True
        timeout_seconds = 0.01

        async def rerank(self, query: str, passages: list[str]) -> list[object]:
            await asyncio.sleep(1)
            return []

    clients = SimpleNamespace(rerank=SlowRerank())
    with pytest.raises(RerankError, match=r"Rerank timeout after 0.01s"):
        await _rerank(clients, "query", [{"content": "passage"}])
