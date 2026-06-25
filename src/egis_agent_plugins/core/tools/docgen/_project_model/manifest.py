"""DocGen Manifest 模型 — manifest.json 的 Pydantic 定义

manifest.json 是项目核心元数据文件，记录项目状态、活跃 flow、artifact 索引和版本。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArtifactMeta(BaseModel):
    """Artifact 元数据"""

    artifact_id: str
    kind: str  # upload / parsed / template / word_draft / word_final ...
    file_format: str  # pdf / docx / md / json ...
    path: str  # 相对于 project root 的路径
    created_by: str  # 创建此 artifact 的 tool name
    source_artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class FlowState(BaseModel):
    """活跃 Flow 状态快照"""

    flow_id: str
    parent_flow_id: str | None = None
    current_step: str = ""
    blocking: bool = False
    blocking_event: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class ManifestModel(BaseModel):
    """docgen 项目 manifest.json 的完整模型"""

    project_id: str
    project_name: str
    workflow: str  # e.g. "docgen.word_standard.tender"
    document_kind: str  # "word" | "ppt"
    status: str  # "created" | "running" | "waiting_upload" | "completed" | ...
    current_step: str = ""
    active_flow: FlowState | None = None
    artifacts: dict[str, ArtifactMeta | None] = Field(default_factory=dict)
    versions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def set_artifact(self, key: str, meta: ArtifactMeta) -> None:
        """注册或更新一个 artifact"""
        self.artifacts[key] = meta
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def get_artifact(self, key: str) -> ArtifactMeta | None:
        """读取一个 artifact 元数据"""
        return self.artifacts.get(key)

    def set_flow(self, flow: FlowState) -> None:
        """更新活跃 flow 状态"""
        self.active_flow = flow
        self.current_step = flow.current_step
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def clear_flow(self) -> None:
        """清除活跃 flow（flow 完成后调用）"""
        self.active_flow = None
        self.updated_at = datetime.now().isoformat(timespec="seconds")
