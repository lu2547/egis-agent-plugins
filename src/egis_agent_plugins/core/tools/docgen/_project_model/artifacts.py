"""DocGen Artifacts — artifact 文件的读写管理

每个 artifact 由 ArtifactMeta（manifest 中）+ 物理文件（project 目录中）组成。
本模块提供物理文件层面的操作，manifest 层面的操作在 store.py 中。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .manifest import ArtifactMeta

logger = logging.getLogger(__name__)


def write_artifact(
    project_path: Path | str,
    meta: ArtifactMeta,
    content: bytes | str | None = None,
    *,
    source_file: Path | str | None = None,
) -> Path:
    """写入 artifact 文件到项目目录

    Args:
        project_path: 项目根目录
        meta: artifact 元数据（含 path 字段）
        content: 文件内容（bytes 或 str）
        source_file: 源文件路径（复制而非写入 content）

    Returns:
        artifact 文件的绝对路径
    """
    project_path = Path(project_path)
    artifact_path = project_path / meta.path

    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    if source_file is not None:
        source_file = Path(source_file)
        if source_file.exists():
            shutil.copy2(str(source_file), str(artifact_path))
            logger.info("[DocGen Artifact] Copied %s → %s", source_file, artifact_path)
        else:
            raise FileNotFoundError(f"Source file not found: {source_file}")
    elif content is not None:
        if isinstance(content, str):
            artifact_path.write_text(content, encoding="utf-8")
        else:
            artifact_path.write_bytes(content)
        logger.info("[DocGen Artifact] Written %s (%d bytes)", artifact_path, artifact_path.stat().st_size)
    else:
        # 只注册元数据，不写物理文件（例如引用外部路径）
        logger.info("[DocGen Artifact] Registered metadata only: %s", meta.path)

    return artifact_path


def read_artifact(
    project_path: Path | str,
    meta: ArtifactMeta,
    *,
    as_text: bool = False,
) -> bytes | str | None:
    """读取 artifact 文件内容

    Args:
        project_path: 项目根目录
        meta: artifact 元数据
        as_text: 是否以文本方式读取

    Returns:
        文件内容，文件不存在返回 None
    """
    project_path = Path(project_path)
    artifact_path = project_path / meta.path

    if not artifact_path.exists():
        logger.warning("[DocGen Artifact] File not found: %s", artifact_path)
        return None

    if as_text:
        return artifact_path.read_text(encoding="utf-8")
    return artifact_path.read_bytes()


def list_artifacts(
    project_path: Path | str,
    subdirectory: str | None = None,
) -> list[dict[str, Any]]:
    """列出项目目录下的物理文件

    Args:
        project_path: 项目根目录
        subdirectory: 限定子目录（如 "sources/uploads"）

    Returns:
        文件信息列表
    """
    project_path = Path(project_path)
    search_dir = project_path / subdirectory if subdirectory else project_path

    if not search_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    for f in sorted(search_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            results.append({
                "name": f.name,
                "path": str(f.relative_to(project_path)),
                "size": f.stat().st_size,
            })
    return results
