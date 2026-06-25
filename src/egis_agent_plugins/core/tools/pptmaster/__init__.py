"""PPT Master 工具集

将 ppt-master 的所有 Python 脚本封装为 Agent 可调用的 Tool，
通过 subprocess 执行，完整支持 ppt-master 全流程：
  Step 1 (知识库内容整理) → Step 2 (项目初始化) → Step 4 (图像分析)
  → Step 7 (后处理 & 导出)

环境变量:
    PPT_MASTER_SCRIPTS_DIR: ppt-master scripts/ 全路径（必须配置）
    PPT_MASTER_SKILL_DIR:   ppt-master 原始仓库目录（templates/icons/references 等）
"""

from ._base import PptMasterBaseTool, SCRIPTS_DIR, SKILL_DIR
from .source_to_md import PdfToMdTool, DocToMdTool, PptToMdTool, WebToMdTool
from .project_tools import ProjectInitTool, ProjectImportSourcesTool, ProjectValidateTool
from .image_tools import AnalyzeImagesTool
from .annotation_tools import CheckAnnotationsTool, SvgEditorTool
from .pipeline_tools import (
    TotalMdSplitTool,
    FinalizeSvgTool,
    SvgToPptxTool,
    SvgQualityCheckTool,
    UpdateSpecTool,
)
from .file_tools import WriteFileTool
from .layout_tools import CopyLayoutTool, ListLayoutsTool, RegisterTemplateTool
from .resource_tools import ReadFileTool, SearchIconsTool
from .state_tools import PptSaveStateTool

__all__ = [
    # 基类与配置
    "PptMasterBaseTool",
    "SCRIPTS_DIR",
    "SKILL_DIR",
    # Step 1: 文档转 Markdown
    "PdfToMdTool",
    "DocToMdTool",
    "PptToMdTool",
    "WebToMdTool",
    # Step 2: 项目管理
    "ProjectInitTool",
    "ProjectImportSourcesTool",
    "ProjectValidateTool",
    # Step 3: 布局模板
    "CopyLayoutTool",
    "ListLayoutsTool",
    "RegisterTemplateTool",
    # Step 4/6: 资源读取 & 图标搜索
    "ReadFileTool",
    "SearchIconsTool",
    # Step 4: 图像分析
    "AnalyzeImagesTool",
    # Step 6: SVG 标注 & 编辑器
    "CheckAnnotationsTool",
    "SvgEditorTool",
    # Step 4/6: 文件写入（LLM 生成内容落盘）
    "WriteFileTool",
    # Step 7: Pipeline 处理与导出
    "TotalMdSplitTool",
    "FinalizeSvgTool",
    "SvgToPptxTool",
    # 辅助工具
    "SvgQualityCheckTool",
    "UpdateSpecTool",
    # 状态持久化
    "PptSaveStateTool",
    # 工厂函数
    "create_pptmaster_tools",
]


def create_pptmaster_tools(*, include_converters: bool = False) -> list:
    """创建所有 PPT Master 工具实例列表

    企业问答默认不暴露文档转换工具。知识库文档应先由 RAG 检索和深度阅读，
    PPT Master 只接收已整理好的 Markdown/source_data。

    Args:
        include_converters: 兼容开关；仅独立 PPT 场景需要直接处理本地文件时启用。

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    tools = []
    if include_converters:
        tools.extend([
            PdfToMdTool(),
            DocToMdTool(),
            PptToMdTool(),
            WebToMdTool(),
        ])

    tools.extend([
        # Step 2: 项目管理
        ProjectInitTool(),
        ProjectImportSourcesTool(),
        ProjectValidateTool(),
        # Step 3: 布局模板
        CopyLayoutTool(),
        ListLayoutsTool(),
        RegisterTemplateTool(),
        # Step 4/6: 资源读取 & 图标搜索
        ReadFileTool(),
        SearchIconsTool(),
        # Step 4: 图像分析
        AnalyzeImagesTool(),
        # Step 6: SVG 标注 & 编辑器
        CheckAnnotationsTool(),
        SvgEditorTool(),
        # Step 4/6: 文件写入
        WriteFileTool(),
        # Step 7: Pipeline
        TotalMdSplitTool(),
        FinalizeSvgTool(),
        SvgToPptxTool(),
        # 辅助
        SvgQualityCheckTool(),
        UpdateSpecTool(),
        # 状态持久化
        PptSaveStateTool(),
    ])
    return tools
