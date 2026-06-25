"""DocGen ProjectStore — 项目生命周期管理

封装项目创建、manifest 读写、目录管理。
所有 docgen tools 通过 ProjectStore 操作项目。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .manifest import ManifestModel, FlowState, ArtifactMeta
from .events import append_event

logger = logging.getLogger(__name__)

# 项目基础目录：优先 DOCGEN_PROJECTS_DIR，回退 WORD_MASTER_PROJECTS_DIR
_ENV_PROJECTS_DIR = (
    os.getenv("DOCGEN_PROJECTS_DIR", "")
    or os.getenv("WORD_MASTER_PROJECTS_DIR", "")
    or os.getenv("PPT_MASTER_PROJECTS_DIR", "")
)
PROJECTS_DIR: Path | None = Path(_ENV_PROJECTS_DIR) if _ENV_PROJECTS_DIR else None

# 项目子目录结构
_PROJECT_SUBDIRS = [
    "sources/uploads",
    "sources/parsed",
    "templates",
    "drafts",
    "output",
    "exports",
    "cache/rag",
    "cache/mineru",
]


def _sanitize_id(raw: str, max_len: int = 8) -> str:
    """移除特殊字符并截断，用于目录名安全拼接。"""
    return re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:max_len] or "anon"


class ProjectStore:
    """docgen 项目存储管理

    提供项目创建、manifest 读写、artifact 注册等核心操作。
    """

    def __init__(self, projects_dir: Path | None = None) -> None:
        self._projects_dir = projects_dir or PROJECTS_DIR

    @property
    def projects_dir(self) -> Path | None:
        return self._projects_dir

    def init_project(
        self,
        project_name: str,
        *,
        workflow: str,
        document_kind: str = "word",
        session_id: str = "",
        user_id: str = "",
    ) -> tuple[Path, ManifestModel]:
        """创建新项目

        Args:
            project_name: 项目名称（建议不含空格）
            workflow: workflow 标识，如 "docgen.word_standard.tender"
            document_kind: 文档类型 "word" | "ppt"
            session_id: 会话 ID（用于多用户隔离）
            user_id: 用户 ID

        Returns:
            (project_path, manifest) 元组
        """
        sid = _sanitize_id(session_id)
        uid = _sanitize_id(user_id)
        date_str = datetime.now().strftime("%Y%m%d")
        dir_name = f"{project_name}_{sid}_{uid}"

        if self._projects_dir:
            project_path = self._projects_dir / date_str / dir_name
        else:
            project_path = Path(date_str) / dir_name

        # 创建目录结构
        project_path.mkdir(parents=True, exist_ok=True)
        for sub in _PROJECT_SUBDIRS:
            (project_path / sub).mkdir(parents=True, exist_ok=True)

        project_path = project_path.resolve()

        # 创建 manifest
        project_id = f"{project_name}_{date_str}_{sid}_{uid}"
        manifest = ManifestModel(
            project_id=project_id,
            project_name=project_name,
            workflow=workflow,
            document_kind=document_kind,
            status="created",
        )

        self._write_manifest(project_path, manifest)

        # 记录创建事件
        append_event(
            project_path,
            "project_created",
            flow_id=workflow,
            project_id=project_id,
        )

        logger.info("[DocGen Project] Created: %s (id=%s)", project_path, project_id)
        return project_path, manifest

    def get_project(self, project_path: Path | str) -> ManifestModel | None:
        """读取项目 manifest

        Args:
            project_path: 项目根目录

        Returns:
            ManifestModel 或 None（项目不存在）
        """
        project_path = Path(project_path)
        manifest_file = project_path / "manifest.json"

        if not manifest_file.exists():
            return None

        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            return ManifestModel(**data)
        except Exception as e:
            logger.error("[DocGen Project] Failed to read manifest: %s", e)
            return None

    def update_manifest(
        self,
        project_path: Path | str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        active_flow: FlowState | None = None,
        clear_flow: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> ManifestModel | None:
        """更新项目 manifest

        Args:
            project_path: 项目根目录
            status: 新状态
            current_step: 当前步骤
            active_flow: 活跃 flow 状态
            clear_flow: 是否清除活跃 flow
            extra: 其他要更新的字段

        Returns:
            更新后的 ManifestModel
        """
        project_path = Path(project_path)
        manifest = self.get_project(project_path)
        if manifest is None:
            return None

        if status is not None:
            manifest.status = status
        if current_step is not None:
            manifest.current_step = current_step
        if active_flow is not None:
            manifest.set_flow(active_flow)
        if clear_flow:
            manifest.clear_flow()
        if extra:
            for k, v in extra.items():
                if hasattr(manifest, k):
                    setattr(manifest, k, v)

        manifest.updated_at = datetime.now().isoformat(timespec="seconds")
        self._write_manifest(project_path, manifest)
        return manifest

    def register_artifact(
        self,
        project_path: Path | str,
        key: str,
        meta: ArtifactMeta,
    ) -> ManifestModel | None:
        """注册 artifact 到 manifest

        Args:
            project_path: 项目根目录
            key: artifact 在 manifest 中的键名
            meta: artifact 元数据

        Returns:
            更新后的 ManifestModel
        """
        project_path = Path(project_path)
        manifest = self.get_project(project_path)
        if manifest is None:
            return None

        manifest.set_artifact(key, meta)
        self._write_manifest(project_path, manifest)
        return manifest

    def resolve_project_path(self, project_id_or_path: str) -> Path | None:
        """将 project_id 或路径解析为绝对路径

        支持:
        - 绝对路径 → 直接使用
        - 相对路径 → 在 projects_dir 下查找
        - project_id → 在 projects_dir 下搜索匹配目录

        Args:
            project_id_or_path: 项目 ID 或路径

        Returns:
            解析后的项目路径，未找到返回 None
        """
        p = Path(project_id_or_path)

        # 绝对路径
        if p.is_absolute() and p.is_dir():
            return p

        # projects_dir 下的相对路径
        if self._projects_dir:
            candidate = self._projects_dir / project_id_or_path
            if candidate.is_dir():
                return candidate

            # 搜索匹配 project_id 的目录
            for date_dir in self._projects_dir.iterdir():
                if date_dir.is_dir():
                    for proj_dir in date_dir.iterdir():
                        if proj_dir.is_dir() and proj_dir.name.startswith(project_id_or_path):
                            manifest_file = proj_dir / "manifest.json"
                            if manifest_file.exists():
                                return proj_dir

        return None

    @staticmethod
    def _write_manifest(project_path: Path, manifest: ManifestModel) -> None:
        """写入 manifest.json"""
        manifest_file = project_path / "manifest.json"
        manifest_file.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
