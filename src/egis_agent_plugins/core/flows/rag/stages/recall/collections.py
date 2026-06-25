"""三级知识库 → Milvus collection 路由层。

严格对齐 WeKnora 端 [collection_resolver.go](file:///Users/frankie/Documents/work/workspace/ai-app/WeKnora/internal/application/repository/retriever/milvus/collection_resolver.go) 的命名约定：

- ``personal`` → ``personal_knowledge_base``
- ``public``   → ``public_knowledge_base``
- ``enterprise`` → ``enterprise_<lower(knowledge_base_id)>``（属主库，一库一 collection）
- summary collection → ``summary_knowledge_base``（与 KB category 无关，全局共用）

enterprise 命名规则：直接拼接 `lower(id)`，**不做 sanitize**，调用方需保证 KB ID
合法（Milvus collection 名只能含字母 / 数字 / 下划线，首字符为字母或下划线）。
保留 ``sanitize_collection_suffix`` 函数以兼容可能的外部导入，但本文件内部不再使用。
"""

from __future__ import annotations

import logging

from egis_agent_plugins.core.service.base.postgres_client import KBMeta

logger = logging.getLogger(__name__)

# WeKnora 端固定常量（migrations + Go 实现）。如需自定义请改 RAGConfig。
DEFAULT_PERSONAL_COLLECTION = "personal_knowledge_base"
DEFAULT_PUBLIC_COLLECTION = "public_knowledge_base"
DEFAULT_SUMMARY_COLLECTION = "summary_knowledge_base"

CATEGORY_PERSONAL = "personal"
CATEGORY_PUBLIC = "public"
CATEGORY_ENTERPRISE = "enterprise"


def sanitize_collection_suffix(s: str) -> str:
    """与 WeKnora 端 ``sanitizeCollectionSuffix`` 行为完全一致。"""
    if not s:
        return "default"
    return s.replace("-", "_").replace(".", "_").replace("/", "_")


def enterprise_collection_name(meta: KBMeta) -> str:
    """属主库（enterprise）的 collection 名：``enterprise_<lower(knowledge_base_id)>``。

    直接拼接 `meta.id.lower()`，**不做 sanitize**。调用方（KB 创建逻辑）需保证写入
    PG 的 ``id`` 只含 Milvus collection 名合法字符（字母 / 数字 / 下划线）。
    使用完整 ID，不拼 unix_ms、不截断。
    """
    return f"enterprise_{meta.id.lower()}"


def embedding_collection_for(
    meta: KBMeta,
    *,
    personal_collection: str = DEFAULT_PERSONAL_COLLECTION,
    public_collection: str = DEFAULT_PUBLIC_COLLECTION,
) -> str | None:
    """返回该 KB 应该查询的 embedding collection 名。未识别的 category 返回 None 并记日志。"""
    if meta.category == CATEGORY_PERSONAL:
        return personal_collection
    if meta.category == CATEGORY_PUBLIC:
        return public_collection
    if meta.category == CATEGORY_ENTERPRISE:
        return enterprise_collection_name(meta)
    logger.warning(
        "[CollectionResolver] unknown KB category=%r id=%s, skipping",
        meta.category,
        meta.id,
    )
    return None


def group_by_collection(
    kb_metas: list[KBMeta],
    *,
    personal_collection: str = DEFAULT_PERSONAL_COLLECTION,
    public_collection: str = DEFAULT_PUBLIC_COLLECTION,
) -> dict[str, list[str]]:
    """把 KB 列表按目标 collection 聚合，返回 ``{collection_name: [kb_id, ...]}``。

    - personal/public 共享一个 collection（多个 KB 的 ID 会聚到同一 key）
    - 每个 enterprise KB 自成一个 key
    - 未识别 category 的 KB 会被跳过（已打 warning）
    """
    result: dict[str, list[str]] = {}
    for meta in kb_metas:
        col = embedding_collection_for(
            meta,
            personal_collection=personal_collection,
            public_collection=public_collection,
        )
        if not col:
            continue
        result.setdefault(col, []).append(meta.id)
    return result


def group_by_collection_from_names(
    collection_map: dict[str, list[str]],
) -> dict[str, list[str]]:
    """接受预计算的 ``{collection_name: [kb_id, ...]}`` 映射，直接返回。

    当 Knowledge 表已存储 ``collection_name`` 字段时，调用方可以跳过
    KB category 路由，直接使用此函数。
    过滤空 key（collection_name 为空串的记录会被跳过）。
    """
    return {
        col: kb_ids
        for col, kb_ids in collection_map.items()
        if col and kb_ids
    }
