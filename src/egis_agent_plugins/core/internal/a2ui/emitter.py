"""a2ui.emitter — A2UI compatibility adapter

发射逻辑位于 ``core.internal.emit``（拆分为 llm / frontend / trace 三通道）。
本模块保留 A2UI 侧历史入口。
"""

from __future__ import annotations

from ..emit import (
    attach_frontend_digest,
    emit_result as apply_dual_layer,
    set_llm_digest,
)

__all__ = [
    "set_llm_digest",
    "attach_frontend_digest",
    "apply_dual_layer",
]
