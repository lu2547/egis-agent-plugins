"""egis-agent-plugins 路由

子模块：
    download  → /api/download/*

Chat 路由与 /health 由 ark ``APIPlugin`` 原生提供，不再在此注册。
"""

from __future__ import annotations

from egis_agent_plugins.core.router.download import register_download_routes

__all__ = ["register_download_routes"]
