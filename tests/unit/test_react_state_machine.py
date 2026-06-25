"""ReAct 状态机单测 — 覆盖 run_mode / todo_write / final_answer / planning_guard 核心路径。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ark_agentic.core.types import AgentMessage, ToolCall
from ark_agentic.core.runtime.callbacks import CallbackResult, HookAction


# ────────────────────────────────────────────────────────────
# 1. _parse_bool
# ────────────────────────────────────────────────────────────

class TestParseBool:
    def test_bool_true(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool(True) is True

    def test_bool_false(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool(False) is False

    def test_none(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool(None) is False

    def test_string_true(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool("true") is True
        assert _parse_bool("True") is True
        assert _parse_bool("1") is True

    def test_string_false(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool("false") is False
        assert _parse_bool("False") is False
        assert _parse_bool("0") is False
        assert _parse_bool("") is False

    def test_int(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool(1) is True
        assert _parse_bool(0) is False

    def test_unknown_string_returns_false(self) -> None:
        from egis_agent_plugins.core.tools.react.final_answer import _parse_bool
        assert _parse_bool("maybe") is False
        assert _parse_bool("yes") is False


# ────────────────────────────────────────────────────────────
# 2. normalize_run_mode
# ────────────────────────────────────────────────────────────

class TestNormalizeRunMode:
    def test_flash(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import normalize_run_mode, RunMode
        assert normalize_run_mode("flash") == RunMode.FLASH
        assert normalize_run_mode("fast") == RunMode.FLASH
        assert normalize_run_mode("quick") == RunMode.FLASH

    def test_pro(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import normalize_run_mode, RunMode
        assert normalize_run_mode("pro") == RunMode.PRO
        assert normalize_run_mode("professional") == RunMode.PRO
        assert normalize_run_mode("plan") == RunMode.PRO
        assert normalize_run_mode("planning") == RunMode.PRO

    def test_empty_returns_default(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import normalize_run_mode, RunMode
        assert normalize_run_mode("") == RunMode.PRO
        assert normalize_run_mode(None) == RunMode.PRO

    def test_unknown_returns_default(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import normalize_run_mode, RunMode
        assert normalize_run_mode("turbo") == RunMode.PRO


# ────────────────────────────────────────────────────────────
# 3. filter_tool_schemas_for_run_mode
# ────────────────────────────────────────────────────────────

def _make_schema(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name}}


class TestFilterToolSchemasForRunMode:
    def test_flash_removes_todo_write(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import filter_tool_schemas_for_run_mode
        schemas = [_make_schema("todo_write"), _make_schema("final_answer"), _make_schema("search")]
        result = filter_tool_schemas_for_run_mode(
            schemas, {"user:run_mode": "flash"}, agent_id="test"
        )
        names = [s["function"]["name"] for s in result]
        assert "todo_write" not in names
        assert "final_answer" in names
        assert "search" in names

    def test_pro_keeps_all(self) -> None:
        from egis_agent_plugins.core.tools.react._services.run_mode import filter_tool_schemas_for_run_mode
        schemas = [_make_schema("todo_write"), _make_schema("final_answer")]
        result = filter_tool_schemas_for_run_mode(
            schemas, {"user:run_mode": "pro"}, agent_id="test"
        )
        names = [s["function"]["name"] for s in result]
        assert "todo_write" in names
        assert "final_answer" in names


# ────────────────────────────────────────────────────────────
# 4. todo_write: JSON 字符串 steps 解析 + 多 in_progress 兜底
# ────────────────────────────────────────────────────────────

class TestTodoWriteEdgeCases:
    @pytest.fixture
    def tool(self):
        from egis_agent_plugins.core.tools.react.todo_write import TodoWriteTool
        return TodoWriteTool()

    def _make_tool_call(self, task: str, steps: Any) -> ToolCall:
        return ToolCall.create("todo_write", {"task": task, "steps": steps})

    @pytest.mark.asyncio
    async def test_json_string_steps(self, tool) -> None:
        """steps 为 JSON 字符串时应正确解析为列表。"""
        steps_json = json.dumps([
            {"id": "s1", "description": "第一步", "status": "pending"},
            {"id": "s2", "description": "第二步", "status": "pending"},
        ])
        tc = self._make_tool_call("测试任务", steps_json)
        result = await tool.execute(tc)
        assert not result.is_error
        metadata = result.metadata or {}
        assert metadata.get("total_steps") == 2

    @pytest.mark.asyncio
    async def test_multi_in_progress_downgrade(self, tool) -> None:
        """多个 in_progress 时只保留第一个，其余降级为 pending。"""
        steps = [
            {"id": "s1", "description": "步骤1", "status": "in_progress"},
            {"id": "s2", "description": "步骤2", "status": "in_progress"},
            {"id": "s3", "description": "步骤3", "status": "pending"},
        ]
        tc = self._make_tool_call("多进行中测试", steps)
        result = await tool.execute(tc)
        assert not result.is_error

        # 检查 state_delta 中的 steps
        metadata = result.metadata or {}
        state_delta = metadata.get("state_delta", {})
        ps = state_delta.get("user:planning_state", {})
        result_steps = ps.get("steps", [])

        in_progress_count = sum(1 for s in result_steps if s["status"] == "in_progress")
        assert in_progress_count == 1, f"Expected 1 in_progress, got {in_progress_count}"

        # s1 保持 in_progress，s2 降级为 pending
        assert result_steps[0]["status"] == "in_progress"
        assert result_steps[1]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_single_in_progress_no_change(self, tool) -> None:
        """单个 in_progress 不应被修改。"""
        steps = [
            {"id": "s1", "description": "步骤1", "status": "completed"},
            {"id": "s2", "description": "步骤2", "status": "in_progress"},
            {"id": "s3", "description": "步骤3", "status": "pending"},
        ]
        tc = self._make_tool_call("单进行中测试", steps)
        result = await tool.execute(tc)
        metadata = result.metadata or {}
        state_delta = metadata.get("state_delta", {})
        ps = state_delta.get("user:planning_state", {})
        result_steps = ps.get("steps", [])

        in_progress_count = sum(1 for s in result_steps if s["status"] == "in_progress")
        assert in_progress_count == 1
        assert result_steps[1]["status"] == "in_progress"


# ────────────────────────────────────────────────────────────
# 5. final_answer: is_blocking 字符串解析
# ────────────────────────────────────────────────────────────

class TestFinalAnswerIsBlocking:
    @pytest.fixture
    def tool(self):
        from egis_agent_plugins.core.tools.react.final_answer import FinalAnswerTool
        return FinalAnswerTool()

    def _make_tool_call(self, answer: str, is_blocking: Any = False) -> ToolCall:
        return ToolCall.create("final_answer", {"answer": answer, "is_blocking": is_blocking})

    @pytest.mark.asyncio
    async def test_string_false_is_not_blocking(self, tool) -> None:
        """"false" 字符串应被解析为 False（不阻塞）。"""
        tc = self._make_tool_call("测试回答", "false")
        result = await tool.execute(tc)
        assert not result.is_error
        metadata = result.metadata or {}
        assert metadata.get("is_blocking") is False

    @pytest.mark.asyncio
    async def test_string_true_is_blocking(self, tool) -> None:
        tc = self._make_tool_call("需要确认", "true")
        result = await tool.execute(tc)
        metadata = result.metadata or {}
        assert metadata.get("is_blocking") is True

    @pytest.mark.asyncio
    async def test_bool_false(self, tool) -> None:
        tc = self._make_tool_call("正常回答", False)
        result = await tool.execute(tc)
        metadata = result.metadata or {}
        assert metadata.get("is_blocking") is False


# ────────────────────────────────────────────────────────────
# 6. planning_guard: prepend / pass-through 规则
# ────────────────────────────────────────────────────────────

class TestPlanningGuard:
    @pytest.fixture
    def guard_callback(self):
        """获取 planning_guard 的 after_model 回调。"""
        from egis_agent_plugins.core.tools.react._services.planning_guard import build_planning_callbacks
        cbs = build_planning_callbacks()
        return cbs.after_model[0]

    def _make_ctx(self, state: dict | None = None, user_input: str = "测试请求") -> MagicMock:
        ctx = MagicMock()
        session = MagicMock()
        session.state = state or {}
        ctx.session = session
        ctx.user_input = user_input
        return ctx

    @pytest.mark.asyncio
    async def test_passthrough_when_first_is_todo_write(self, guard_callback) -> None:
        """LLM 已以 todo_write 开头 → guard 放行不干预。"""
        ctx = self._make_ctx()
        response = AgentMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall.create("todo_write", {"task": "t", "steps": []}),
                ToolCall.create("search", {"query": "q"}),
            ],
        )
        result = await guard_callback(ctx, turn=1, response=response)
        assert result is None  # None = 不干预

    @pytest.mark.asyncio
    async def test_prepend_when_first_is_not_todo_write(self, guard_callback) -> None:
        """LLM 未以 todo_write 开头 → guard 前置注入。"""
        ctx = self._make_ctx()
        response = AgentMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall.create("search", {"query": "q"})],
        )
        result = await guard_callback(ctx, turn=1, response=response)
        assert result is not None
        assert result.action == HookAction.PASS
        # 第一个变成 todo_write，原 search 保留在第二个
        names = [tc.name for tc in response.tool_calls]
        assert names[0] == "todo_write"
        assert names[1] == "search"

    @pytest.mark.asyncio
    async def test_single_final_answer_passthrough(self, guard_callback) -> None:
        """单独 final_answer → 放行。"""
        ctx = self._make_ctx()
        response = AgentMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall.create("final_answer", {"answer": "ok"})],
        )
        result = await guard_callback(ctx, turn=1, response=response)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_tool_calls_bootstrap(self, guard_callback) -> None:
        """空 tool_calls → bootstrap 注入 todo_write。"""
        ctx = self._make_ctx(user_input="帮我做个 PPT")
        response = AgentMessage(role="assistant", content="好的", tool_calls=[])
        result = await guard_callback(ctx, turn=1, response=response)
        assert result is not None
        assert result.action == HookAction.PASS
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "todo_write"
