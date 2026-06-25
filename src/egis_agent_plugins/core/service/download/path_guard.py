"""下载路径守卫 —— 白名单 / 路径穿越防护 / MIME 映射

职责：为下载路由提供纯函数级的安全校验与路径解析，
路由层仅保留 HTTP 语义，具体安全策略集中在此。

对外提供：
- ``ALLOWED_EXTENSIONS`` / ``MIME_TYPES``
- ``get_projects_dir()``            —— 读取 ``PPT_MASTER_PROJECTS_DIR`` 白名单根
- ``resolve_safe_path(base, rel)``  —— resolve + 可选白名单穿越校验
- ``ensure_allowed_extension(p)``   —— 校验扩展名
- ``mime_of(path)``                 —— 取 MIME 类型
"""

from __future__ import annotations

import os
from pathlib import Path


# 允许下载的文件扩展名（白名单）
ALLOWED_EXTENSIONS: set[str] = {
    ".pptx", ".docx", ".doc", ".xlsx", ".xls",
    ".svg", ".md", ".pdf", ".png", ".jpg", ".jpeg",
    ".json", ".txt", ".csv", ".html",
}

# MIME 类型映射
MIME_TYPES: dict[str, str] = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc":  "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls":  "application/vnd.ms-excel",
    ".svg":  "image/svg+xml",
    ".md":   "text/markdown",
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".json": "application/json",
    ".txt":  "text/plain",
    ".csv":  "text/csv",
    ".html": "text/html",
}


class PathGuardError(Exception):
    """路径守卫异常 —— 路由层捕获后映射到 HTTP 状态码"""

    def __init__(self, reason: str, *, status: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def get_projects_dir() -> Path | None:
    """读取 ``PPT_MASTER_PROJECTS_DIR``。空值 / 目录不存在返回 None。

    lazy 求值，避免模块导入时环境变量还未准备好。
    """
    val = (os.getenv("PPT_MASTER_PROJECTS_DIR") or "").strip()
    if not val:
        return None
    p = Path(val)
    return p if p.is_dir() else None


def _ensure_within_whitelist(target: Path) -> None:
    """若配置了 ``PPT_MASTER_PROJECTS_DIR``，校验 target 位于其内。"""
    projects_dir = get_projects_dir()
    if projects_dir is None:
        return
    try:
        target.relative_to(projects_dir.resolve())
    except ValueError as e:
        raise PathGuardError("禁止访问项目目录外的文件", status=403) from e


def resolve_safe_file(abs_base: str | Path, rel: str | Path = "") -> Path:
    """解析 ``abs_base + rel`` 为文件路径，依次做：

    1. resolve + 白名单穿越校验（403）
    2. 扩展名白名单校验（403）
    3. 文件存在性校验（404）
    """
    target = (Path(abs_base) / rel).resolve()
    _ensure_within_whitelist(target)
    ext = target.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise PathGuardError(f"不允许下载 {ext} 类型文件", status=403)
    if not target.is_file():
        raise PathGuardError("文件不存在或已被清理", status=404)
    return target


def resolve_safe_dir(abs_path: str | Path) -> Path:
    """解析 ``abs_path`` 为项目目录，依次做：

    1. resolve + 白名单穿越校验（403）
    2. 目录存在性校验（404）
    """
    target = Path(abs_path).resolve()
    _ensure_within_whitelist(target)
    if not target.is_dir():
        raise PathGuardError("项目不存在或已被清理", status=404)
    return target


def mime_of(path: Path) -> str:
    """获取文件 MIME 类型，未登记的返回 ``application/octet-stream``。"""
    return MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")
