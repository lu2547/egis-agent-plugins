"""DocGen 项目管理工具集

提供 docgen 项目生命周期管理工具:
- DocgenProjectInitTool:  初始化项目目录
- DocgenWriteArtifactTool: 写入 artifact
- DocgenReadArtifactTool:  读取 artifact
- DocgenListArtifactsTool: 列出 artifacts
- DocgenAppendEventTool:   追加事件记录
"""

from .init_tool import DocgenProjectInitTool
from .artifact_tools import DocgenWriteArtifactTool, DocgenReadArtifactTool, DocgenListArtifactsTool
from .event_tool import DocgenAppendEventTool

__all__ = [
    "DocgenProjectInitTool",
    "DocgenWriteArtifactTool",
    "DocgenReadArtifactTool",
    "DocgenListArtifactsTool",
    "DocgenAppendEventTool",
    "create_docgen_project_tools",
]


def create_docgen_project_tools() -> list:
    """创建 docgen 项目管理工具实例列表

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    return [
        DocgenProjectInitTool(),
        DocgenWriteArtifactTool(),
        DocgenReadArtifactTool(),
        DocgenListArtifactsTool(),
        DocgenAppendEventTool(),
    ]
