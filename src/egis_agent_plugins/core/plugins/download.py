"""EgisDownloadPlugin —— 文件下载路由插件。

挂载 ``/api/download/*`` 路由（PPT/Word 生成产物下载）。
始终启用，无外部依赖。
"""

from __future__ import annotations

import logging
from typing import Any

from ark_agentic.core.protocol.plugin import BasePlugin

logger = logging.getLogger(__name__)


class EgisDownloadPlugin(BasePlugin):
    """文件下载路由插件（PPT/Word 生成产物）。"""

    name = "egis_download"

    def install_routes(self, app: Any) -> None:
        from egis_agent_plugins.core.router.download import register_download_routes

        register_download_routes(app)
        logger.info("EgisDownloadPlugin: /api/download/* 路由已挂载")
