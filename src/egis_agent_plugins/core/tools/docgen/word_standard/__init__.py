"""DocGen Word 标化制作工具集

提供 Word 标化制作工具:
- DocgenWordStandardBuildTemplateTool: 基于招标材料生成投标模板
- DocgenWordStandardFillFromRagTool:   基于模板从知识库检索填充
- DocgenWordStandardGenerateTool:      生成 Word 文档
- DocgenWordStandardOpenEditorTool:    打开 Word 编辑器
"""

from .build_template import DocgenWordStandardBuildTemplateTool
from .fill_from_rag import DocgenWordStandardFillFromRagTool
from .generate import DocgenWordStandardGenerateTool
from .open_editor import DocgenWordStandardOpenEditorTool

__all__ = [
    "DocgenWordStandardBuildTemplateTool",
    "DocgenWordStandardFillFromRagTool",
    "DocgenWordStandardGenerateTool",
    "DocgenWordStandardOpenEditorTool",
    "create_docgen_word_standard_tools",
]


def create_docgen_word_standard_tools() -> list:
    """创建 docgen Word 标化制作工具实例列表

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    return [
        DocgenWordStandardBuildTemplateTool(),
        DocgenWordStandardFillFromRagTool(),
        DocgenWordStandardGenerateTool(),
        DocgenWordStandardOpenEditorTool(),
    ]
