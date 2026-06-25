"""材料制作流程控制工具集

提供材料制作助手专属的流程控制工具：
- SelectMethodTool:    弹出制作方式选择卡片（标化/AI）
- StdRedirectTool:     标化路径跳转卡片
- OutlineCardTool:     可编辑大纲卡片
- MaterialSaveStateTool: 流程状态持久化

环境变量:
    无专属环境变量，配置复用 core/display 体系。
"""

from .select_method import SelectMethodTool
from .std_redirect import StdRedirectTool
from .outline_card import OutlineCardTool
from .state_tools import MaterialSaveStateTool

__all__ = [
    "SelectMethodTool",
    "StdRedirectTool",
    "OutlineCardTool",
    "MaterialSaveStateTool",
    "create_material_tools",
]


def create_material_tools() -> list:
    """创建材料制作流程控制工具实例列表

    Returns:
        Tool 实例列表，可直接注册到 ToolRegistry。
    """
    return [
        SelectMethodTool(),
        StdRedirectTool(),
        OutlineCardTool(),
        MaterialSaveStateTool(),
    ]
