"""DocGen 素材工具集

提供文档素材处理工具:
- DocgenSourceUploadCardTool:  展示上传卡片（A2UI）
- DocgenSourceSaveUploadTool:  保存上传文件到项目
- DocgenSourceParseMineruTool: 调用 MinerU 解析文档
"""

from .upload_card import DocgenSourceUploadCardTool
from .save_upload import DocgenSourceSaveUploadTool
from .parse_mineru import DocgenSourceParseMineruTool

__all__ = [
    "DocgenSourceUploadCardTool",
    "DocgenSourceSaveUploadTool",
    "DocgenSourceParseMineruTool",
    "create_docgen_source_tools",
]


def create_docgen_source_tools() -> list:
    """创建 docgen 素材工具实例列表

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    return [
        DocgenSourceUploadCardTool(),
        DocgenSourceSaveUploadTool(),
        DocgenSourceParseMineruTool(),
    ]
