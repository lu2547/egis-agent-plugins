"""egis_agent_plugins.core.tools.delivery — 文件交付工具

通用文件交付工具，适用于所有需要向用户提供可下载文件链接的场景：
PPT、Word、PDF、Excel 等。依赖 FastAPI 下载接口（router/download.py）。
"""

from .download_url import CreateDownloadUrlTool

__all__ = [
    "CreateDownloadUrlTool",
]
