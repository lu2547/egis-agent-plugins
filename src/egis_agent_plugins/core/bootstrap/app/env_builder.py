"""环境变量加载与组合型默认值。

两层 .env 加载机制（优先级从高到低）：
  1. 真实环境变量（export / docker -e / k8s env）
  2. 业务项目 .env（调用方 cwd 下的 .env）
  3. 框架 egis-agent-plugins/.env（兜底默认值）

组合型默认值：
  - DB_CONNECTION_STR 未设时，从 DB_HOST/PORT/USER/PASSWORD/NAME 构建 PG 连接串
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 框架 .env 路径：egis-agent-plugins/.env
_FRAMEWORK_ENV = Path(__file__).resolve().parents[5] / ".env"


def load_env() -> None:
    """两层 .env 加载，优先级：真实 env > 业务 .env > 框架 .env。

    两层均使用 ``override=False``，只填充尚未设置的变量，
    因此先加载的框架 .env 优先级最低，后加载的业务 .env 可覆盖它，
    而真实环境变量始终不被覆盖。
    """
    # 1) 业务 .env（cwd 下，不覆盖真实 env）
    _business_env = Path.cwd() / ".env"
    if _business_env.exists():
        load_dotenv(_business_env, override=False)
        logger.debug("Loaded business .env: %s", _business_env)

    # 2) 框架 .env（兜底默认值，不覆盖真实 env 和业务 .env 已设的值）
    if _FRAMEWORK_ENV.exists():
        load_dotenv(_FRAMEWORK_ENV, override=False)
        logger.debug("Loaded framework .env: %s", _FRAMEWORK_ENV)


def apply_computed_defaults() -> None:
    """从组件变量构建复合值（仅当复合值本身未设时）。

    当前处理的组合型默认值：
    - ``DB_CONNECTION_STR``：从 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME 构建
    """
    if not os.environ.get("DB_CONNECTION_STR", "").strip():
        host = os.environ.get("DB_HOST", "").strip()
        if host:
            user = os.getenv("DB_USER", "postgres")
            pwd = os.getenv("DB_PASSWORD", "")
            port = os.getenv("DB_PORT", "5432")
            name = os.getenv("DB_NAME", "postgres")
            os.environ["DB_CONNECTION_STR"] = (
                f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{name}"
            )
            logger.debug(
                "Built DB_CONNECTION_STR from components (host=%s port=%s db=%s)",
                host, port, name,
            )
