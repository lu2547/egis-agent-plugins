"""Evidence quality evaluation stage entrypoint."""

from __future__ import annotations

import logging
from typing import Any

from .service import EvidenceQualityService, QualityEvaluation

logger = logging.getLogger(__name__)
_service: EvidenceQualityService | None = None


def _get_service() -> EvidenceQualityService:
    global _service
    if _service is None:
        from ark_agentic.core.llm import create_chat_model_from_env

        _service = EvidenceQualityService(create_chat_model_from_env())
    return _service


async def run(*, args: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: ARG001
    evidence = list(args.get("evidence") or [])
    if not evidence:
        return QualityEvaluation(
            passed=False,
            reason="没有可用于回答的证据",
            missing_points=["缺少能够回答用户问题的直接证据"],
            retry_queries=[str(args.get("query") or "").strip()],
        ).to_dict()

    query = str(args.get("query") or "").strip()
    try:
        result = await _get_service().evaluate(
            query=query,
            query_plan=args.get("query_plan") or {},
            retrieval_context=args.get("retrieval_context") or {},
            evidence=evidence,
            selected_documents=list(args.get("selected_documents") or []),
            gap_ledger=args.get("gap_ledger") or {},
            round_number=int(args.get("round_number") or 1),
            max_rounds=int(args.get("max_rounds") or 5),
        )
        return result.to_dict()
    except Exception as exc:
        # 评估服务失败时不能默认放行 —— 曾用 passed=True 兜底会让不相关证据流入答案 LLM 引发幻觉。
        # 改为标记未通过 + 用原 query 作为下轮补搜种子，把决定权交回 retry/max_rounds。
        logger.warning("[EvidenceQuality] evaluation failed, marking insufficient: %s", exc)
        return QualityEvaluation(
            passed=False,
            score=0.0,
            reason=f"质量评估服务不可用: {exc}",
            retry_queries=[query] if query else [],
            fallback=True,
        ).to_dict()
