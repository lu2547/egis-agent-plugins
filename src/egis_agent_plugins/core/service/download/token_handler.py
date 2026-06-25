"""下载链接 Token 签发与验证

使用 itsdangerous.URLSafeSerializer 对路径参数做签名+序列化，
生成不透明的 URL-safe token，防止用户看到服务端目录结构。

Token payload 版本：
  v2 (当前)：{"ap": <abs_project_path>, "f": <file_rel>}
  v1 (兼容)：{"p": <project_name>, "f": <file_rel>}  —— 依赖 env 拼完整路径

密钥来源：DOWNLOAD_SECRET_KEY 环境变量，缺省使用硬编码值（仅开发用）。
"""

from __future__ import annotations

import os

from itsdangerous import URLSafeSerializer

# 密钥：生产环境务必通过环境变量设置
_SECRET_KEY = os.getenv("DOWNLOAD_SECRET_KEY", "egis-download-2026-dev")

_serializer = URLSafeSerializer(_SECRET_KEY, salt="egis-dl")


def encode_download_token(abs_project_path: str, file_rel: str) -> str:
    """签发 v2 token：存绝对项目目录 + 文件相对路径。

    Args:
        abs_project_path: 项目绝对路径（如 /.../projects/foo_ppt169_2026...）
        file_rel: 文件相对项目目录的路径（如 exports/xx.pptx）
    """
    return _serializer.dumps({"ap": abs_project_path, "f": file_rel})


def decode_download_token(token: str) -> tuple[str, str] | None:
    """解码 token，返回 (abs_project_path_or_empty, file_rel)。

    兼容 v1/v2：
      - v2: 有 ap 字段，直接返回绝对路径
      - v1: 只有 p 字段（短名），返回 ("", "<project_name>/<file_rel>")
             —— 由调用方根据 env 拼回根目录后再解析
    签名无效返回 None。
    """
    try:
        data = _serializer.loads(token)
    except Exception:
        return None
    # v2: 绝对路径方案
    if "ap" in data:
        return data["ap"], data["f"]
    # v1 兼容：返回空 abs_project_path + 已拼接的 <project_name>/<file_rel>
    if "p" in data:
        return "", f"{data['p']}/{data['f']}"
    return None


def encode_project_token(abs_project_path: str) -> str:
    """签发”仅项目目录” token，用于 list 接口。"""
    return _serializer.dumps({"ap": abs_project_path})


def decode_project_token(token: str) -> str | None:
    """解码 project token，返回项目绝对路径。签名无效或缺字段返回 None。"""
    try:
        data = _serializer.loads(token)
    except Exception:
        return None
    return data.get("ap") if isinstance(data, dict) else None
