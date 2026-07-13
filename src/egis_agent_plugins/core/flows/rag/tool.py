"""RagTool — Auto-Drive Wrapper

对 LLM 暴露单一 ``rag`` 工具，内部 auto-drive 完成全流程。
LLM 不感知阶段边界，一次调用拿到 evidence pack。

继承 ``AgentTool``（非 ``WorkflowTool``），因为 ``WorkflowTool`` 会
把每个 transition action 暴露给 LLM，而我们希望 LLM 只看到一次 ``run``。

1. rewrite: 仅做轻量分词，为 BM25 生成空格分隔文本
2. select: summary 与 metadata 两路 hybrid search，经 RRF 选文档
3. recall/rank: 文档内 hybrid recall → rerank → composite score → MMR
4. read: 只对 MMR 选中短块做同文档双向上下文扩展
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from opentelemetry import trace as otel_trace

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
from egis_agent_plugins.core.flows.rag.schema import DEFAULT_QUALITY_MAX_RETRIES
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
    description = """一体化 RAG 检索工具 — 基于原子问题和资料范围返回可追溯 evidence pack。

功能：
- 轻量分词，不扩写问题
- summary/metadata 双路 hybrid + RRF 选文档
- 文档内 hybrid recall、Rerank、综合评分和标准 MMR
- 仅扩展 MMR 选中的短块
- 可选的内部充分性评估；关闭时首轮直接返回

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
            description="检索来源：auto/internal 使用知识库；web 显式使用网络检索",
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
    ]

    # data_source: RAG 工具产出 evidence，计入 citation 统计
    data_source = True

    # 声明写入 session.state 的 keys
    output_state_keys = ("_rag_evidence_pack", "_rag_references", "user:rag_state")

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
    def _parse_bool(raw: Any, *, default: bool) -> bool:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    @classmethod
    def _evaluation_enabled_from_env(cls) -> bool:
        """RAG 内部评估是部署配置，不允许由模型通过工具参数控制。"""
        return cls._parse_bool(os.getenv("RAG_ENABLE_EVALUATION"), default=False)

    @staticmethod
    def _read_frontend_rag_filter(ctx: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Read frontend scope from tool context.

        The frontend scope is request/runtime state, not something the LLM
        should be trusted to synthesize. The after-model callback injects it
        before tool tracing; this method also enforces the same contract at execution time.
        """
        rag_state = read_rag_state(ctx)
        raw = rag_state.get("rag_filter") or rag_state.get("rag_filters")
        if not isinstance(raw, list):
            return None
        scopes = [item for item in raw if isinstance(item, dict)]
        return scopes or None

    @staticmethod
    def _read_previous_rag_context(ctx: dict[str, Any]) -> dict[str, Any]:
        """Read only the compact multi-turn context persisted in rag_state."""
        rag_state = read_rag_state(ctx)
        context = rag_state.get("context") if isinstance(rag_state.get("context"), dict) else {}
        documents = (
            rag_state.get("selected_documents")
            if isinstance(rag_state.get("selected_documents"), list)
            else []
        )
        last_quality = (
            rag_state.get("last_quality")
            if isinstance(rag_state.get("last_quality"), dict)
            else {}
        )
        if not context and not documents:
            return {}
        return {
            "context": context,
            "selected_documents": [item for item in documents if isinstance(item, dict)],
            "last_quality": last_quality,
        }

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

        filters = {"rag_filter": rag_filter}
        args["filters"] = filters
        logger.debug(
            "[RagRetrieval] injected frontend rag_filter into tool filters: scopes=%d",
            len(rag_filter),
        )
        return filters

    @staticmethod
    def _inject_emit_event(ctx: dict[str, Any]) -> None:
        """Bridge system:event_handler → ctx['emit_event'] callable.

        ToolExecutor 将 StreamEventBus 注入到 ctx['system:event_handler']，
        但 workflow effect 里的 emit_progress / emit_references 读取的是
        ctx['emit_event']（一个接收 dict 的 callable）。本方法拉通两者。
        """
        if "emit_event" in ctx:
            return  # 已有显式注入（单测场景），不覆盖
        handler = ctx.get("system:event_handler")
        if handler is None:
            return
        on_custom = getattr(handler, "on_custom_event", None)
        if not callable(on_custom):
            return

        def _emit(event: dict[str, Any]) -> None:
            event_type = event.get("type", "rag_progress")
            if event_type == "custom":
                # events.py 里 emit 的 frontend_digest 封装
                on_custom(event.get("custom_type", ""), event.get("custom_data", {}))
            else:
                # rag_progress / references 等原始事件，统一走 custom event 通道
                on_custom(event_type, event)

        ctx["emit_event"] = _emit

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

    @staticmethod
    def _doc_trace_item(doc: dict[str, Any]) -> dict[str, Any]:
        scores = doc.get("document_match_scores") or {}
        return {
            "title": doc.get("knowledge_title") or doc.get("file_name") or doc.get("knowledge_id"),
            "kid": doc.get("knowledge_id", ""),
            "score": round(float(doc.get("score", 0.0) or 0.0), 4),
            "filename": round(float(scores.get("filename", 0.0) or 0.0), 4),
            "summary": round(float(scores.get("summary", 0.0) or 0.0), 4),
            "constraint_matched": bool(scores.get("constraint_matched", True)),
            "query_matches": [
                {
                    "query": match.get("query", ""),
                    "rank": int(match.get("rank", 0) or 0),
                    "score": round(float(match.get("score", 0.0) or 0.0), 4),
                }
                for match in doc.get("query_matches", [])
                if isinstance(match, dict)
            ],
        }

    @staticmethod
    def _instance_data_from_state_delta(state_delta: dict[str, Any] | None) -> dict[str, Any]:
        data = (
            (state_delta or {})
            .get("_workflows", {})
            .get("rag", {})
            .get("instances", {})
        )
        return next(iter(data.values())).get("data", {}) if data else {}

    @staticmethod
    def _ranked_document_summary(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in ranked:
            knowledge_id = str(item.get("knowledge_id") or "")
            key = knowledge_id or str(item.get("knowledge_title") or "unknown")
            entry = grouped.setdefault(
                key,
                {
                    "title": item.get("knowledge_title") or item.get("file_name") or knowledge_id,
                    "kid": knowledge_id,
                    "chunk_count": 0,
                },
            )
            entry["chunk_count"] += 1
        return sorted(
            grouped.values(),
            key=lambda item: (-int(item["chunk_count"]), str(item["title"])),
        )

    @classmethod
    def _set_rewrite_trace_attrs(
        cls,
        span: Any,
        state_delta: dict[str, Any] | None,
        step_args: dict[str, Any] | None,
    ) -> None:
        try:
            instance_data = cls._instance_data_from_state_delta(state_delta)
            rewrite = instance_data.get("rewrite") or {}
            span.set_attribute("rag.rewrite.input", str((step_args or {}).get("query") or ""))
            span.set_attribute(
                "rag.rewrite.result",
                json.dumps(
                    {
                        "intent": rewrite.get("intent", ""),
                        "resolved_query": rewrite.get("resolved_query", ""),
                        "continues_previous_rag": bool(
                            rewrite.get("continues_previous_rag")
                        ),
                        "reuse_previous_documents": bool(
                            rewrite.get("reuse_previous_documents")
                        ),
                        "rewrite_query": rewrite.get("rewrite_query", ""),
                        "doc_query": rewrite.get("doc_query", ""),
                        "doc_queries": rewrite.get("doc_queries", []),
                        "analysis_query": rewrite.get("analysis_query", ""),
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        except Exception:
            logger.debug("[RagRetrieval] failed to set rewrite trace attrs", exc_info=True)

    @classmethod
    def _set_branch_recall_trace_attrs(cls, span: Any, state_delta: dict[str, Any] | None) -> None:
        try:
            instance_data = cls._instance_data_from_state_delta(state_delta)
            selected = instance_data.get("selected_documents") or []
            rewrite = instance_data.get("rewrite") or {}
            span.set_attribute(
                "rag.document_select.queries",
                json.dumps(rewrite.get("doc_queries") or [], ensure_ascii=False, default=str),
            )
            selected_items = []
            for rank, document in enumerate(selected, 1):
                item = cls._doc_trace_item(document)
                item["rank"] = rank
                selected_items.append(item)
            span.set_attribute(
                "rag.document_select.selected",
                json.dumps(selected_items, ensure_ascii=False, default=str),
            )
            span.set_attribute("rag.document_select.selected_count", len(selected_items))
        except Exception:
            logger.debug("[RagRetrieval] failed to set branch recall trace attrs", exc_info=True)

    @classmethod
    def _set_rank_trace_attrs(cls, span: Any, state_delta: dict[str, Any] | None) -> None:
        try:
            instance_data = cls._instance_data_from_state_delta(state_delta)
            by_document = cls._ranked_document_summary(instance_data.get("ranked") or [])
            span.set_attribute(
                "rag.chunk_rerank.by_document",
                json.dumps(by_document, ensure_ascii=False, default=str),
            )
            span.set_attribute("rag.chunk_rerank.selected_count", sum(item["chunk_count"] for item in by_document))
        except Exception:
            logger.debug("[RagRetrieval] failed to set rank trace attrs", exc_info=True)

    @classmethod
    def _set_quality_trace_attrs(cls, span: Any, state_delta: dict[str, Any] | None) -> None:
        try:
            instance_data = cls._instance_data_from_state_delta(state_delta)
            quality = instance_data.get("quality_evaluation") or {}
            span.set_attribute("rag.quality.round", int(quality.get("round", 0) or 0))
            span.set_attribute("rag.quality.score", float(quality.get("score", 0.0) or 0.0))
            span.set_attribute("rag.quality.passed", bool(quality.get("passed")))
            span.set_attribute("rag.quality.result", str(quality.get("reason") or ""))
            span.set_attribute(
                "rag.quality.executed_queries",
                json.dumps(quality.get("executed_queries") or [], ensure_ascii=False, default=str),
            )
            span.set_attribute(
                "rag.quality.missing_points",
                json.dumps(quality.get("missing_points") or [], ensure_ascii=False, default=str),
            )
            span.set_attribute(
                "rag.quality.next_queries",
                json.dumps(quality.get("retry_queries") or [], ensure_ascii=False, default=str),
            )
            span.set_attribute(
                "rag.quality.requires_document_reselection",
                bool(quality.get("requires_document_reselection")),
            )
        except Exception:
            logger.debug("[RagRetrieval] failed to set quality trace attrs", exc_info=True)

    async def execute(
        self,
        tool_call: Any,
        context: dict[str, Any] | None = None,
    ) -> AgentToolResult:
        """Auto-drive RAG workflow — 一次调用完成全流程。"""
        t_total = time.perf_counter()
        args = tool_call.arguments
        ctx = context or {}

        # 桥接 emit_event: 把框架注入的 system:event_handler (StreamEventBus)
        # 转为 workflow effect 里 emit_progress / emit_references 能调用的 callable。
        self._inject_emit_event(ctx)

        query = args.get("query", "")
        source = args.get("source", "auto")
        filters = self._inject_frontend_filters(args=args, ctx=ctx)
        enable_evaluation = self._evaluation_enabled_from_env()
        max_retries = DEFAULT_QUALITY_MAX_RETRIES if enable_evaluation else 0

        if not query.strip():
            return AgentToolResult.error_result(tool_call.id, "query 参数不能为空")

        logger.debug(
            "[RagRetrieval] query=%s, source=%s, evaluation=%s, max_retries=%d",
            query[:80], source, enable_evaluation, max_retries,
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

        _tracer = otel_trace.get_tracer("egis_rag")

        async def _step(action: str, step_args: dict[str, Any] | None = None) -> Any:
            with _tracer.start_as_current_span(f"rag.{action}") as span:
                span.set_attribute("rag.action", action)
                research = ctx.get("temp:research_trace") if isinstance(ctx.get("temp:research_trace"), dict) else {}
                if research:
                    span.set_attribute("research.project_id", str(research.get("project_id") or ""))
                    span.set_attribute("research.turn", int(research.get("turn") or 0))
                    span.set_attribute("research.phase", str(research.get("phase") or "collecting"))
                    span.set_attribute("research.step", str(research.get("step") or "collect"))
                    span.set_attribute("research.task_id", str(research.get("task_id") or ""))
                    span.set_attribute("research.query", str(research.get("query") or "")[:500])
                span.set_attribute(
                    "rag.step.input",
                    json.dumps(step_args or {}, ensure_ascii=False, default=str, separators=(",", ":"))[:1200],
                )
                result = await flow.execute(iid, action, step_args or {}, ctx)
                span.set_attribute("rag.success", result.success)
                span.set_attribute("rag.new_state", result.new_state or "")
                span.set_attribute(
                    "rag.step.output",
                    json.dumps(
                        {"success": result.success, "state": result.new_state or "", "digest": str(result.digest or "")[:300]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                if action == "rewrite":
                    self._set_rewrite_trace_attrs(span, result.state_delta, step_args)
                if action == "branch_recall":
                    self._set_branch_recall_trace_attrs(span, result.state_delta)
                if action == "rank":
                    self._set_rank_trace_attrs(span, result.state_delta)
                if action == "read":
                    self._set_quality_trace_attrs(span, result.state_delta)
                if not result.success:
                    span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, result.digest or "step failed"))
                else:
                    _apply_state_delta(result.state_delta)
                return result

        async def _route_recall_rank() -> Any:
            """Drive rewritten -> routed -> branch_recalled -> recalled -> ranked."""
            result = await _step("route")
            if not result.success or result.new_state in ("no_evidence", "no_retrieval"):
                return result

            if result.new_state == "insufficient":
                return result

            result = await _step("branch_recall")
            if not result.success or result.new_state in ("no_evidence", "no_retrieval", "insufficient"):
                return result

            if result.new_state == "branch_recalled":
                result = await _step("chunk_recall")
                if not result.success or result.new_state == "insufficient":
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

        # ── Step 1: rewrite/router ──
        initial_args = {
            "query": query,
            "current_user_input": str(ctx.get("temp:user_input") or query),
            "previous_rag_context": self._read_previous_rag_context(ctx),
            "source": source,
            "filters": filters,
            "max_retries": max_retries,
            "enable_evaluation": enable_evaluation,
        }
        result = await _step("rewrite", initial_args)
        if not result.success:
            return self._error_result(tool_call.id, result.digest)

        # ── Step 2-6: route → branch_recall → chunk_recall → rank → read → decide ──
        result = await _route_recall_rank()
        if not result.success:
            return self._error_result(tool_call.id, result.digest)
        if result.new_state == "no_retrieval":
            return self._no_retrieval_result(tool_call.id, result, ctx)
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
            if result.new_state == "no_retrieval":
                break
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
                state_keys=("_rag_evidence_pack", "_rag_references", "user:rag_state"),
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
        logger.debug(
            "[RAG_TOOL] final_result content_chars=%d llm_digest_chars=%d refs=%d evidence=%d",
            len(str(result.content or "")),
            len(result.llm_digest or ""),
            ref_count,
            evidence_count,
        )
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
        state_delta = self._slim_state_delta(flow_result.state_delta or {})
        result = AgentToolResult.text_result(
            tool_call_id=tool_call_id,
            text=message,
            metadata={"state_delta": state_delta} if state_delta else {},
            llm_digest=ToolDigest(
                tool="rag",
                status="partial",
                summary="未找到相关内容",
                state_keys=("user:rag_state",),
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
