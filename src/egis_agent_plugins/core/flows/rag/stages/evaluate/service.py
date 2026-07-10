"""LLM-backed evidence quality evaluation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)
_PROMPT_PATH = Path(__file__).parent / "prompt.yaml"


# score 采用 0-100；维度名必须与 prompt.yaml 保持一致。
DIMENSION_KEYS: tuple[str, ...] = (
    "task_coverage",
    "concept_alignment",
    "analysis_support",
    "source_reliability",
)
_DEFAULT_PASS_SCORE = 80.0


def _pass_threshold() -> float:
    raw = os.getenv("RAG_QUALITY_PASS_SCORE", str(_DEFAULT_PASS_SCORE))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PASS_SCORE
    return max(0.0, min(100.0, value))


@dataclass
class QualityEvaluation:
    passed: bool
    score: float = 0.0
    reason: str = ""
    task_type: str = ""
    dimensions: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    resolved_points: list[str] = field(default_factory=list)
    missing_points: list[str] = field(default_factory=list)
    retry_queries: list[str] = field(default_factory=list)
    requires_document_reselection: bool = False
    fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "task_type": self.task_type,
            "dimensions": self.dimensions,
            "reason": self.reason,
            "resolved_points": self.resolved_points,
            "missing_points": self.missing_points,
            "retry_queries": self.retry_queries,
            "requires_document_reselection": self.requires_document_reselection,
            "fallback": self.fallback,
        }


def _default_timeout_seconds() -> float:
    # 评估阶段要综合证据 + gap_ledger + missing_points 做 JSON 输出，15s 常常吃紧。
    # 默认放到 60s，可通过 RAG_QUALITY_TIMEOUT_SECONDS 调整。
    raw = os.getenv("RAG_QUALITY_TIMEOUT_SECONDS", "60")
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return 60.0


class EvidenceQualityService:
    def __init__(self, llm: BaseChatModel) -> None:
        self._timeout_seconds = _default_timeout_seconds()
        self._llm = llm
        import yaml

        data = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
        self._system_prompt = str((data or {}).get("system_prompt") or "").strip()
        if not self._system_prompt:
            raise ValueError(f"quality evaluation prompt missing: {_PROMPT_PATH}")

    async def evaluate(
        self,
        *,
        query: str,
        query_plan: dict[str, Any],
        retrieval_context: dict[str, Any],
        evidence: list[dict[str, Any]],
        selected_documents: list[dict[str, Any]],
        gap_ledger: dict[str, Any],
        round_number: int,
        max_rounds: int,
    ) -> QualityEvaluation:
        evidence_payload = _budget_evidence(evidence)
        payload = {
            "current_date": date.today().isoformat(),
            "round": round_number,
            "max_rounds": max_rounds,
            "query": query,
            "query_plan": query_plan,
            "retrieval_context": retrieval_context,
            "gap_ledger": gap_ledger,
            "selected_documents": [
                {
                    "knowledge_id": item.get("knowledge_id", ""),
                    "title": item.get("knowledge_title") or item.get("file_name", ""),
                }
                for item in selected_documents
            ],
            "evidence": evidence_payload,
        }
        response = await asyncio.wait_for(
            self._llm.ainvoke(
                [
                    SystemMessage(content=self._system_prompt),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
                ],
                temperature=0,
                max_tokens=800,
            ),
            timeout=self._timeout_seconds,
        )
        return self._parse(str(response.content or ""))

    @staticmethod
    def _parse(raw: str) -> QualityEvaluation:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        data = json.loads(raw.strip())

        resolved_points = _string_list(data.get("resolved_points"), limit=8)
        missing_points = _string_list(data.get("missing_points"), limit=8)
        retry_queries = _string_list(data.get("retry_queries"), limit=8)
        task_type = _normalize_task_type(data.get("task_type"))
        dimensions = _parse_dimensions(data.get("dimensions"))
        # score 优先用适用维度均值重新计算，避免 LLM 与自己的维度分相矛盾。
        computed = _compute_score(dimensions)
        if computed is not None:
            score = computed
        else:
            score = _coerce_score(data.get("score"))
        # passed 强制以阈值为准，忽略 LLM 主观输出的 passed。
        passed = score >= _pass_threshold()
        return QualityEvaluation(
            passed=passed,
            score=score,
            reason=str(data.get("reason") or "").strip(),
            task_type=task_type,
            dimensions=dimensions,
            resolved_points=resolved_points,
            missing_points=missing_points,
            retry_queries=retry_queries,
            requires_document_reselection=data.get("requires_document_reselection") is True,
        )


def _normalize_task_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"factual", "analytical", "descriptive"}:
        return text
    return ""


def _coerce_score(value: Any) -> float:
    try:
        raw = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    # 兼容 LLM 输出 0~1 旧格式：若能确认为小数，自动乘 100。
    if 0.0 < raw <= 1.0:
        raw *= 100.0
    return max(0.0, min(100.0, raw))


def _parse_dimensions(value: Any) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    raw = value if isinstance(value, dict) else {}
    for key in DIMENSION_KEYS:
        item = raw.get(key)
        if item is None:
            result[key] = None
            continue
        if not isinstance(item, dict):
            result[key] = None
            continue
        result[key] = {
            "score": _coerce_score(item.get("score")),
            "reason": str(item.get("reason") or "").strip(),
        }
    return result


def _compute_score(dimensions: dict[str, dict[str, Any] | None]) -> float | None:
    scores = [
        float(item["score"])
        for item in dimensions.values()
        if isinstance(item, dict) and item.get("score") is not None
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 2)


def _string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _budget_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per_chunk = max(1, int(os.getenv("RAG_QUALITY_CHARS_PER_CHUNK", "1200")))
    total_chars = max(1, int(os.getenv("RAG_QUALITY_MAX_TOTAL_CHARS", "18000")))
    payload: list[dict[str, Any]] = []
    used = 0
    for item in evidence:
        remaining = total_chars - used
        if remaining <= 0:
            break
        content = str(item.get("content") or "")[: min(per_chunk, remaining)]
        if not content:
            continue
        payload.append({
            "knowledge_id": item.get("knowledge_id", ""),
            "title": item.get("knowledge_title", ""),
            "content": content,
        })
        used += len(content)
    return payload
