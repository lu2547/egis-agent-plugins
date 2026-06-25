"""下载服务层

把原先散落在 ``router/download*`` 中的安全/业务逻辑集中于此：
- ``token_handler``：下载链接的签发与验签（itsdangerous）
- ``path_guard``：白名单 + 扩展名 + 路径穿越防护
- ``file_lister``：按项目目录列举可下载文件
路由层只负责把 HTTP 请求翻译成上述服务调用。
"""

from .token_handler import (
    decode_download_token,
    decode_project_token,
    encode_download_token,
    encode_project_token,
)
from .path_guard import (
    ALLOWED_EXTENSIONS,
    MIME_TYPES,
    PathGuardError,
    get_projects_dir,
    mime_of,
    resolve_safe_dir,
    resolve_safe_file,
)
from .file_lister import collect_files

__all__ = [
    # token
    "encode_download_token",
    "decode_download_token",
    "encode_project_token",
    "decode_project_token",
    # path guard
    "ALLOWED_EXTENSIONS",
    "MIME_TYPES",
    "PathGuardError",
    "get_projects_dir",
    "mime_of",
    "resolve_safe_dir",
    "resolve_safe_file",
    # file listing
    "collect_files",
]
