"""Deterministic query-tokenization stage."""

from __future__ import annotations

from typing import Any

from .service import QueryRewriteService

_service = QueryRewriteService()


async def run(
    *,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,  # noqa: ARG001
    clients: Any | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "query 不能为空"}
    return (await _service.rewrite(query)).to_dict()
