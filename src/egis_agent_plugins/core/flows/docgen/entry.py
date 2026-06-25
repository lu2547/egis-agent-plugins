"""DocgenEntryFlow — 统一入口流程（Step1 选方式 → Step2 选方案 → 路由）

前端由 DocgenEntryCard.vue 组件渲染（frontend_digest 通道），
本模块只负责状态机 + 路由逻辑。

状态机:
  start(None → idle)           创建实例
  show_entry(idle → idle)      展示入口卡片 (frontend_digest)
  select_tender(idle → routed)          标书 → 建项目 → 路由
  select_pension_intro(idle → routed)   养老险 → 建项目 → 路由
  select_investment_report(idle → routed) 投资报告 → 建项目 → 路由
  select_word(idle → routed)            AI Word → 路由
  select_ppt(idle → routed)             AI PPT → 路由
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from ark_agentic.core.workflow.engine import Workflow
from ark_agentic.core.workflow.protocol import (
    EffectOutput, InstanceCtx, Transition,
)

logger = logging.getLogger(__name__)

_ENTRY_STATE_FILE = "docgen_entry_state.json"


def _state_event(action: str, **data: Any) -> dict[str, Any]:
    return {"action": action, **data}


def _get_user_request(ictx: InstanceCtx) -> str:
    """Read the current user request from args or runtime context."""
    for key in ("user_request", "query", "message", "input"):
        value = ictx.args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("temp:user_input", "user_input", "current_user_input"):
        value = ictx.session_ctx.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _infer_mode_from_request(user_request: str) -> str | None:
    """Deterministically map explicit entry wording to a mode."""
    text = user_request.strip().lower()
    if not text:
        return None
    if "标化" in text or "标准模板" in text or "标准化" in text:
        return "standard"
    if "ai制作" in text or "ai 制作" in text or "智能制作" in text:
        return "ai"
    return None


def _initial_entry_state(
    *,
    project_id: str,
    project_path: str,
    user_request: str,
) -> dict[str, Any]:
    mode = _infer_mode_from_request(user_request)
    return {
        "schema_version": 1,
        "workflow": "docgen_entry",
        "project_id": project_id,
        "project_path": project_path,
        "user_request": user_request,
        "mode": mode,
        "doc_type": None,
        "selected_template": None,
        "current_step": "template_selection" if mode else "mode_selection",
        "next_action": "select_template" if mode == "standard" else "select_mode",
        "status": "started",
        "history": [_state_event("start", mode=mode, user_request=user_request)],
    }


def _load_entry_state(project_path: str) -> dict[str, Any]:
    if not project_path:
        return {}
    path = Path(project_path) / "templates" / _ENTRY_STATE_FILE
    if not path.exists():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[DocgenEntry] failed to load entry state: %s", exc)
        return {}


def _save_entry_state(project_path: str, state: dict[str, Any]) -> None:
    from egis_agent_plugins.core.tools.docgen import service

    service.save_json_artifact(
        data=state,
        filename=_ENTRY_STATE_FILE,
        project_path=Path(project_path),
        subdir="templates",
    )


def _append_history(state: dict[str, Any], action: str, **data: Any) -> None:
    history = state.setdefault("history", [])
    if isinstance(history, list):
        history.append(_state_event(action, **data))
        del history[:-20]


# ── Effects ──────────────────────────────────────────────────────────


async def _ef_start(ictx: InstanceCtx) -> EffectOutput | None:
    """创建入口 flow 实例，初始化路由项目和过程状态 JSON。"""
    from egis_agent_plugins.core.tools.docgen import service

    user_request = _get_user_request(ictx)
    project_id = service.get_state_value(ictx.session_ctx, "docgen_state.project_id", "")
    project_path = service.get_state_value(ictx.session_ctx, "docgen_state.project_path", "")

    store = service.ProjectStore()
    manifest = store.get_project(Path(project_path)) if project_path else None
    if manifest is None:
        project_path_obj, manifest = store.init_project(
            "docgen-entry",
            workflow="docgen.entry",
            document_kind="mixed",
            session_id=str(ictx.session_ctx.get("session_id", "")),
            user_id=str(ictx.session_ctx.get("user:id", "") or ictx.session_ctx.get("user_id", "")),
        )
        project_path = str(project_path_obj)
        project_id = manifest.project_id
    elif not project_id:
        project_id = manifest.project_id

    state = _load_entry_state(project_path)
    if not state:
        state = _initial_entry_state(
            project_id=project_id,
            project_path=project_path,
            user_request=user_request,
        )
    else:
        if user_request:
            state["user_request"] = user_request
            inferred_mode = _infer_mode_from_request(user_request)
            if inferred_mode:
                state["mode"] = inferred_mode
                state["current_step"] = "template_selection"
                state["next_action"] = "select_template"
            _append_history(state, "start", mode=state.get("mode"), user_request=user_request)

    _save_entry_state(project_path, state)

    ictx.instance_data["step"] = state.get("current_step")
    ictx.instance_data["mode"] = state.get("mode")
    ictx.instance_data["doc_type"] = state.get("doc_type")
    ictx.instance_data["project_id"] = project_id
    ictx.instance_data["project_path"] = project_path

    return EffectOutput(
        message="DocGen 入口流程已启动。",
        extras={
            "docgen_state.project_id": project_id,
            "docgen_state.project_path": project_path,
            "docgen_state.entry_state_path": f"templates/{_ENTRY_STATE_FILE}",
            "docgen_state.entry_mode": state.get("mode"),
            "docgen_state.entry_step": state.get("current_step"),
        },
    )


async def _ef_show_entry(ictx: InstanceCtx) -> EffectOutput | None:
    """准备展示入口卡片（frontend_digest 由 DocgenEntryTool 发送）。"""
    project_path = str(ictx.instance_data.get("project_path") or "")
    state = _load_entry_state(project_path)
    mode = state.get("mode") or ictx.args.get("mode") or ictx.args.get("initial_mode")
    if mode in ("standard", "ai"):
        state["mode"] = mode
        state["current_step"] = "template_selection"
        state["next_action"] = "select_template"
        _append_history(state, "show_entry", mode=mode)
        if project_path:
            _save_entry_state(project_path, state)
    ictx.instance_data["step"] = "entry"
    ictx.instance_data["mode"] = mode
    return EffectOutput(
        message="入口卡片已展示，等待用户选择。",
        extras={
            "docgen_state.entry_mode": mode,
            "docgen_state.entry_step": state.get("current_step", "mode_selection"),
        },
    )


async def _ef_select_tender(ictx: InstanceCtx) -> EffectOutput | None:
    """选择标书 — 设模式+类型，创建项目，路由到 tender flow。"""
    ictx.instance_data["mode"] = "standard"
    ictx.instance_data["doc_type"] = "tender_word"
    return await _create_project(ictx, "tender_word")


async def _ef_select_pension_intro(ictx: InstanceCtx) -> EffectOutput | None:
    """选择养老险优势介绍 — 设模式+类型，创建项目，路由。"""
    ictx.instance_data["mode"] = "standard"
    ictx.instance_data["doc_type"] = "pension_intro_word"
    return await _create_project(ictx, "pension_intro_word")


async def _ef_select_investment_report(ictx: InstanceCtx) -> EffectOutput | None:
    """选择投资报告 — 设模式+类型，创建项目，路由。"""
    ictx.instance_data["mode"] = "standard"
    ictx.instance_data["doc_type"] = "investment_report_word"
    return await _create_project(ictx, "investment_report_word")


async def _ef_select_word(ictx: InstanceCtx) -> EffectOutput | None:
    """选择 AI Word 制作。"""
    ictx.instance_data["mode"] = "ai"
    ictx.instance_data["doc_type"] = "ai_word"
    return EffectOutput(
        message="用户选择了 AI Word 制作。请按 wordmaster 技能执行。",
        extras={
            "docgen_state.mode": "ai",
            "docgen_state.doc_type": "ai_word",
        },
    )


async def _ef_select_ppt(ictx: InstanceCtx) -> EffectOutput | None:
    """选择 AI PPT 制作。"""
    ictx.instance_data["mode"] = "ai"
    ictx.instance_data["doc_type"] = "ai_ppt"
    return EffectOutput(
        message="用户选择了 AI PPT 制作。请按 pptmaster 技能执行。",
        extras={
            "docgen_state.mode": "ai",
            "docgen_state.doc_type": "ai_ppt",
        },
    )


async def _create_project(
    ictx: InstanceCtx, doc_type: str,
) -> EffectOutput:
    """创建项目并返回路由结果。"""
    from egis_agent_plugins.core.tools.docgen._project_model import ProjectStore

    workflow_map = {
        "tender_word": "docgen.word_standard.tender",
        "pension_intro_word": "docgen.word_standard.pension_intro",
        "investment_report_word": "docgen.word_standard.investment_report",
    }
    workflow = workflow_map.get(doc_type, f"docgen.word_standard.{doc_type}")
    project_kind = doc_type.replace("_word", "").replace("_", "-") + "_doc"

    store = ProjectStore()
    project_path, manifest = store.init_project(
        project_kind,
        workflow=workflow,
        document_kind="word",
        session_id=str(ictx.session_ctx.get("session_id", "")),
        user_id=str(ictx.session_ctx.get("user_id", "")),
    )

    ictx.instance_data["project_id"] = manifest.project_id
    ictx.instance_data["project_path"] = str(project_path)

    entry_state = _load_entry_state(str(project_path)) or {
        "schema_version": 1,
        "workflow": "docgen_entry",
        "project_id": manifest.project_id,
        "project_path": str(project_path),
        "history": [],
    }
    entry_state.update({
        "mode": "standard",
        "doc_type": doc_type,
        "selected_template": doc_type,
        "current_step": "routed",
        "next_action": workflow,
        "status": "routed",
    })
    _append_history(entry_state, "select_template", doc_type=doc_type, workflow=workflow)
    _save_entry_state(str(project_path), entry_state)

    return EffectOutput(
        message=(
            f"{doc_type} 项目已创建 (project_id={manifest.project_id})。"
            f"请调用对应材料的 flow 启动制作流程。"
        ),
        extras={
            "docgen_state.mode": "standard",
            "docgen_state.doc_type": doc_type,
            "docgen_state.project_id": manifest.project_id,
            "docgen_state.project_path": str(project_path),
            "docgen_state.entry_state_path": f"templates/{_ENTRY_STATE_FILE}",
        },
    )


# ── Workflow ──────────────────────────────────────────────────────────


class DocgenEntryFlow(Workflow):
    """DocGen 统一入口流程 — Step1 选方式 → Step2 选方案 → 路由"""

    flow_id: ClassVar[str] = "docgen_entry"
    aliases: ClassVar[dict[str, str]] = {"default": "default"}

    states: ClassVar[tuple[str, ...]] = (
        "idle",
        "routed",
    )

    initial_state: ClassVar[str] = "idle"

    final_states: ClassVar[tuple[str, ...]] = (
        "routed",
    )

    transitions: ClassVar[tuple[Transition, ...]] = (
        Transition("start", None, "idle", effect=_ef_start),
        Transition("show_entry", "idle", "idle", effect=_ef_show_entry),
        # 标化
        Transition("select_tender", "idle", "routed", effect=_ef_select_tender),
        Transition("select_pension_intro", "idle", "routed", effect=_ef_select_pension_intro),
        Transition("select_investment_report", "idle", "routed", effect=_ef_select_investment_report),
        # AI
        Transition("select_word", "idle", "routed", effect=_ef_select_word),
        Transition("select_ppt", "idle", "routed", effect=_ef_select_ppt),
    )
