"""PostgreSQL 客户端封装

只读访问 ``chunk`` / ``knowledge`` / ``knowledge_base`` 表（三级知识库重构后新表名）。

SQL 内统一使用 ``id_knowledge_base`` / ``id_knowledge`` / ``id_chunk`` 等语义化主键/外键列名，
通过 ``SELECT col AS alias`` 把新列别名回旧 key（如 ``id``、``knowledge_base_id``、``knowledge_id``），
上层 dataclass 字段保持稳定。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import asyncpg
from asyncpg import Pool

from egis_agent_plugins.core.internal.rag_config import RAGConfig

logger = logging.getLogger(__name__)


@dataclass
class KBMeta:
    """知识库路由元数据（名称解析 + collection 路由专用），不使用完整 KnowledgeBase。"""

    id: str
    name: str
    category: str  # personal / public / enterprise
    created_at_unix_ms: int
    owner: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "created_at_unix_ms": self.created_at_unix_ms,
            "owner": self.owner,
        }


@dataclass
class Chunk:
    """知识分块（PG ``chunk`` 表已不再含 ``tenant_id`` 列；权限通过 KB ID 走）"""
    id: str
    content: str
    chunk_index: int
    knowledge_id: str
    knowledge_base_id: str
    chunk_type: str = "text"
    source_type: int = 0  # deprecated, schema 无此列；保留以避免上层 to_dict 兼容性破坏
    is_enabled: bool = True
    start_at: int = 0
    end_at: int = 0
    parent_chunk_id: str = ""
    image_info: str = ""
    tag_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "chunk_index": self.chunk_index,
            "knowledge_id": self.knowledge_id,
            "knowledge_base_id": self.knowledge_base_id,
            "chunk_type": self.chunk_type,
            "source_type": self.source_type,
            "is_enabled": self.is_enabled,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "parent_chunk_id": self.parent_chunk_id,
            "image_info": self.image_info,
            "tag_id": self.tag_id,
            "created_at": self.created_at,
        }


@dataclass
class Knowledge:
    """知识文档（与 PG ``knowledge`` 表 000034 重建后字段对齐）"""
    id: str
    title: str
    knowledge_base_id: str
    description: str = ""
    type: str = "file"
    file_name: str = ""
    file_type: str = ""
    file_size: int = 0
    parse_status: str = ""
    enable_status: str = "0"          # '0' 启用 / '1' 禁用
    summary_status: str = "none"      # none / generating / completed
    tag_id: str = ""
    collection_name: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "knowledge_base_id": self.knowledge_base_id,
            "description": self.description,
            "type": self.type,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "parse_status": self.parse_status,
            "enable_status": self.enable_status,
            "summary_status": self.summary_status,
            "tag_id": self.tag_id,
            "collection_name": self.collection_name,
            "created_at": self.created_at,
        }


@dataclass
class KnowledgeBase:
    """知识库（DDL 重建后 embedding_model_id 已移至 knowledge 表）"""
    id: str
    name: str
    type: str = "document"
    description: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "created_at": self.created_at,
        }


class PostgresClient:
    """PostgreSQL 客户端（只读访问知识库相关表）。"""

    def __init__(self, config: RAGConfig) -> None:
        self._config = config
        self._pool: Pool | None = None

    async def connect(self) -> None:
        """建立连接池"""
        if self._pool:
            return

        try:
            self._pool = await asyncpg.create_pool(
                host=self._config.db_host,
                port=self._config.db_port,
                user=self._config.db_user,
                password=self._config.db_password,
                database=self._config.db_name,
                min_size=2,
                max_size=10,
                timeout=10,
                command_timeout=30,
            )
            logger.info(
                f"[Postgres] Connected to {self._config.db_host}:{self._config.db_port}/{self._config.db_name}"
            )
        except Exception as e:
            logger.error(f"[Postgres] Failed to connect: {e}")
            raise

    async def close(self) -> None:
        """关闭连接池"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("[Postgres] Connection pool closed")

    def _get_pool(self) -> Pool:
        """获取连接池"""
        if not self._pool:
            raise RuntimeError("Postgres client not connected, call connect() first")
        return self._pool

    async def get_knowledge_base_by_id(
        self,
        knowledge_base_id: str,
    ) -> KnowledgeBase | None:
        """根据 ID 获取知识库"""
        async with self._get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id_knowledge_base AS id, name, type, description, created_at
                FROM knowledge_base
                WHERE id_knowledge_base = $1 AND deleted_at IS NULL
                """,
                knowledge_base_id,
            )
            if not row:
                return None
            return KnowledgeBase(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                description=row["description"] or "",
                created_at=str(row["created_at"]) if row["created_at"] else "",
            )

    async def get_knowledge_by_id(
        self,
        knowledge_id: str,
    ) -> Knowledge | None:
        """根据 ID 获取知识文档"""
        async with self._get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id_knowledge AS id, title, id_knowledge_base AS knowledge_base_id,
                       description, type,
                       file_name, file_type, file_size, parse_status,
                       enable_status, summary_status, tag_id,
                       collection_name, created_at
                FROM knowledge
                WHERE id_knowledge = $1 AND deleted_at IS NULL
                """,
                knowledge_id,
            )
            if not row:
                return None
            return Knowledge(
                id=row["id"],
                title=row["title"],
                knowledge_base_id=row["knowledge_base_id"],
                description=row["description"] or "",
                type=row["type"],
                file_name=row["file_name"] or "",
                file_type=row["file_type"] or "",
                file_size=row["file_size"] or 0,
                parse_status=row["parse_status"] or "",
                enable_status=row["enable_status"] or "0",
                summary_status=row["summary_status"] or "none",
                tag_id=row["tag_id"] or "",
                collection_name=row["collection_name"] or "",
                created_at=str(row["created_at"]) if row["created_at"] else "",
            )

    async def get_knowledges_by_ids(
        self,
        knowledge_ids: list[str],
    ) -> list[Knowledge]:
        """批量获取知识文档"""
        if not knowledge_ids:
            return []

        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id_knowledge AS id, title, id_knowledge_base AS knowledge_base_id,
                       description, type,
                       file_name, file_type, file_size, parse_status,
                       enable_status, summary_status, tag_id,
                       collection_name, created_at
                FROM knowledge
                WHERE id_knowledge = ANY($1) AND deleted_at IS NULL
                """,
                knowledge_ids,
            )
            return [
                Knowledge(
                    id=row["id"],
                    title=row["title"],
                    knowledge_base_id=row["knowledge_base_id"],
                    description=row["description"] or "",
                    type=row["type"],
                    file_name=row["file_name"] or "",
                    file_type=row["file_type"] or "",
                    file_size=row["file_size"] or 0,
                    parse_status=row["parse_status"] or "",
                    enable_status=row["enable_status"] or "0",
                    summary_status=row["summary_status"] or "none",
                    tag_id=row["tag_id"] or "",
                    collection_name=row["collection_name"] or "",
                    created_at=str(row["created_at"]) if row["created_at"] else "",
                )
                for row in rows
            ]

    async def get_chunks_by_knowledge_id(
        self,
        knowledge_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        chunk_types: list[str] | None = None,
    ) -> tuple[list[Chunk], int]:
        """获取指定知识的所有分块"""
        async with self._get_pool().acquire() as conn:
            type_condition = ""
            params: list[Any] = [knowledge_id]

            if chunk_types:
                type_condition = "AND chunk_type = ANY($2)"
                params.append(chunk_types)

            count_query = f"""
                SELECT COUNT(*) as total
                FROM chunk
                WHERE id_knowledge = $1 AND is_enabled = true AND deleted_at IS NULL
                {type_condition}
            """
            total_row = await conn.fetchrow(count_query, *params)
            total = total_row["total"] if total_row else 0

            params.extend([limit, offset])
            data_query = f"""
                SELECT id_chunk AS id, content, chunk_index,
                       id_knowledge AS knowledge_id, id_knowledge_base AS knowledge_base_id,
                       chunk_type, is_enabled, start_at, end_at, parent_chunk_id,
                       image_info, tag_id, created_at
                FROM chunk
                WHERE id_knowledge = $1 AND is_enabled = true AND deleted_at IS NULL
                {type_condition}
                ORDER BY chunk_index ASC
                LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """
            rows = await conn.fetch(data_query, *params)

            chunks = [
                Chunk(
                    id=row["id"],
                    content=row["content"] or "",
                    chunk_index=row["chunk_index"],
                    knowledge_id=row["knowledge_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    chunk_type=row["chunk_type"],
                    source_type=0,
                    is_enabled=row["is_enabled"],
                    start_at=row["start_at"] or 0,
                    end_at=row["end_at"] or 0,
                    parent_chunk_id=row["parent_chunk_id"] or "",
                    image_info=row["image_info"] or "",
                    tag_id=row["tag_id"] or "",
                    created_at=str(row["created_at"]) if row["created_at"] else "",
                )
                for row in rows
            ]

            return chunks, total

    async def get_chunks_around_index(
        self,
        knowledge_id: str,
        center_index: int,
        radius: int = 5,
    ) -> list["Chunk"]:
        """获取 center_index 前后 radius 范围内的 chunks，按 chunk_index 排序。"""
        start_idx = max(0, center_index - radius)
        end_idx = center_index + radius
        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id_chunk AS id, content, chunk_index,
                       id_knowledge AS knowledge_id, id_knowledge_base AS knowledge_base_id,
                       chunk_type, is_enabled, start_at, end_at, parent_chunk_id,
                       image_info, tag_id, created_at
                FROM chunk
                WHERE id_knowledge = $1
                      AND chunk_index BETWEEN $2 AND $3
                      AND is_enabled = true
                      AND deleted_at IS NULL
                ORDER BY chunk_index ASC
                """,
                knowledge_id, start_idx, end_idx,
            )
            return [
                Chunk(
                    id=row["id"],
                    content=row["content"] or "",
                    chunk_index=row["chunk_index"],
                    knowledge_id=row["knowledge_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    chunk_type=row["chunk_type"],
                    source_type=0,
                    is_enabled=row["is_enabled"],
                    start_at=row["start_at"] or 0,
                    end_at=row["end_at"] or 0,
                    parent_chunk_id=row["parent_chunk_id"] or "",
                    image_info=row["image_info"] or "",
                    tag_id=row["tag_id"] or "",
                    created_at=str(row["created_at"]) if row["created_at"] else "",
                )
                for row in rows
            ]

    async def search_chunks_by_keywords(
        self,
        patterns: list[str],
        *,
        knowledge_base_ids: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        limit: int = 50,
    ) -> list[Chunk]:
        """关键词搜索（PostgreSQL ILIKE 查询，作为 Milvus BM25 的回退）"""
        if not patterns:
            return []

        async with self._get_pool().acquire() as conn:
            like_conditions = " OR ".join(
                f"content ILIKE ${i + 2}" for i in range(len(patterns))
            )
            like_values = [f"%{p}%" for p in patterns]

            filter_conditions: list[str] = []
            params: list[Any] = [limit]

            if knowledge_base_ids:
                filter_conditions.append(f"c.id_knowledge_base = ANY(${len(params) + 1})")
                params.append(knowledge_base_ids)

            if knowledge_ids:
                filter_conditions.append(f"c.id_knowledge = ANY(${len(params) + 1})")
                params.append(knowledge_ids)

            if tag_ids:
                filter_conditions.append(f"c.tag_id = ANY(${len(params) + 1})")
                params.append(tag_ids)

            filter_str = ""
            if filter_conditions:
                filter_str = "AND " + " AND ".join(filter_conditions)

            query = f"""
                SELECT c.id_chunk AS id, c.content, c.chunk_index,
                       c.id_knowledge AS knowledge_id,
                       c.id_knowledge_base AS knowledge_base_id,
                       c.chunk_type, c.is_enabled, c.start_at,
                       c.end_at, c.parent_chunk_id, c.image_info,
                       c.tag_id, c.created_at,
                       k.title as knowledge_title
                FROM chunk c
                JOIN knowledge k ON c.id_knowledge = k.id_knowledge
                WHERE c.is_enabled = true
                      AND c.deleted_at IS NULL
                      AND k.deleted_at IS NULL
                      AND ({like_conditions})
                      {filter_str}
                ORDER BY c.created_at DESC
                LIMIT $1
            """

            rows = await conn.fetch(query, *params, *like_values)

            return [
                Chunk(
                    id=row["id"],
                    content=row["content"] or "",
                    chunk_index=row["chunk_index"],
                    knowledge_id=row["knowledge_id"],
                    knowledge_base_id=row["knowledge_base_id"],
                    chunk_type=row["chunk_type"],
                    source_type=0,
                    is_enabled=row["is_enabled"],
                    start_at=row["start_at"] or 0,
                    end_at=row["end_at"] or 0,
                    parent_chunk_id=row["parent_chunk_id"] or "",
                    image_info=row["image_info"] or "",
                    tag_id=row["tag_id"] or "",
                    created_at=str(row["created_at"]) if row["created_at"] else "",
                )
                for row in rows
            ]

    async def get_chunk_count_by_knowledge_id(
        self,
        knowledge_id: str,
    ) -> int:
        """获取知识的分块总数"""
        async with self._get_pool().acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) as total
                FROM chunk
                WHERE id_knowledge = $1 AND is_enabled = true AND deleted_at IS NULL
                """,
                knowledge_id,
            )
            return row["total"] if row else 0

    # ============================================================
    # 名称解析类查询（三级知识库重构后新增，服务于 RAG 工具的「名称入参」）
    # ============================================================

    async def resolve_kb_metas_by_names(
        self,
        names: list[str],
    ) -> list[KBMeta]:
        """按名称精确查知识库，返回路由元数据（含 category + created_at）。

        同名知识库会一起返回。调用方负责后续去重。
        """
        if not names:
            return []

        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id_knowledge_base AS id, name, category,
                       COALESCE(owner, '') AS owner, created_at
                FROM knowledge_base
                WHERE name = ANY($1) AND deleted_at IS NULL
                """,
                names,
            )
            return [
                KBMeta(
                    id=row["id"],
                    name=row["name"],
                    category=row["category"],
                    created_at_unix_ms=int(row["created_at"].timestamp() * 1000)
                    if row["created_at"]
                    else 0,
                    owner=row["owner"] or "",
                )
                for row in rows
            ]

    async def resolve_kb_metas_by_ids(
        self,
        kb_ids: list[str],
    ) -> list[KBMeta]:
        """按 ID 查知识库路由元数据（上层 kb_ids 参数需要走 collection 路由时用）。"""
        if not kb_ids:
            return []

        async with self._get_pool().acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id_knowledge_base AS id, name, category,
                       COALESCE(owner, '') AS owner, created_at
                FROM knowledge_base
                WHERE id_knowledge_base = ANY($1) AND deleted_at IS NULL
                """,
                kb_ids,
            )
            return [
                KBMeta(
                    id=row["id"],
                    name=row["name"],
                    category=row["category"],
                    created_at_unix_ms=int(row["created_at"].timestamp() * 1000)
                    if row["created_at"]
                    else 0,
                    owner=row["owner"] or "",
                )
                for row in rows
            ]

    async def resolve_tag_ids_by_names(
        self,
        names: list[str],
        kb_ids: list[str] | None = None,
    ) -> list[str]:
        """按 tag 名称精确查 tag ID。同名一起命中；kb_ids 限定则只在该范围内查。"""
        if not names:
            return []

        async with self._get_pool().acquire() as conn:
            if kb_ids:
                rows = await conn.fetch(
                    """
                    SELECT id_knowledge_tag AS id
                    FROM knowledge_tag
                    WHERE name = ANY($1) AND id_knowledge_base = ANY($2)
                          AND deleted_at IS NULL
                    """,
                    names,
                    kb_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id_knowledge_tag AS id
                    FROM knowledge_tag
                    WHERE name = ANY($1) AND deleted_at IS NULL
                    """,
                    names,
                )
            return [row["id"] for row in rows]

    async def resolve_knowledge_ids_by_file_names(
        self,
        names: list[str],
        kb_ids: list[str] | None = None,
    ) -> list[str]:
        """按文件名精确查 knowledge ID（title 或 file_name 匹配）。同名一起命中。"""
        if not names:
            return []

        async with self._get_pool().acquire() as conn:
            if kb_ids:
                rows = await conn.fetch(
                    """
                    SELECT id_knowledge AS id
                    FROM knowledge
                    WHERE (title = ANY($1) OR file_name = ANY($1))
                          AND id_knowledge_base = ANY($2)
                          AND deleted_at IS NULL
                    """,
                    names,
                    kb_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id_knowledge AS id
                    FROM knowledge
                    WHERE (title = ANY($1) OR file_name = ANY($1))
                          AND deleted_at IS NULL
                    """,
                    names,
                )
            return [row["id"] for row in rows]
