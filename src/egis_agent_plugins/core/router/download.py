"""文件下载路由

仅保留 HTTP 语义：解码 token → 调 service → 返回响应。
安全策略、路径校验、文件列举全部在 ``core/service/download/`` 中实现。

路由：
    GET /api/download/{token}              — 下载指定项目文件
    GET /api/download/list/{project_token} — 列出项目可下载文件
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

from egis_agent_plugins.core.service.download.file_lister import collect_files
from egis_agent_plugins.core.service.download.path_guard import (
    PathGuardError,
    mime_of,
    resolve_safe_dir,
    resolve_safe_file,
)
from egis_agent_plugins.core.service.download.token_handler import (
    decode_download_token,
    decode_project_token,
)

logger = logging.getLogger(__name__)

download_router = APIRouter(prefix="/api/download", tags=["download"])


@download_router.get("/{token}")
async def download_file(token: str):
    """通过加密 token 下载项目文件。"""
    result = decode_download_token(token)
    if result is None:
        raise HTTPException(status_code=400, detail="无效或已过期的下载链接")

    abs_project_path, file_rel = result
    try:
        target = resolve_safe_file(abs_project_path, file_rel)
    except PathGuardError as e:
        logger.warning("[Download] %s (token=%s...)", e.reason, token[:8])
        raise HTTPException(status_code=e.status, detail=e.reason) from None

    logger.info("[Download] token=%s... → %s (%d bytes)", token[:8], target, target.stat().st_size)
    return FileResponse(path=str(target), media_type=mime_of(target), filename=target.name)


@download_router.get("/list/{project_token}")
async def list_project_files_by_token(project_token: str):
    """基于 project_token 列出项目可下载文件。"""
    abs_project_path = decode_project_token(project_token)
    if not abs_project_path:
        raise HTTPException(status_code=400, detail="无效或已过期的链接")

    try:
        project_dir = resolve_safe_dir(abs_project_path)
    except PathGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.reason) from None

    return collect_files(project_dir)


def register_download_routes(app: FastAPI) -> None:
    """挂载文件下载路由到 FastAPI 实例。"""
    app.include_router(download_router)
    logger.info("[Router] 已挂载文件下载路由: /api/download/*")
