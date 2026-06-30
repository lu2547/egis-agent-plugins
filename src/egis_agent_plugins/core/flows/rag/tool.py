"""RagTool — Auto-Drive Wrapper

对 LLM 暴露单一 ``rag`` 工具，内部 auto-drive 完成全流程。
LLM 不感知阶段边界，一次调用拿到 evidence pack。

继承 ``AgentTool``（非 ``WorkflowTool``），因为 ``WorkflowTool`` 会
把每个 transition action 暴露给 LLM，而我们希望 LLM 只看到一次 ``run``。

1、	start阶段: 闲聊跳出
2、rewrite:	查询改写（代词消解、子问题拆分）
3、route: web / internal / web_unavailable
recall 阶段:
  → _milvus_search_with_embedding(top_k=10)
    → search_across_collections(top_k=10*2=20)
      → 每个 collection: hybrid_search(top_k=20)
        → 向量请求 limit=20*2=40
        → BM25 请求 limit=20*2=40
        → RRF 融合返回 limit=20
      → 跨集合 RRF 返回 top_k=20
rank 阶段:
  → 取前 30 条送 rerank → MMR 返回 10 条
evidence 阶段:
  → 取前 10 条做深度阅读（关联读）chunk < 350 字符、上限1000字符
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from ark_agentic.core.tools.base import AgentTool, ToolParameter
from ark_agentic.core.types import AgentToolResult, ToolDigest

from egis_agent_plugins.core.internal.a2ui import (
    FrontendDigest,
    MinimalView,
    DetailedView,
    ViewSection,
    ToolDisplayType,
    apply_dual_layer,
)
from egis_agent_plugins.core.flows.rag.clients import RAGClients

from ._services.scope_adapter import read_rag_state
from .workflow import RagRetrievalWorkflow

logger = logging.getLogger(__name__)


class RagTool(AgentTool):
    """RAG 检索一体化工具 — 一次调用完成全流程。

    LLM 传入 ``query`` + 可选参数，工具内部 auto-drive 状态机：
    rewrite → route → recall → rank → read → decide → (retry)。

    返回 evidence pack（结构化文本）+ references（写入 session state）。
    """

    name = "rag"
    description = """一体化 RAG 检索工具 — 一次调用完成查询改写、多路召回、重排、深度阅读全流程。

功能：
- 自动识别意图（知识库/网络搜索/文档定向/闲聊）
- 查询改写（代词消解、子问题拆分）
- 多路并行召回（向量 + 关键词 RRF 融合）
- Rerank + MMR 去冗余
- 深度阅读 + 充分性评估
- 证据不足时自动扩展重试

输出：结构化证据包（编号 [1]、[2]...，含引用来源和内容摘要），可直接用于回答用户问题。
回答时用 [N] 标注引用出处，系统会自动将编号映射为真实文档引用。"""

    parameters = [
        ToolParameter(
            name="query",
            type="string",
            description="用户的原始问题",
            required=True,
        ),
        ToolParameter(
            name="source",
            type="string",
            description="检索来源：auto（自动判断）、internal（仅知识库）、web（仅网络）",
            required=False,
            default="auto",
            enum=["auto", "internal", "web"],
        ),
        ToolParameter(
            name="filters",
            type="object",
            description=(
                "资料范围过滤条件。前端指定范围时由系统直接注入 filters.rag_filter，"
                "结构为 [{kb_id,kb_name,tags:[{tag_id,tag_name,files:[{id,name,type}]}],files:[...]}]。"
                "不要把层级范围简化成 kb_id/tag_ids/file_ids；同一知识库内 tag 与 file 是 OR 关系，"
                "不同知识库分开查询。"
            ),
            required=False,
        ),
        ToolParameter(
            name="max_retries",
            type="integer",
            description="证据不足时的最大重试次数（默认 1）",
            required=False,
            default=1,
        ),
    ]

    # data_source: RAG 工具产出 evidence，计入 citation 统计
    data_source = True

    # 声明写入 session.state 的 keys
    output_state_keys = ("_rag_evidence_pack", "_rag_references")

    def __init__(self, *, clients: RAGClients) -> None:
        self._clients = clients

    # ── Parameter parsing helpers (LLM often sends strings instead of typed values) ──

    @staticmethod
    def _parse_filters(raw: Any) -> dict[str, Any]:
        """Parse ``filters`` which LLM may send as a JSON string instead of a dict."""
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            raw = raw.strip()
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            logger.warning("[RagRetrieval] filters 参数无法解析为对象: %s", raw[:200])
            return {}
        return {}

    @staticmethod
    def _read_frontend_rag_filter(ctx: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Read frontend scope from tool context.

        The frontend scope is request/runtime state, not something the LLM
        should be trusted to synthesize. The after-model callback injects it
        before tool tracing; this method is the execution-time fallback.
        """
        rag_state = read_rag_state(ctx)
        raw = rag_state.get("rag_filter") or rag_state.get("rag_filters")
        if not isinstance(raw, list):
            return None
        scopes = [item for item in raw if isinstance(item, dict)]
        return scopes or None

    @classmethod
    def _inject_frontend_filters(
        cls,
        *,
        args: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge frontend RAG scope into tool arguments for execution and trace."""
        filters = cls._parse_filters(args.get("filters"))
        rag_filter = cls._read_frontend_rag_filter(ctx)
        if not rag_filter:
            args["filters"] = filters
            return filters

        merged = dict(filters)
        merged["rag_filter"] = rag_filter
        args["filters"] = merged
        logger.info("[RagRetrieval] injected frontend rag_filter into tool filters: scopes=%d", len(rag_filter))
        return merged

    @staticmethod
    def _parse_int(raw: Any, *, default: int = 0) -> int:
        """Parse an integer from a possibly-string value."""
        if raw is None:
            return default
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _slim_state_delta(state_delta: dict[str, Any]) -> dict[str, Any]:
        """Drop workflow-private RAG internals before writing back to session."""
        slim = dict(state_delta)
        workflows = slim.get("_workflows")
        if isinstance(workflows, dict) and "rag" in workflows:
            workflows = dict(workflows)
            workflows.pop("rag", None)
            if workflows:
                slim["_workflows"] = workflows
            else:
                slim.pop("_workflows", None)
        return slim

    async def execute(
        self,
        tool_call: Any,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        """Auto-drive RAG workflow — 一次调用完成全流程。"""
        t_total = time.perf_counter()
        args = tool_call.arguments
        ctx = context or {}

        query = args.get("query", "")
        source = args.get("source", "auto")
        filters = self._inject_frontend_filters(args=args, ctx=ctx)
        max_retries = self._parse_int(args.get("max_retries"), default=1)

        if not query.strip():
            return AgentToolResult.error_result(tool_call.id, "query 参数不能为空")

        logger.info(
            "[RagRetrieval] query=%s, source=%s, max_retries=%d",
            query[:80], source, max_retries,
        )

        # Build workflow (stage run 函数直接 import，无需传 tools)
        flow = RagRetrievalWorkflow(clients=self._clients)

        # Generate unique instance ID
        iid = f"rag_{uuid.uuid4().hex[:12]}"

        def _apply_state_delta(state_delta: dict[str, Any] | None) -> None:
            """Apply workflow state_delta to this tool-local context.

            Workflow.execute is stateless between calls; the caller must feed
            the returned _workflows state back into the next transition.
            """
            if not state_delta:
                return
            for key, value in state_delta.items():
                if (
                    isinstance(value, dict)
                    and isinstance(ctx.get(key), dict)
                ):
                    ctx[key].update(value)
                else:
                    ctx[key] = value

        async def _step(action: str, step_args: dict[str, Any] | None = None) -> Any:
            result = await flow.execute(iid, action, step_args or {}, ctx)
            if result.success:
                _apply_state_delta(result.state_delta)
            return result

        async def _route_recall_rank() -> Any:
            """Drive rewritten -> recalled -> ranked, including internal recall."""
            result = await _step("route")
            if not result.success or result.new_state == "no_evidence":
                return result

            if result.new_state == "docs_selected":
                result = await _step("recall")
                if not result.success:
                    return result

            if result.new_state == "insufficient":
                return result

            return await _step("rank")

        async def _read_decide(result: Any) -> Any:
            """Drive ranked -> evidence_checked -> terminal/insufficient."""
            if result.new_state == "insufficient":
                return result

            result = await _step("read")
            if not result.success:
                return result

            return await _step("decide")

        # ── Step 1: start ──
        start_args = {
            "query": query,
            "source": source,
            "filters": filters,
            "max_retries": max_retries,
        }
        result = await _step("start", start_args)
        if not result.success:
            return self._error_result(tool_call.id, result.digest)

        if result.new_state in ("no_retrieval",):
            return self._no_retrieval_result(tool_call.id, result, ctx)

        # ── Step 2: rewrite ──
        result = await _step("rewrite")
        if not result.success:
            return self._error_result(tool_call.id, result.digest)

        # ── Step 3-5: route → recall → rank → read → decide ──
        result = await _route_recall_rank()
        if not result.success:
            return self._error_result(tool_call.id, result.digest)
        if result.new_state == "no_evidence":
            return self._no_evidence_result(tool_call.id, result, ctx)

        result = await _read_decide(result)
        if not result.success:
            return self._error_result(tool_call.id, result.digest)

        # ── Retry loop ──
        while result.new_state == "insufficient":
            result = await _step("retry")
            if not result.success:
                return self._error_result(tool_call.id, result.digest)
            if result.new_state in ("answer_ready", "no_evidence"):
                break

            result = await _route_recall_rank()
            if not result.success:
                return self._error_result(tool_call.id, result.digest)
            if result.new_state == "no_evidence":
                break

            result = await _read_decide(result)
            if not result.success:
                return self._error_result(tool_call.id, result.digest)

        return self._build_final_result(tool_call.id, result, ctx, t_total)

    # ── Result builders ──────────────────────────────────────────────────

    def _build_final_result(
        self,
        tool_call_id: str,
        flow_result: Any,
        ctx: dict[str, Any],
        t_start: float,
    ) -> AgentToolResult:
        """Build the final AgentToolResult from the workflow's terminal state."""
        total_ms = int((time.perf_counter() - t_start) * 1000)
        message = flow_result.message or "检索完成"

        # Collect state_delta from all transitions
        state_delta = {}
        if flow_result.state_delta:
            state_delta.update(flow_result.state_delta)
        state_delta = self._slim_state_delta(state_delta)

        # Extract evidence pack info for digest
        evidence_pack = state_delta.get("_rag_evidence_pack", {})
        ref_count = len(state_delta.get("_rag_references", []))
        evidence_count = evidence_pack.get("evidence_count", 0)

        result = AgentToolResult.text_result(
            tool_call_id=tool_call_id,
            text=message,
            metadata={"state_delta": state_delta} if state_delta else {},
            llm_digest=ToolDigest(
                tool="rag",
                status="ok" if flow_result.new_state == "answer_ready" else "partial",
                summary=f"检索完成: {ref_count} 引用, {evidence_count} 证据, {total_ms}ms",
                state_keys=("_rag_evidence_pack", "_rag_references"),
            ),
        )

        # Frontend digest
        status = "success" if flow_result.new_state == "answer_ready" else "warning"
        digest = FrontendDigest(
            tool_name="rag",
            display_type=ToolDisplayType.SEARCH,
            minimal=MinimalView(
                title="知识检索",
                summary=f"找到 {ref_count} 条引用，{evidence_count} 条证据 ({total_ms}ms)",
                icon="search",
                status=status,
            ),
            detailed=DetailedView(
                title="RAG 检索结果",
                sections=[
                    ViewSection(
                        heading="摘要",
                        content_type="text",
                        data=f"引用数: {ref_count} | 证据数: {evidence_count} | 总耗时: {total_ms}ms",
                    ),
                ],
            ),
        )
        apply_dual_layer(result, digest, message)
        return result

    def _no_retrieval_result(
        self,
        tool_call_id: str,
        flow_result: Any,
        ctx: dict[str, Any],
    ) -> AgentToolResult:
        message = flow_result.message or "问题无需检索，可直接回答。"
        result = AgentToolResult.text_result(
            tool_call_id=tool_call_id,
            text=message,
            llm_digest=ToolDigest(
                tool="rag",
                status="ok",
                summary="无需检索",
            ),
        )
        digest = FrontendDigest(
            tool_name="rag",
            display_type=ToolDisplayType.TEXT,
            minimal=MinimalView(
                title="知识检索",
                summary="无需检索，可直接回答",
                icon="chat",
                status="info",
            ),
            detailed=DetailedView(
                title="RAG 检索",
                sections=[ViewSection(heading="结果", content_type="text", data=message)],
            ),
        )
        apply_dual_layer(result, digest, "[RAG] 无需检索")
        return result

    def _no_evidence_result(
        self,
        tool_call_id: str,
        flow_result: Any,
        ctx: dict[str, Any],
    ) -> AgentToolResult:
        message = flow_result.message or "未找到相关内容。"
        result = AgentToolResult.text_result(
            tool_call_id=tool_call_id,
            text=message,
            llm_digest=ToolDigest(
                tool="rag",
                status="partial",
                summary="未找到相关内容",
            ),
        )
        digest = FrontendDigest(
            tool_name="rag",
            display_type=ToolDisplayType.TEXT,
            minimal=MinimalView(
                title="知识检索",
                summary="未找到相关内容",
                icon="search",
                status="warning",
            ),
            detailed=DetailedView(
                title="RAG 检索",
                sections=[ViewSection(heading="结果", content_type="text", data=message)],
            ),
        )
        apply_dual_layer(result, digest, "[RAG] 未找到相关内容")
        return result

    @staticmethod
    def _error_result(tool_call_id: str, message: str) -> AgentToolResult:
        return AgentToolResult.error_result(tool_call_id, f"RAG workflow error: {message}")
