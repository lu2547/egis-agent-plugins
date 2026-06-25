"""DocGen 公共能力集 — 文档制作公共工具

能力域:
- _project_model/: 项目模型（manifest / events / artifacts）
- project/: 项目管理工具（init / artifact / event）
- source/: 素材工具（upload / save / parse_mineru）
- word_standard/: Word 标化制作工具（build_template / fill / generate / open_editor）

聚合工厂:
- ``create_docgen_tools()`` — 返回所有 docgen 原子工具实例列表
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = [
    "create_docgen_tools",
]


def create_docgen_tools() -> list:
    """创建所有 DocGen 原子工具实例列表

    聚合 project + source + word_standard 三个能力域的工具。

    Returns:
        工具实例列表，可直接注册到 ToolRegistry。
    """
    from .project import create_docgen_project_tools
    from .source import create_docgen_source_tools
    from .word_standard import create_docgen_word_standard_tools

    tools: list = []
    tools.extend(create_docgen_project_tools())
    tools.extend(create_docgen_source_tools())
    tools.extend(create_docgen_word_standard_tools())
    return tools
