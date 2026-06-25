"""Word Master 工具集

将 docx 技能的脚本封装为 Agent 可调用的 Tool，
通过 subprocess 执行，支持 Word 文档全流程：
  创建（docx-js）→ 编辑（unpack/edit/repack）→ 转换 → 导出

环境变量:
    WORD_MASTER_SCRIPTS_DIR: wordmaster scripts/ 全路径（可选，默认使用包内 scripts/）
    WORD_MASTER_PROJECTS_DIR: 项目基础目录（可选）
"""

from ._base import WordMasterBaseTool, SCRIPTS_DIR
from .project_tools import DocxProjectInitTool
from .file_tools import DocxWriteFileTool, DocxReadFileTool
from .generate_tools import DocxGenerateTool
from .outline_tools import DocxGenerateFromOutlineTool
from .edit_tools import DocxUnpackTool, DocxPackTool, DocxValidateTool
from .comment_tools import DocxAcceptChangesTool, DocxAddCommentTool
from .convert_tools import DocxConvertTool
from .state_tools import DocxSaveStateTool

__all__ = [
    # 基类与配置
    "WordMasterBaseTool",
    "SCRIPTS_DIR",
    # 项目管理
    "DocxProjectInitTool",
    # 文件读写
    "DocxWriteFileTool",
    "DocxReadFileTool",
    # 文档生成（docx-js）
    "DocxGenerateTool",
    "DocxGenerateFromOutlineTool",
    # 编辑流（unpack → edit → repack）
    "DocxUnpackTool",
    "DocxPackTool",
    "DocxValidateTool",
    # 修订与注释
    "DocxAcceptChangesTool",
    "DocxAddCommentTool",
    # 格式转换
    "DocxConvertTool",
    # 状态持久化
    "DocxSaveStateTool",
    # 工厂函数
    "create_wordmaster_tools",
]


def create_wordmaster_tools() -> list:
    """创建所有 Word Master 工具实例列表

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    return [
        # 项目管理
        DocxProjectInitTool(),
        # 文件读写
        DocxWriteFileTool(),
        DocxReadFileTool(),
        # 文档生成
        DocxGenerateTool(),
        DocxGenerateFromOutlineTool(),
        # 编辑流
        DocxUnpackTool(),
        DocxPackTool(),
        DocxValidateTool(),
        # 修订与注释
        DocxAcceptChangesTool(),
        DocxAddCommentTool(),
        # 格式转换
        DocxConvertTool(),
        # 状态持久化
        DocxSaveStateTool(),
    ]
