"""egis-agent-plugins 对外暴露的 ark 标准 Plugin 集合。

每个 Plugin 实现 ark ``BasePlugin`` 协议，由宿主 composition root
通过 ``Bootstrap([EgisStudioPlugin(), EgisDownloadPlugin()])`` 统一编排。
"""

from .download import EgisDownloadPlugin
from .studio import EgisStudioPlugin

__all__ = ["EgisStudioPlugin", "EgisDownloadPlugin"]
