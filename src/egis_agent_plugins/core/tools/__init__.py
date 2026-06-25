"""egis_agent_plugins.core.tools — LLM 可见的 AgentTool 集合

- react/    : ReAct 推理工具（todo_write、final_answer）
- pptmaster/: PPT 生成工具（subprocess 封装 ppt-master 脚本）
- wordmaster/: Word 文档工具（subprocess 封装 docx 脚本）
- delivery/ : 文件交付工具（生成可点击下载链接）

RAG 能力不再在本包下暴露子工具：LLM 只看到一个 ``rag``，
实现全部下沉到 ``core.flows.rag``（tool / workflow / _services / _steps）。
顶层 __init__.py 保持对外接口不变，内部实现按子目录组织。
"""

from .react import (
    TodoWriteTool,
    FinalAnswerTool,
    build_react_callbacks,
)
from .delivery import CreateDownloadUrlTool

__all__ = [
    "TodoWriteTool",
    "FinalAnswerTool",
    "build_react_callbacks",
    "CreateDownloadUrlTool",
    # 工厂函数
    "create_rag_workflow_tool",
    "create_react_tools",
    "create_pptmaster_tools",
    "create_wordmaster_tools",
    "create_delivery_tools",
    "create_material_tools",
]


def create_rag_workflow_tool(
    config: "RAGConfig",
    *,
    knowledge_base_ids: list[str] | None = None,
) -> list:
    """RAG workflow 一体化工具工厂 — 返回单个 RagTool。

    对 LLM 暴露单一 ``rag`` 工具，内部 auto-drive 完成全流程。

    Args:
        config: RAGConfig 实例
        knowledge_base_ids: 知识库 ID 列表（覆盖 config 默认值）

    Returns:
        包含 RagTool 的单元素列表
    """
    from egis_agent_plugins.core.flows.rag import RagTool
    from egis_agent_plugins.core.flows.rag.clients import ServiceRegistry

    clients = ServiceRegistry.build_rag_clients(
        config,
        knowledge_base_ids=knowledge_base_ids,
    )
    return [RagTool(clients=clients)]


def create_react_tools() -> list:
    """创建 ReAct 推理工具集合。"""
    return [
        TodoWriteTool(),
        FinalAnswerTool(),
    ]


def create_pptmaster_tools() -> list:
    """创建 PPT Master 工具集合

    Returns:
        工具实例列表
    """
    from egis_agent_plugins.core.tools.pptmaster import create_pptmaster_tools as _create
    return _create()


def create_wordmaster_tools() -> list:
    """创建 Word Master 工具集合

    Returns:
        工具实例列表
    """
    from egis_agent_plugins.core.tools.wordmaster import create_wordmaster_tools as _create
    return _create()


def create_delivery_tools() -> list:
    """创建文件交付工具集合

    Returns:
        工具实例列表
    """
    return [CreateDownloadUrlTool()]


def create_material_tools() -> list:
    """创建材料制作流程控制工具集合

    Returns:
        工具实例列表（select_method / std_redirect / outline_card / material_save_state）
    """
    from egis_agent_plugins.core.tools.material import create_material_tools as _create
    return _create()
