"""DocGen Project 模型层 — manifest / events / artifacts 管理

纯数据层，不依赖任何 LLM 概念。所有 docgen tools 共享。
"""

from .store import ProjectStore
from .manifest import ManifestModel, ArtifactMeta, FlowState
from .events import append_event
from .artifacts import write_artifact, read_artifact, list_artifacts

__all__ = [
    "ProjectStore",
    "ManifestModel",
    "ArtifactMeta",
    "FlowState",
    "append_event",
    "write_artifact",
    "read_artifact",
    "list_artifacts",
]
