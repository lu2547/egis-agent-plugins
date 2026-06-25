"""RAG 工具的「名称入参」解析层。

把工具入参中的 ``kb_names`` / ``tag_names`` / ``file_names`` 转换为对应的 ID 列表，
统一汇总为一份 ``ResolvedFilters``，供后续 Milvus / PostgreSQL 检索阶段使用。

设计要点：
- **精确匹配**：name = ANY($1)，同名 tag/file 一起命中（用户已确认接受）
- **ID 与 Names 并存**：最终 ID 集合 = ids ∪ resolve(names)；任一为空则跳过该过滤
- **kb_ids 边界传递**：解析 tag/file 时若已知 kb_ids，会把范围限制在这些 KB 内，避免跨库误命中

数据来源：[PostgresClient](file:///Users/frankie/Documents/work/workspace/ai-app/egis-agents/src/egis_agents/core/service/base/postgres_client.py)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from egis_agent_plugins.core.service.base.postgres_client import KBMeta, PostgresClient

logger = logging.getLogger(__name__)


@dataclass
class ResolvedFilters:
    """名称 + ID 解析后的统一过滤结构。

    所有字段都是去重后的列表；空列表表示「不过滤该维度」。
    """

    kb_ids: list[str] = field(default_factory=list)
    kb_metas: list[KBMeta] = field(default_factory=list)
    tag_ids: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)

    def has_any_filter(self) -> bool:
        return bool(self.kb_ids or self.tag_ids or self.knowledge_ids)


async def resolve_filters(
    pg: PostgresClient,
    *,
    kb_ids: list[str] | None = None,
    kb_names: list[str] | None = None,
    tag_ids: list[str] | None = None,
    tag_names: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    file_names: list[str] | None = None,
) -> ResolvedFilters:
    """统一解析 RAG 工具入参，返回合并后的 ID 集合 + KB 路由元数据。

    流程:
    1. 解析 KB：(kb_ids ∪ resolve_kb_ids_by_names(kb_names)) → kb_metas
    2. 解析 tag：(tag_ids ∪ resolve_tag_ids_by_names(tag_names, kb_ids))
    3. 解析 file：(knowledge_ids ∪ resolve_knowledge_ids_by_file_names(file_names, kb_ids))

    任一 PG 解析失败仅记日志并跳过名称段，不破坏调用方的 ID 链路。
    """
    # ---- KB ----
    kb_meta_map: dict[str, KBMeta] = {}

    if kb_ids:
        try:
            for meta in await pg.resolve_kb_metas_by_ids(kb_ids):
                kb_meta_map[meta.id] = meta
        except Exception as e:
            logger.warning("[NameResolver] resolve_kb_metas_by_ids failed: %s", e)

    if kb_names:
        try:
            for meta in await pg.resolve_kb_metas_by_names(kb_names):
                kb_meta_map[meta.id] = meta
        except Exception as e:
            logger.warning("[NameResolver] resolve_kb_metas_by_names failed: %s", e)

    merged_kb_ids = list(kb_meta_map.keys())
    merged_kb_metas = list(kb_meta_map.values())

    # ---- tag ----
    tag_id_set: set[str] = set(tag_ids or [])
    if tag_names:
        try:
            for tid in await pg.resolve_tag_ids_by_names(
                tag_names, kb_ids=merged_kb_ids or None
            ):
                tag_id_set.add(tid)
        except Exception as e:
            logger.warning("[NameResolver] resolve_tag_ids_by_names failed: %s", e)

    # ---- file / knowledge ----
    knowledge_id_set: set[str] = set(knowledge_ids or [])
    if file_names:
        try:
            for kid in await pg.resolve_knowledge_ids_by_file_names(
                file_names, kb_ids=merged_kb_ids or None
            ):
                knowledge_id_set.add(kid)
        except Exception as e:
            logger.warning(
                "[NameResolver] resolve_knowledge_ids_by_file_names failed: %s", e
            )

    return ResolvedFilters(
        kb_ids=merged_kb_ids,
        kb_metas=merged_kb_metas,
        tag_ids=sorted(tag_id_set),
        knowledge_ids=sorted(knowledge_id_set),
    )
