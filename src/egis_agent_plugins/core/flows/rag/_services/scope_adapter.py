"""RAG scope adapter.

This module converts UI/tool filtering input into query scopes consumed by
select-documents and recall stages. Frontend ``rag_state`` is intentionally
limited to the per-KB tree shape:

    {"rag_filter": [
        {"kb_id": "...", "kb_name": "...", "tags": [
            {"tag_id": "...", "tag_name": "...", "files": [{"id": "..."}]}
        ]}
    ]}

The important semantic is per-KB scoping. Inside a KB, selected tags and files
are OR-ed: ``kb AND (tag in selected_tags OR knowledge_id in selected_files)``.
This lets "tag1 + files under tag2" work without turning tag/file into an
overly strict AND filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RAG_STATE_KEY = "user:rag_state"


def _quote(value: str) -> str:
    """Escape a scalar for Milvus filter expressions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _quoted_values(values: list[str]) -> str:
    return ", ".join(f'"{_quote(v)}"' for v in values if v)


def _in_expr(field: str, values: list[str]) -> str | None:
    values = [v for v in values if v]
    if not values:
        return None
    return f"{field} in [{_quoted_values(values)}]"


def _array_contains_any_expr(field: str, values: list[str]) -> str | None:
    values = [v for v in values if v]
    if not values:
        return None
    return f"ARRAY_CONTAINS_ANY({field}, [{_quoted_values(values)}])"


@dataclass(frozen=True)
class RecallScope:
    """A single executable RAG scope, normally one knowledge base."""

    kb_id: str = ""
    kb_name: str = ""
    tag_ids: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    file_names: list[str] = field(default_factory=list)
    source: str = "flat"

    def with_extra_knowledge_ids(self, ids: list[str]) -> "RecallScope":
        merged = list(dict.fromkeys([*self.knowledge_ids, *ids]))
        return RecallScope(
            kb_id=self.kb_id,
            kb_name=self.kb_name,
            tag_ids=list(self.tag_ids),
            knowledge_ids=merged,
            file_names=list(self.file_names),
            source=self.source,
        )

    def to_filter_expr(self, *, include_enabled: bool = True) -> str | None:
        """Build a Milvus expression for this scope.

        The expression is:
            is_enabled AND kb AND (tag OR file)
        """
        base: list[str] = []
        if include_enabled:
            base.append("is_enabled == true")
        if self.kb_id:
            base.append(f'knowledge_base_id == "{_quote(self.kb_id)}"')

        selector_parts: list[str] = []
        tag_expr = _array_contains_any_expr("tag_id", self.tag_ids)
        if tag_expr:
            selector_parts.append(tag_expr)
        kid_expr = _in_expr("knowledge_id", self.knowledge_ids)
        if kid_expr:
            selector_parts.append(kid_expr)
        if self.file_names:
            like_parts = [f'file_name like "%{_quote(name)}%"' for name in self.file_names]
            selector_parts.append("(" + " or ".join(like_parts) + ")")

        if selector_parts:
            base.append("(" + " or ".join(selector_parts) + ")")

        return " and ".join(base) if base else None

    def to_flat_filters(self) -> dict[str, list[str]]:
        return {
            "knowledge_base_ids": [self.kb_id] if self.kb_id else [],
            "tag_ids": list(self.tag_ids),
            "knowledge_ids": list(self.knowledge_ids),
            "file_names": list(self.file_names),
        }


@dataclass(frozen=True)
class ScopePlan:
    """Normalized RAG scope plan."""

    scopes: list[RecallScope] = field(default_factory=list)
    source: str = "none"

    @property
    def has_scopes(self) -> bool:
        return bool(self.scopes)

    def flat_kb_ids(self) -> list[str]:
        return list(dict.fromkeys(s.kb_id for s in self.scopes if s.kb_id))

    def flat_tag_ids(self) -> list[str]:
        return list(dict.fromkeys(t for s in self.scopes for t in s.tag_ids))

    def flat_knowledge_ids(self) -> list[str]:
        return list(dict.fromkeys(k for s in self.scopes for k in s.knowledge_ids))


def read_rag_state(ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Read frontend-injected RAG state from a tool context."""
    raw = (ctx or {}).get("rag_state") or (ctx or {}).get(RAG_STATE_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def _extract_file_id(file_obj: Any) -> str:
    if isinstance(file_obj, dict):
        return str(
            file_obj.get("id")
            or file_obj.get("knowledge_id")
            or file_obj.get("file_id")
            or ""
        ).strip()
    return str(file_obj or "").strip()


def _scopes_from_rag_filter(rag_filter: Any) -> list[RecallScope]:
    if not isinstance(rag_filter, list):
        return []

    scopes: list[RecallScope] = []
    for item in rag_filter:
        if not isinstance(item, dict):
            continue
        kb_id = str(
            item.get("kb_id")
            or item.get("knowledge_base_id")
            or item.get("id")
            or ""
        ).strip()
        kb_name = str(item.get("kb_name") or item.get("name") or "").strip()
        tags_raw = item.get("tags")
        if tags_raw is None:
            tags_raw = item.get("tag")

        tag_ids: list[str] = []
        knowledge_ids: list[str] = []
        if isinstance(tags_raw, list):
            for tag in tags_raw:
                if not isinstance(tag, dict):
                    continue
                files = tag.get("files") or []
                file_ids = [_extract_file_id(f) for f in files] if isinstance(files, list) else []
                file_ids = [fid for fid in file_ids if fid]
                if file_ids:
                    knowledge_ids.extend(file_ids)
                    continue
                tag_id = str(tag.get("tag_id") or tag.get("id") or "").strip()
                if tag_id:
                    tag_ids.append(tag_id)

        direct_files = item.get("files") or []
        if isinstance(direct_files, list):
            knowledge_ids.extend(fid for fid in (_extract_file_id(f) for f in direct_files) if fid)

        if kb_id or tag_ids or knowledge_ids:
            scopes.append(
                RecallScope(
                    kb_id=kb_id,
                    kb_name=kb_name,
                    tag_ids=list(dict.fromkeys(tag_ids)),
                    knowledge_ids=list(dict.fromkeys(knowledge_ids)),
                    source="rag_filter",
                )
            )

    return scopes


def _scope_plan_from_mapping(mapping: dict[str, Any] | None, *, source: str) -> ScopePlan:
    if not isinstance(mapping, dict):
        return ScopePlan()
    raw = mapping.get("rag_filter") or mapping.get("rag_filters")
    if raw is None and isinstance(mapping.get("filters"), dict):
        nested = mapping["filters"]
        raw = nested.get("rag_filter") or nested.get("rag_filters")
    scopes = _scopes_from_rag_filter(raw)
    if scopes:
        return ScopePlan(scopes=scopes, source=source)
    return ScopePlan()


def scope_plan_from_filters(filters: dict[str, Any] | None) -> ScopePlan:
    """Build a scope plan from tool ``filters.rag_filter`` only."""
    return _scope_plan_from_mapping(filters, source="filters.rag_filter")


def scope_plan_from_context(ctx: dict[str, Any] | None) -> ScopePlan:
    """Build a scope plan from frontend ``rag_state.rag_filter`` only."""
    return _scope_plan_from_mapping(read_rag_state(ctx), source="rag_state.rag_filter")


def scope_plan_from_filters_or_context(
    filters: dict[str, Any] | None,
    ctx: dict[str, Any] | None,
) -> ScopePlan:
    """Prefer explicit tool filters, then fallback to runtime context."""
    plan = scope_plan_from_filters(filters)
    if plan.has_scopes:
        return plan
    return scope_plan_from_context(ctx)


def scopes_from_flat_filters(
    *,
    kb_ids: list[str] | None = None,
    tag_ids: list[str] | None = None,
    knowledge_ids: list[str] | None = None,
    file_names: list[str] | None = None,
) -> list[RecallScope]:
    """Convert legacy flat filters to per-KB scopes."""
    kb_ids = kb_ids or []
    tag_ids = tag_ids or []
    knowledge_ids = knowledge_ids or []
    file_names = file_names or []
    if kb_ids:
        return [
            RecallScope(
                kb_id=kb_id,
                tag_ids=list(tag_ids),
                knowledge_ids=list(knowledge_ids),
                file_names=list(file_names),
            )
            for kb_id in kb_ids
        ]
    if tag_ids or knowledge_ids or file_names:
        return [
            RecallScope(
                tag_ids=list(tag_ids),
                knowledge_ids=list(knowledge_ids),
                file_names=list(file_names),
            )
        ]
    return []
