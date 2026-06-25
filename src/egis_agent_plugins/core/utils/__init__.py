"""通用工具集合

面向 agent/tools 层的轻量辅助：环境变量注入模板、字符串处理等。
无业务语义，不依赖 service/tools 层。
"""

from .env import format_env_section

__all__ = ["format_env_section"]
