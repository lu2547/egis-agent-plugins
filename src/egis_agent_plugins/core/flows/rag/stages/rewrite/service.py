"""Query Rewrite 服务

对用户问题进行意图识别和改写：
- 代词消解、省略补全、术语统一
- 子问题拆分（最多 N 个 sub_queries）
- 意图分类（rag / web_search / direct）

作为 query_rewrite 工具的后端服务，由 LLM 在 rag skill 引导下调用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────

IntentType = Literal["rag", "web_search", "direct"]

_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompt.yaml"

_DEFAULT_INTENT: IntentType = "rag"

# 代词 / 指代词含有则需要历史进行消解，否则跳过历史节约 token
_REFERENCE_PATTERN = re.compile(
    r"(它|他|她|它们|他们|她们|这|那|这个|那个|这些|那些|这种|那种|上面|下面|前面|后面|之前|之后|刚才|上述|上一|上轮"
    r"|\b(?:it|its|this|that|these|those|the\s+above|the\s+previous)\b)",
    re.IGNORECASE,
)

# 历史限制：只取最近 _MAX_HISTORY_MESSAGES 条，单条截断到 _MAX_HISTORY_CHARS 字
_MAX_HISTORY_MESSAGES = 2
_MAX_HISTORY_CHARS = 100


# ── 输出数据结构 ──────────────────────────────────────────────────────

@dataclass
class RewriteResult:
    """Query Rewrite 输出"""
    rewrite_query: str
    sub_queries: list[str] = field(default_factory=list)
    intent: IntentType = "rag"
    keywords: list[str] = field(default_factory=list)
    doc_query: str = ""
    analysis_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rewrite_query": self.rewrite_query,
            "sub_queries": self.sub_queries,
            "intent": self.intent,
            "keywords": self.keywords,
            "doc_query": self.doc_query,
            "analysis_query": self.analysis_query,
        }


# ── 服务实现 ──────────────────────────────────────────────────────────

class QueryRewriteService:
    """Query Rewrite 服务

    输入: 用户 query + 对话历史 + 语言
    输出: RewriteResult (改写后 query、子问题列表、意图、关键词)

    失败时使用原 query + intent=rag
    """

    # LLM 调用内部超时（秒），需小于 ToolExecutor 的 30s，保证超时在 rewrite 层转换为原始 query。
    _LLM_TIMEOUT: float = 15.0

    def __init__(
        self,
        llm: BaseChatModel,
        *,
        max_sub_queries: int = 4,
        temperature: float = 0.3,
    ) -> None:
        self._llm = llm
        self._max_sub_queries = max_sub_queries
        self._temperature = temperature
        self._system_prompt = self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        """加载 system prompt 模板。模板丢失是明显部署错误，直接报错。"""
        if not _PROMPT_TEMPLATE_PATH.is_file():
            raise FileNotFoundError(
                f"Query rewrite prompt template missing: {_PROMPT_TEMPLATE_PATH}"
            )
        import yaml
        data = yaml.safe_load(_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8"))
        prompt = data.get("system_prompt", "") if isinstance(data, dict) else ""
        if not prompt:
            raise ValueError(
                f"Query rewrite prompt template empty or missing 'system_prompt' key: {_PROMPT_TEMPLATE_PATH}"
            )
        return prompt

    @staticmethod
    def _needs_history(query: str) -> bool:
        """判断 query 是否含代词/指代词，有才需要带历史去消解。"""
        return bool(_REFERENCE_PATTERN.search(query or ""))

    async def rewrite(
        self,
        query: str,
        *,
        history: list[dict[str, str]] | None = None,
        language: str = "zh-CN",
        pinned_knowledge_ids: list[str] | None = None,
    ) -> RewriteResult:
        """执行 Query Rewrite

        Args:
            query: 原始用户输入
            history: 对话历史 [{"role": "user"|"assistant", "content": "..."}]
            language: 用户语言
            pinned_knowledge_ids: 若非空，强制 intent=rag 并跳过 LLM 改写

        Returns:
            RewriteResult
        """
        # 快捷路径：用户已指定文档 → 直接返回 rag
        if pinned_knowledge_ids:
            return RewriteResult(
                rewrite_query=query,
                sub_queries=[query],
                intent="rag",
                keywords=self._extract_simple_keywords(query),
                doc_query=query,
                analysis_query=query,
            )

        try:
            result = await self._call_llm(query, history=history, language=language)
            return result
        except Exception as e:
            logger.warning("[QueryRewrite] LLM 调用失败, 使用原始 query: %s", e)
            return RewriteResult(
                rewrite_query=query,
                sub_queries=[query],
                intent=_DEFAULT_INTENT,
                keywords=self._extract_simple_keywords(query),
            )

    async def _call_llm(
        self,
        query: str,
        *,
        history: list[dict[str, str]] | None = None,
        language: str = "zh-CN",
    ) -> RewriteResult:
        """调用 LLM 进行改写。

        历史只在 query 含代词时才拼入，且取最近 _MAX_HISTORY_MESSAGES 条、
        单条截断到 _MAX_HISTORY_CHARS 字，避免不必要的 token 消耗。
        """
        # 构建 conversation context——仅在需要消解代词时才带上历史
        conv_lines: list[str] = []
        if history and self._needs_history(query):
            for msg in history[-_MAX_HISTORY_MESSAGES:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content:
                    conv_lines.append(f"{role}: {content[:_MAX_HISTORY_CHARS]}")

        user_content = f"""## 对话历史
{chr(10).join(conv_lines) if conv_lines else "（无历史）"}

## 用户问题
{query}

## 当前日期
{date.today().isoformat()}

## 语言
{language}

## JSON 输出"""

        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=user_content),
        ]

        # 调用 LLM（低温度）—— 加 asyncio 超时，防止 LLM 慢响应导致整个工具 30s 硬超时
        try:
            response = await asyncio.wait_for(
                self._llm.ainvoke(
                    messages,
                    temperature=self._temperature,
                    max_tokens=500,
                ),
                timeout=self._LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM response exceeded {self._LLM_TIMEOUT}s")

        raw = response.content.strip() if response.content else ""
        return self._parse_response(raw, query)

    def _parse_response(self, raw: str, original_query: str) -> RewriteResult:
        """解析 LLM 返回的 JSON"""
        # 去除可能的 markdown code fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[QueryRewrite] JSON 解析失败: %s", raw[:200])
            return RewriteResult(
                rewrite_query=original_query,
                sub_queries=[original_query],
                intent=_DEFAULT_INTENT,
                keywords=self._extract_simple_keywords(original_query),
            )

        rewrite_query = data.get("rewrite_query", original_query) or original_query
        doc_query = data.get("doc_query", "") or ""
        analysis_query = data.get("analysis_query", "") or ""
        sub_queries = data.get("sub_queries", [rewrite_query])
        if not sub_queries:
            sub_queries = [rewrite_query]
        # 限制子问题数量
        sub_queries = sub_queries[: self._max_sub_queries]

        intent = data.get("intent", _DEFAULT_INTENT)
        if intent not in ("rag", "web_search", "direct"):
            intent = _DEFAULT_INTENT

        keywords = data.get("keywords", [])
        if not isinstance(keywords, list):
            keywords = []

        if intent == "rag":
            if not doc_query:
                doc_query = rewrite_query
            if not analysis_query:
                analysis_query = original_query
        else:
            doc_query = ""
            analysis_query = ""

        return RewriteResult(
            rewrite_query=rewrite_query,
            sub_queries=sub_queries,
            intent=intent,
            keywords=keywords[:5],
            doc_query=doc_query,
            analysis_query=analysis_query,
        )

    @staticmethod
    def _extract_simple_keywords(query: str) -> list[str]:
        """简单关键词提取（不依赖 LLM）"""
        import re
        # 去除标点，按空格/标点分词，取长度>=2的片段
        tokens = re.split(r'[\s,，。！？、；：""''（）()\[\]{}]+', query)
        return [t for t in tokens if len(t) >= 2][:5]
