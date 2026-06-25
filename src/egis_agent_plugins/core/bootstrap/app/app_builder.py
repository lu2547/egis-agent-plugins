"""app_builder — 框架标准生命周期 API。

对外暴露四个函数：

- ``create(lifespan=...)`` — 初始化框架，返回 FastAPI 实例
- ``start()``              — 启动全部组件（按注册顺序）
- ``stop()``               — 关闭全部组件（逆序 pop）
- ``serve()``              — 启动 uvicorn HTTP 服务

业务项目 app.py 只需：

.. code-block:: python

    from egis_agent_plugins import app_builder
    app = app_builder.create(lifespan=my_lifespan)
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
import sys
from pathlib import Path
from typing import Any, AsyncContextManager, Callable

logger = logging.getLogger(__name__)

# ── 模块级状态 ─────────────────────────────────────────────────────────
_bootstrap: Any = None          # ark Bootstrap 实例（create 后赋值）
_app: Any = None                # FastAPI 实例（start 时挂载 app.state.ctx）
_app_module: str = ""           # 调用方模块名（serve 时用于 uvicorn 定位 app）


# ══════════════════════════════════════════════════════════════════════
# create()
# ══════════════════════════════════════════════════════════════════════

def create(
    *,
    lifespan: AsyncContextManager | None = None,
    agents_root: Path | str | None = None,
    plugins_dir: Path | str | None = None,
) -> Any:  # -> FastAPI（延迟 import 避免循环）
    """初始化框架并返回 FastAPI 应用对象。

    内部依次完成：
      1. 加载环境变量（业务 .env > 框架 .env，真实 env 最高优先）
      2. 组合型默认值（DB_CONNECTION_STR 等）
      3. 日志初始化
      4. 构建 Plugin 列表（API -> Download -> Studio -> plugins/ 自定义）
      5. 创建 Bootstrap + FastAPI + 安装路由 + /api/chat 兼容中间件

    Args:
        lifespan:    FastAPI lifespan 上下文管理器（控制 start/stop 调用时机）。
        agents_root: agent 自动发现根目录。优先 AGENTS_ROOT 环境变量，
                     再退到调用方文件所在目录的 ``agents/`` 子目录。
        plugins_dir: 业务自定义 Plugin 目录。默认取 ``agents_root`` 的
                     兄弟目录 ``../plugins``。
    """
    global _bootstrap, _app, _app_module

    # ── 记录调用方模块名（serve 需要） ──
    _app_module = _resolve_caller_module()

    # ── 1. 环境变量 ──
    from egis_agent_plugins.core.bootstrap.app.env_builder import apply_computed_defaults, load_env

    load_env()
    apply_computed_defaults()

    # ── 2. 日志 ──
    from egis_agent_plugins.core.bootstrap.app.log_builder import init_logging

    init_logging()

    # ── 3. 解析 agents_root ──
    resolved_agents_root = _resolve_agents_root(agents_root)

    # ── 4. 解析 plugins_dir ──
    resolved_plugins_dir = _resolve_plugins_dir(plugins_dir, resolved_agents_root)

    # ── 5. 构建 Plugin 列表 ──
    from ark_agentic.plugins.api.plugin import APIPlugin

    from egis_agent_plugins.core.plugins import EgisDownloadPlugin, EgisStudioPlugin

    components: list = [
        APIPlugin(),                # ark 原生：CORS + /chat + /health
        EgisDownloadPlugin(),       # 始终加载
        EgisStudioPlugin(),         # is_enabled() 内部读 ENABLE_EGIS_STUDIO
    ]

    # 扫描业务 plugins/ 目录
    if resolved_plugins_dir and resolved_plugins_dir.is_dir():
        user_plugins = _discover_plugins(resolved_plugins_dir)
        components.extend(user_plugins)
        logger.info(
            "Discovered %d user plugin(s) from %s",
            len(user_plugins), resolved_plugins_dir,
        )

    # ── 6. Bootstrap ──
    from ark_agentic.core.protocol.bootstrap import Bootstrap
    from ark_agentic.core.storage.datasource import Datasource

    _bootstrap = Bootstrap(
        components,
        datasource=Datasource.from_env(),
        agents_root=None,  # _resolve_agents_root 已写入 AGENTS_ROOT env，ark 走 env 分支
    )

    # ── 7. FastAPI ──
    from fastapi import FastAPI

    app_kwargs: dict[str, Any] = {
        "title": os.getenv("APP_TITLE", "egis-agents API"),
        "version": "0.1.0",
    }
    if lifespan is not None:
        app_kwargs["lifespan"] = lifespan

    app = FastAPI(**app_kwargs)
    _app = app

    # ── 8. 安装路由 ──
    _bootstrap.install_routes(app)

    # ── 9. /api/chat -> /chat 兼容中间件 ──
    @app.middleware("http")
    async def _rewrite_api_chat(request: Any, call_next: Any) -> Any:
        if request.scope["path"] == "/api/chat":
            request.scope["path"] = "/chat"
        return await call_next(request)

    logger.info("app_builder.create() complete (agents_root=%s)", resolved_agents_root)
    return app


# ══════════════════════════════════════════════════════════════════════
# start() / stop()
# ══════════════════════════════════════════════════════════════════════

async def start() -> None:
    """启动全部组件（按注册顺序）。在 lifespan yield 前显式调用。"""
    from ark_agentic.core.protocol.app_context import AppContext

    assert _bootstrap is not None, "call app_builder.create() first"
    assert _app is not None, "call app_builder.create() first"
    ctx = AppContext()
    await _bootstrap.start(ctx)
    _app.state.ctx = ctx


async def stop() -> None:
    """关闭全部组件（逆序 pop）+ 清理 ServiceRegistry。在 lifespan finally 中显式调用。"""
    from egis_agent_plugins.core.flows.rag.clients import ServiceRegistry

    await ServiceRegistry.close_all()
    if _bootstrap is not None:
        await _bootstrap.stop()
    if _app is not None and hasattr(_app.state, "ctx"):
        delattr(_app.state, "ctx")


# ══════════════════════════════════════════════════════════════════════
# serve()
# ══════════════════════════════════════════════════════════════════════

def serve() -> None:
    """从 .env 读取 API_HOST / API_PORT / LOG_LEVEL，启动 uvicorn。"""
    import uvicorn

    module = _app_module or "egis_gpt_agents.app"
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "38081"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    logger.info("Starting uvicorn: %s:app on %s:%s", module, host, port)
    uvicorn.run(
        f"{module}:app",
        host=host,
        port=port,
        reload=False,
        log_level=log_level,
    )


# ══════════════════════════════════════════════════════════════════════
# 内部辅助
# ══════════════════════════════════════════════════════════════════════

def _resolve_caller_module() -> str:
    """从调用栈推断调用方模块名（跳过 app_builder 自身）。"""
    for frame_info in inspect.stack()[1:]:
        fname = frame_info.filename
        if "app_builder" in fname:
            continue
        # 从文件路径推断模块名
        mod = sys.modules.get(frame_info.frame.f_globals.get("__name__", ""))
        if mod and hasattr(mod, "__name__"):
            return mod.__name__
        # fallback: 从文件名推断
        name = frame_info.frame.f_globals.get("__name__", "")
        if name and name != "__main__":
            return name
    return ""


def _resolve_agents_root(explicit: Path | str | None) -> Path | None:
    """三层解析 agents_root：显式参数 > AGENTS_ROOT env > 调用方约定。

    逻辑与 ark ``Bootstrap._resolve_agents_root`` 完全一致，但必须在 egis 层预解析：
    因为 ``Bootstrap(...)`` 在 app_builder 内部调用，ark 的栈回溯会停在 app_builder.py
    而非业务 app.py，找不到 ``agents/`` 目录。预解析后写入 AGENTS_ROOT env，
    再传 agents_root=None 让 ark 走 env 分支，避免重复解析。
    """
    if explicit is not None:
        return Path(explicit).resolve()

    env_val = os.getenv("AGENTS_ROOT")
    if env_val:
        return Path(env_val).resolve()

    # 约定：调用方文件目录下的 agents/ 子目录
    for frame_info in inspect.stack()[1:]:
        fname = frame_info.filename
        if "app_builder" in fname:
            continue
        candidate = Path(fname).resolve().parent / "agents"
        if candidate.is_dir():
            os.environ["AGENTS_ROOT"] = str(candidate)
            logger.info("Resolved agents_root by convention: %s", candidate)
            return candidate

    return None


def _resolve_plugins_dir(
    explicit: Path | str | None,
    agents_root: Path | None,
) -> Path | None:
    """解析 plugins_dir：显式参数 > agents_root 的兄弟目录 ../plugins。"""
    if explicit is not None:
        return Path(explicit).resolve()

    if agents_root:
        candidate = agents_root.parent / "plugins"
        if candidate.is_dir():
            return candidate

    return None


def _discover_plugins(plugins_dir: Path) -> list:
    """扫描 plugins_dir 下的 Python 包/模块，收集所有 Lifecycle 子类实例。"""
    from ark_agentic.core.protocol.lifecycle import BaseLifecycle

    plugins_dir = plugins_dir.resolve()
    if not plugins_dir.is_dir():
        return []

    # 确保父目录在 sys.path 中（使 import 可用）
    parent = str(plugins_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    pkg_name = plugins_dir.name
    discovered: list = []

    try:
        pkg = importlib.import_module(pkg_name)
    except Exception:
        logger.warning(
            "Plugin package %r failed to import; no user plugins discovered",
            pkg_name, exc_info=True,
        )
        return []

    for _finder, name, _ispkg in pkgutil.walk_packages(
        pkg.__path__, prefix=f"{pkg_name}.",
    ):
        if name.split(".")[-1].startswith("_"):
            continue
        try:
            mod = importlib.import_module(name)
        except Exception:
            logger.warning(
                "Module %r failed to import during plugin discovery; skipped",
                name, exc_info=True,
            )
            continue

        seen: set = set()
        for obj in vars(mod).values():
            if not isinstance(obj, type):
                continue
            if not issubclass(obj, BaseLifecycle) or obj is BaseLifecycle:
                continue
            if obj in seen:
                continue
            # 排除 re-export（只取在本模块中定义的类）
            if not obj.__module__.startswith(pkg_name):
                continue
            # 排除 BasePlugin 自身
            from ark_agentic.core.protocol.plugin import BasePlugin
            if obj is BasePlugin:
                continue

            seen.add(obj)
            try:
                instance = obj()
                discovered.append(instance)
                logger.info("Discovered user plugin: %s", obj.__name__)
            except Exception:
                logger.warning(
                    "Plugin %s failed to instantiate; skipped",
                    obj.__name__, exc_info=True,
                )

    return discovered
