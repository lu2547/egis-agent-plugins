"""查看文档分块 stage —— 从 PostgreSQL 读取指定 knowledge_id 的所有 chunks。

模块级 ``run()`` 是 workflow 内部 operator 入口，返回结构化 chunk 列表。
"""

from __future__ import annotations

import logging
from typing import Any

from egis_agent_plugins.core.flows.rag.clients import RAGClients
from egis_agent_plugins.core.flows.rag.filters import resolve_filters
from egis_agent_plugins.core.flows.rag._services.scope_adapter import (
    scope_plan_from_filters_or_context,
)

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────


def _resolve_knowledge_id(args: dict[str, Any]) -> str:
    """LLM 经常传错参数名或类型，做防御性解析。"""
    import json as _json

    kid = args.get("knowledge_id") or args.get("knowledge_ids") or ""
    if isinstance(kid, str):
        kid = kid.strip()
        if kid.startswith("["):
            try:
                parsed = _json.loads(kid)
                if isinstance(parsed, list) and parsed:
                    return str(parsed[0]).strip()
            except (ValueError, TypeError):
                pass
        return kid
    if isinstance(kid, list):
        return str(kid[0]).strip() if kid else ""
    return str(kid).strip() if kid else ""


def _format_output(
    *,
    knowledge_id: str,
    knowledge_title: str,
    total: int,
    chunks: list,
    limit: int,
    offset: int,
) -> str:
    """格式化「按需深度阅读」的完整输出（每个 chunk 全文）。"""
    lines = [
        "=== 知识文档分块 ===",
        "",
        f"文档: {knowledge_title}",
        f"文档 ID: {knowledge_id}",
        f"总分块数: {total}",
    ]
    if not chunks:
        lines.append("没有找到分块内容。请确认文档已解析完成。")
        return "\n".join(lines)

    lines.append(f"获取: {len(chunks)} 个分块, 范围: {chunks[0].chunk_index} - {chunks[-1].chunk_index}")
    lines.append(f"当前页: 第 {offset // limit + 1} 页 (每页 {limit} 条)")
    if offset + len(chunks) < total:
        lines.append(f"还有更多分块，workflow 可从 offset={offset + len(chunks)} 继续读取。")
    lines.append("")

    for i, c in enumerate(chunks, start=1):
        content = (c.content or "").strip()
        chunk_type = c.chunk_type or "text"
        start_at = c.start_at or ""
        end_at = c.end_at or ""
        header = f"── 分块 #{i} (index={c.chunk_index}, type={chunk_type}"
        if start_at or end_at:
            header += f", 位置: {start_at} → {end_at}"
        header += ") ──"
        lines.append(header)
        lines.append(content if content else "(空)")
        lines.append("")

    return "\n".join(lines)


# ── Stage entrypoint ───────────────────────────────────────────────────────


async def run(
    *,
    clients: RAGClients,
    args: dict[str, Any],
    ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """list-knowledge-chunks 业务核心。返回结构化 dict。"""
    knowledge_id = _resolve_knowledge_id(args)
    file_name = args.get("file_name") or ""
    if isinstance(file_name, list):
        file_name = file_name[0] if file_name else ""
    file_name = str(file_name).strip()

    scope_plan = scope_plan_from_filters_or_context(args, ctx)
    scoped_kb_ids = scope_plan.flat_kb_ids()
    scoped_knowledge_ids = scope_plan.flat_knowledge_ids()
    kb_ids_in = scoped_kb_ids or clients.default_kb_ids

    if scoped_knowledge_ids:
        if knowledge_id and knowledge_id not in scoped_knowledge_ids:
            return {"error": "knowledge_id 不在用户选定的文档范围内"}
        if not knowledge_id and not file_name:
            knowledge_id = scoped_knowledge_ids[0]

    limit = min(args.get("limit", 20), 100)
    offset = max(args.get("offset", 0), 0)

    if not knowledge_id and not file_name:
        return {"error": "knowledge_id 与 file_name 至少提供一个"}

    try:
        await clients.postgres.connect()

        # file_name 反查
        if not knowledge_id and file_name:
            resolved = await resolve_filters(
                clients.postgres,
                kb_ids=kb_ids_in,
                knowledge_ids=scoped_knowledge_ids or None,
                file_names=[file_name],
            )
            if not resolved.knowledge_ids:
                return {"error": f"未找到名为 '{file_name}' 的文档"}
            knowledge_id = resolved.knowledge_ids[0]
            if len(resolved.knowledge_ids) > 1:
                logger.debug(
                    "[ListChunks] file_name=%s 命中 %d 个文档，取首个 knowledge_id=%s",
                    file_name, len(resolved.knowledge_ids), knowledge_id,
                )

        logger.debug(
            "[ListChunks] knowledge_id=%s, limit=%d, offset=%d", knowledge_id, limit, offset
        )

        knowledge = await clients.postgres.get_knowledge_by_id(knowledge_id)
        if not knowledge:
            return {"error": f"未找到文档: {knowledge_id}"}

        # 权限校验
        if clients.default_kb_ids and knowledge.knowledge_base_id not in clients.default_kb_ids:
            return {"error": f"无权访问知识库: {knowledge.knowledge_base_id}"}
        if scoped_kb_ids and knowledge.knowledge_base_id not in scoped_kb_ids:
            return {"error": f"文档不在用户选定的知识库范围内: {knowledge.knowledge_base_id}"}

        chunks, total = await clients.postgres.get_chunks_by_knowledge_id(
            knowledge_id, limit=limit, offset=offset,
        )

        if not chunks:
            empty_text = (
                f"=== 知识文档分块 ===\n\n"
                f"文档: {knowledge.title}\n"
                f"文档 ID: {knowledge_id}\n\n"
                f"这份文档暂无可用的分块内容。请确认文档已解析完成，或换一份文档。"
            )
            return {
                "knowledge_id": knowledge_id,
                "knowledge_title": knowledge.title,
                "total_chunks": total,
                "fetched_chunks": 0,
                "chunks": [],
                "summary": empty_text,
                "_empty": True,
            }

        full_text = _format_output(
            knowledge_id=knowledge_id,
            knowledge_title=knowledge.title,
            total=total,
            chunks=chunks,
            limit=limit,
            offset=offset,
        )

        return {
            "knowledge_id": knowledge_id,
            "knowledge_title": knowledge.title,
            "total_chunks": total,
            "fetched_chunks": len(chunks),
            "page": offset // limit + 1,
            "page_size": limit,
            "chunks": [
                {
                    "seq": i + 1,
                    "chunk_id": c.id,
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "chunk_type": c.chunk_type,
                    "start_at": c.start_at,
                    "end_at": c.end_at,
                    "parent_chunk_id": c.parent_chunk_id,
                }
                for i, c in enumerate(chunks)
            ],
            "summary": full_text,
        }
    except Exception as e:
        logger.error("[ListChunks] 查询失败: %s", e)
        return {"error": f"查询失败: {e}"}
