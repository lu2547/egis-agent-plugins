"""列举项目可下载文件

扫描项目目录，返回白名单内扩展名的文件元信息供前端展示。
路由层调用 ``collect_files(project_dir)`` 即可。
"""

from __future__ import annotations

from pathlib import Path

from .path_guard import ALLOWED_EXTENSIONS
from .token_handler import encode_download_token, encode_project_token


def collect_files(project_dir: Path) -> dict:
    """列举 ``project_dir`` 下白名单扩展名的可下载文件。

    Returns:
        ``{"project_token": <str>, "files": [{name, rel_path, size, download_url}, ...]}``
    """
    abs_project_path = str(project_dir.resolve())
    project_token = encode_project_token(abs_project_path)

    files: list[dict] = []
    for p in sorted(project_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        rel = p.relative_to(project_dir)
        token = encode_download_token(abs_project_path, str(rel))
        files.append({
            "name": p.name,
            "rel_path": str(rel),
            "size": p.stat().st_size,
            "download_url": f"/api/download/{token}",
        })

    return {
        "project_token": project_token,
        "files": files,
    }
