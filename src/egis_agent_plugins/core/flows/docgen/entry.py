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
    ANY_STATE, KEEP_STATE, EffectOutput, InstanceCtx, Transition,
)
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SelectModeArgs(BaseModel):
    mode: str = Field(description="制作方式: standard(标化材料制作) / ai(AI 制作)")


class SelectTemplateArgs(BaseModel):
    template_id: str = Field(
        description="模板 ID，如 pension_intro / investment_report / tender / ai_word / ai_ppt",
    )


_TEMPLATE_DOC_TYPE: dict[str, str] = {
    "tender": "tender_word",
    "pension": "pension_intro_word",
    "pension_intro": "pension_intro_word",
    "investment": "investment_report_word",
    "investment_report": "investment_report_word",
    "word": "ai_word",
    "ai_word": "ai_word",
    "ppt": "ai_ppt",
    "ai_ppt": "ai_ppt",
}


_DOC_TYPE_TEMPLATE: dict[str, str] = {
    "tender_word": "tender",
    "pension_intro_word": "pension_intro",
    "investment_report_word": "investment_report",
    "ai_word": "ai_word",
    "ai_ppt": "ai_ppt",
}


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


def _doc_type_from_template(template_id: str) -> str:
    return _TEMPLATE_DOC_TYPE.get(template_id.strip(), "")


def _entry_state_delta(
    ictx: InstanceCtx,
    patch: dict[str, Any],
) -> dict[str, Any]:
    from egis_agent_plugins.core.tools.docgen import service

    next_state = service.patch_docgen_state(ictx.session_ctx, patch)
    return service.docgen_state_delta(next_state)


# ── Effects ──────────────────────────────────────────────────────────


async def _ef_start(ictx: InstanceCtx) -> EffectOutput | None:
    """创建或恢复入口 flow 实例，运行态只写 ark session state。"""
    from egis_agent_plugins.core.tools.docgen import service

    user_request = _get_user_request(ictx)
    docgen_state = service.get_docgen_state(ictx.session_ctx)
    project_id = str(docgen_state.get("project_id") or "")
    project_path = str(docgen_state.get("project_path") or "")
    mode = str(docgen_state.get("mode") or "") or _infer_mode_from_request(user_request)

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

    stage = "template_selection" if mode == "standard" else "mode_selection"
    awaiting = "template" if mode == "standard" else "mode"

    ictx.instance_data["step"] = stage
    ictx.instance_data["mode"] = mode or None
    ictx.instance_data["doc_type"] = docgen_state.get("doc_type")
    ictx.instance_data["project_id"] = project_id
    ictx.instance_data["project_path"] = project_path

    state_delta = _entry_state_delta(
        ictx,
        {
            "project_id": project_id,
            "project_path": project_path,
            "status": "active",
            "mode": mode or None,
            "stage": stage,
            "awaiting": awaiting,
            "input": {
                "original_request": docgen_state.get("input", {}).get("original_request") or user_request,
                "last_user_message": user_request,
            },
            "selection": {"mode": mode or None},
            "flows": {
                "entry": {
                    "stage": stage,
                    "awaiting": awaiting,
                },
            },
        },
    )

    return EffectOutput(
        message="DocGen 入口流程已启动。",
        extras={
            **state_delta,
            "docgen_state.entry_mode": mode or None,
            "docgen_state.entry_step": stage,
        },
    )


async def _ef_show_entry(ictx: InstanceCtx) -> EffectOutput | None:
    """准备展示入口卡片（frontend_digest 由 DocgenEntryTool 发送）。"""
    from egis_agent_plugins.core.tools.docgen import service

    docgen_state = service.get_docgen_state(ictx.session_ctx)
    mode = (
        ictx.args.get("mode")
        or ictx.args.get("initial_mode")
        or ictx.instance_data.get("mode")
        or docgen_state.get("mode")
    )
    if mode in ("standard", "ai"):
        stage = "template_selection"
        awaiting = "template"
    else:
        mode = None
        stage = "mode_selection"
        awaiting = "mode"

    ictx.instance_data["step"] = "entry"
    ictx.instance_data["mode"] = mode
    state_delta = _entry_state_delta(
        ictx,
        {
            "mode": mode,
            "stage": stage,
            "awaiting": awaiting,
            "selection": {"mode": mode},
            "flows": {
                "entry": {
                    "stage": stage,
                    "awaiting": awaiting,
                },
            },
            "ui": {
                "blocking": True,
                "component": "docgen_entry",
                "payload": {"initial_mode": mode} if mode else {},
            },
        },
    )
    return EffectOutput(
        message="入口卡片已展示，等待用户选择。",
        extras={
            **state_delta,
            "docgen_state.entry_mode": mode,
            "docgen_state.entry_step": stage,
        },
    )


async def _ef_start_show_entry(ictx: InstanceCtx) -> EffectOutput | None:
    """自启动并展示入口，避免 start/show_entry 并行时丢状态。"""
    from egis_agent_plugins.core.tools.docgen import service

    start_result = await _ef_start(ictx)
    state = dict((start_result.extras or {}).get("docgen_state") or {})
    mode = state.get("mode")
    if mode in ("standard", "ai"):
        stage = "template_selection"
        awaiting = "template"
    else:
        mode = None
        stage = "mode_selection"
        awaiting = "mode"
    state = service.merge_dict(
        state,
        {
            "stage": stage,
            "awaiting": awaiting,
            "selection": {"mode": mode},
            "flows": {
                "entry": {
                    "stage": stage,
                    "awaiting": awaiting,
                },
            },
            "ui": {
                "blocking": True,
                "component": "docgen_entry",
                "payload": {"initial_mode": mode} if mode else {},
            },
        },
    )
    return EffectOutput(
        message="入口卡片已展示，等待用户选择。",
        extras={
            **service.docgen_state_delta(state),
            "docgen_state.entry_mode": mode,
            "docgen_state.entry_step": stage,
        },
    )


async def _ef_select_mode(ictx: InstanceCtx) -> EffectOutput | None:
    """选择制作方式，不离开入口 flow。"""
    mode = str(ictx.args.get("mode") or "").strip()
    if mode not in {"standard", "ai"}:
        return EffectOutput(message="无效制作方式，请选择 standard 或 ai。")

    stage = "template_selection"
    ictx.instance_data["mode"] = mode
    ictx.instance_data["step"] = stage
    state_delta = _entry_state_delta(
        ictx,
        {
            "mode": mode,
            "stage": stage,
            "awaiting": "template",
            "selection": {"mode": mode},
            "flows": {
                "entry": {
                    "stage": stage,
                    "awaiting": "template",
                },
            },
        },
    )
    return EffectOutput(
        message=f"已选择制作方式: {mode}",
        extras={
            **state_delta,
            "docgen_state.entry_mode": mode,
            "docgen_state.entry_step": stage,
        },
    )


async def _ef_select_template(ictx: InstanceCtx) -> EffectOutput | None:
    """通用模板选择入口，供不同 agent 复用 entry flow。"""
    template_id = str(ictx.args.get("template_id") or "").strip()
    doc_type = _doc_type_from_template(template_id)
    if not doc_type:
        return EffectOutput(message=f"未知模板: {template_id}")
    if doc_type == "ai_word":
        return await _ef_select_word(ictx)
    if doc_type == "ai_ppt":
        return await _ef_select_ppt(ictx)
    return await _create_project(ictx, doc_type)


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
    state_delta = _entry_state_delta(
        ictx,
        {
            "mode": "ai",
            "template_id": "ai_word",
            "active_flow": None,
            "stage": "ai_word_selected",
            "awaiting": None,
            "selection": {
                "mode": "ai",
                "template_id": "ai_word",
                "format": "word",
            },
            "flows": {
                "entry": {
                    "stage": "template_selected",
                    "awaiting": None,
                },
            },
        },
    )
    return EffectOutput(
        message="用户选择了 AI Word 制作。请按 wordmaster 技能执行。",
        extras={
            **state_delta,
            "docgen_state.mode": "ai",
            "docgen_state.doc_type": "ai_word",
        },
    )


async def _ef_select_ppt(ictx: InstanceCtx) -> EffectOutput | None:
    """选择 AI PPT 制作。"""
    ictx.instance_data["mode"] = "ai"
    ictx.instance_data["doc_type"] = "ai_ppt"
    state_delta = _entry_state_delta(
        ictx,
        {
            "mode": "ai",
            "template_id": "ai_ppt",
            "active_flow": None,
            "stage": "ai_ppt_selected",
            "awaiting": None,
            "selection": {
                "mode": "ai",
                "template_id": "ai_ppt",
                "format": "ppt",
            },
            "flows": {
                "entry": {
                    "stage": "template_selected",
                    "awaiting": None,
                },
            },
        },
    )
    return EffectOutput(
        message="用户选择了 AI PPT 制作。请按 pptmaster 技能执行。",
        extras={
            **state_delta,
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

    template_id = _DOC_TYPE_TEMPLATE.get(doc_type, doc_type)
    state_delta = _entry_state_delta(
        ictx,
        {
            "project_id": manifest.project_id,
            "project_path": str(project_path),
            "mode": "standard",
            "template_id": template_id,
            "active_flow": None,
            "stage": "template_selected",
            "awaiting": None,
            "selection": {
                "mode": "standard",
                "template_id": template_id,
                "format": "word",
            },
            "flows": {
                "entry": {
                    "stage": "template_selected",
                    "awaiting": None,
                },
            },
        },
    )

    return EffectOutput(
        message=(
            f"{doc_type} 项目已创建 (project_id={manifest.project_id})。"
            f"请调用对应材料的 flow 启动制作流程。"
        ),
        extras={
            **state_delta,
            "docgen_state.mode": "standard",
            "docgen_state.doc_type": doc_type,
            "docgen_state.project_id": manifest.project_id,
            "docgen_state.project_path": str(project_path),
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
        Transition("start", ANY_STATE, KEEP_STATE, effect=_ef_start),
        Transition("show_entry", None, "idle", effect=_ef_start_show_entry),
        Transition("show_entry", "idle", "idle", effect=_ef_show_entry),
        Transition("show_entry", ANY_STATE, KEEP_STATE, effect=_ef_show_entry),
        Transition("select_mode", None, "idle",
                   effect=_ef_select_mode, args_schema=SelectModeArgs),
        Transition("select_mode", "idle", "idle",
                   effect=_ef_select_mode, args_schema=SelectModeArgs),
        Transition("select_mode", ANY_STATE, KEEP_STATE,
                   effect=_ef_select_mode, args_schema=SelectModeArgs),
        Transition("select_template", None, "routed",
                   effect=_ef_select_template, args_schema=SelectTemplateArgs),
        Transition("select_template", "idle", "routed",
                   effect=_ef_select_template, args_schema=SelectTemplateArgs),
        Transition("select_template", ANY_STATE, "routed",
                   effect=_ef_select_template, args_schema=SelectTemplateArgs),
        # 标化
        Transition("select_tender", "idle", "routed", effect=_ef_select_tender),
        Transition("select_tender", ANY_STATE, "routed", effect=_ef_select_tender),
        Transition("select_pension_intro", "idle", "routed", effect=_ef_select_pension_intro),
        Transition("select_pension_intro", ANY_STATE, "routed", effect=_ef_select_pension_intro),
        Transition("select_investment_report", "idle", "routed", effect=_ef_select_investment_report),
        Transition("select_investment_report", ANY_STATE, "routed", effect=_ef_select_investment_report),
        # AI
        Transition("select_word", "idle", "routed", effect=_ef_select_word),
        Transition("select_word", ANY_STATE, "routed", effect=_ef_select_word),
        Transition("select_ppt", "idle", "routed", effect=_ef_select_ppt),
        Transition("select_ppt", ANY_STATE, "routed", effect=_ef_select_ppt),
    )
