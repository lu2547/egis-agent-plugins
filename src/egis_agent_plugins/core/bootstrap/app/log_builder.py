"""日志初始化与噪音治理

提供 init_logging() 统一入口，在应用启动时调用一次即可。
将日志配置从 app.py 等入口文件中彻底解耦。

环境变量：
    LOG_LEVEL   全局日志级别（默认 INFO）

噪音治理说明：
    以下第三方/标准库包在默认日志级别下会产生大量无关输出，
    需要将其压制到 WARNING 级别以保持业务日志可读性：

    - httpcore   httpx 底层连接层，每次 HTTP 请求输出 DEBUG 级连接详情
    - httpx      Milvus / Embedding API 调用走此库，INFO 级输出 request/response headers
    - urllib3    连接池管理，每次建连/重试输出一行 INFO
    - asyncio    标准库事件循环，DEBUG 级输出 selector 详情

    RAG 工具单次检索即可触发上述包刷出十几行日志，
    不压制会严重淹没业务日志。

用法：
    from egis_agent_plugins.core.config import init_logging
    init_logging()
"""

from __future__ import annotations

import logging
import os

# 嘈杂包列表：这些包在 INFO/DEBUG 级别下产生大量无关输出
# 新增嘈杂包时在此列表追加即可，无需修改调用方
_NOISY_PACKAGES = (
    "httpcore",    # httpx 底层连接层 — DEBUG 输出每次 HTTP 请求的连接详情
    "httpx",       # HTTP 客户端 — INFO 输出 Milvus/Embedding 请求的 headers
    "urllib3",     # 连接池 — INFO 输出连接建立/重试信息
    "asyncio",     # 标准库事件循环 — DEBUG 输出 selector 详情
)

# 嘈杂包压制到的目标级别
_NOISY_LEVEL = logging.WARNING


def init_logging() -> None:
    """初始化全局日志配置。

    1. 根据 LOG_LEVEL 环境变量设置根 logger 级别
    2. 配置统一日志格式
    3. 将嘈杂第三方包压制到 WARNING 级别

    应在应用入口（app.py / CLI）调用一次，不要重复调用。
    """
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    for pkg in _NOISY_PACKAGES:
        logging.getLogger(pkg).setLevel(_NOISY_LEVEL)
