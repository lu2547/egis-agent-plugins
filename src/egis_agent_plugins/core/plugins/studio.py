"""EgisStudioPlugin —— 将 egis-agent-studio 作为 ark 标准 Plugin 载入。

职责：
  - ``is_enabled()``：通过 ``ENABLE_EGIS_STUDIO`` 环境变量控制开关
  - ``install_routes(app)``：挂载 Studio 路由（/api/agents, /api/studio, /api/rag）+ 前端静态资源
  - ``start(ctx)``：建立 RAG asyncpg 连接池
  - ``stop()``：释放连接池

前置条件：
  - ``egis-agent-studio`` 包已安装（``uv sync --extra studio``）
  - 必需环境变量：``AGENTS_DIR``, ``CORE_SKILLS_DIR``
  - 可选环境变量：``STUDIO_DATA_DIR``, ``DB_HOST/PORT/USER/PASSWORD/NAME/SSLMODE``
"""

from __future__ import annotations

import logging
from typing import Any

from ark_agentic.core.protocol.plugin import BasePlugin
from ark_agentic.core.utils.env import env_flag

logger = logging.getLogger(__name__)


class EgisStudioPlugin(BasePlugin):
    """EGIS Studio 管理界面插件（agents / chats / RAG 名称查询 + 前端 SPA）。"""

    name = "egis_studio"

    def __init__(self) -> None:
        # 延迟初始化：install_routes 阶段才加载配置和服务实例
        self._rag_service: Any = None

    def is_enabled(self) -> bool:
        if not env_flag("ENABLE_EGIS_STUDIO"):
            return False
        # 检查 egis-agent-studio 是否已安装
        try:
            import egis_agent_studio  # noqa: F401
            return True
        except ImportError:
            logger.warning(
                "ENABLE_EGIS_STUDIO=true 但 egis_agent_studio 未安装，"
                "跳过 Studio 插件（请 `uv sync --extra studio`）"
            )
            return False

    def install_routes(self, app: Any) -> None:
        """挂载 Studio 路由 + 前端静态资源到 FastAPI app。"""
        from egis_agent_studio.app import mount_static
        from egis_agent_studio.config import StudioConfig
        from egis_agent_studio.router import build_studio_router
        from egis_agent_studio.services.agent_service import AgentService
        from egis_agent_studio.services.chat_service import ChatService
        from egis_agent_studio.services.rag_service import RAGService

        try:
            cfg = StudioConfig.load()
        except Exception as exc:
            logger.error("Studio 配置加载失败，插件路由不挂载：%s", exc)
            return

        # 创建服务实例并挂到 app.state（供 router 的 _services 依赖注入取用）
        app.state.agent_service = AgentService(
            agents_base_dir=cfg.paths.agents_base_dir,
            core_skills_dir=cfg.paths.core_skills_dir,
        )
        app.state.chat_service = ChatService(data_dir=cfg.paths.studio_data_dir)
        app.state.rag_service = RAGService(
            db_config=cfg.db,
            paths_config=cfg.paths,
        )
        self._rag_service = app.state.rag_service

        # 预建 chats/ 目录
        cfg.paths.studio_data_dir.mkdir(parents=True, exist_ok=True)
        (cfg.paths.studio_data_dir / "chats").mkdir(parents=True, exist_ok=True)

        # 注册路由
        app.include_router(build_studio_router())
        logger.info(
            "EgisStudioPlugin: 路由已挂载（agents_dir=%s skills_dir=%s）",
            cfg.paths.agents_base_dir,
            cfg.paths.core_skills_dir,
        )

        # 静态前端（dist/ 存在时挂载，放在路由之后）
        mount_static(app)

    async def start(self, ctx: Any) -> None:
        """启动 RAG asyncpg 连接池。"""
        if self._rag_service is not None:
            await self._rag_service.connect()
            logger.info("EgisStudioPlugin: RAG 连接池已建立")

    async def stop(self) -> None:
        """释放 RAG asyncpg 连接池。"""
        if self._rag_service is not None:
            try:
                await self._rag_service.close()
                logger.info("EgisStudioPlugin: RAG 连接池已关闭")
            except Exception:
                logger.exception("EgisStudioPlugin: RAG 连接池关闭失败")
