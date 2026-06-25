"""Display 类型定义 — 双通道前端展示数据结构（强约束骨架）

前端通过 AGUI custom 事件接收 FrontendDigest，根据 display_mode 选择渲染视图。
各工具仅允许使用本模块定义的标准类型组装展示数据，禁止自定义结构。

骨架层级：
  FrontendDigest
    ├─ MinimalView (极简模式：一行摘要卡片)
    └─ DetailedView (详细模式)
         └─ ViewSection[] (结构化分段，content_type 驱动前端渲染)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal


# ────────────────────────────────────────────
# 枚举 & 常量
# ────────────────────────────────────────────

class DisplayMode(str, Enum):
    """前端显示模式"""

    MINIMAL = "minimal"   # 极简模式：一行摘要 + 图标
    DETAILED = "detailed"  # 详细模式：完整结构化数据


class ToolDisplayType(str, Enum):
    """工具展示类型 — 前端按此选择渲染模板

    每个工具必须声明自己的 display_type，前端据此分发到对应的渲染组件。
    """

    TEXT = "text"            # 纯文本/Markdown (thinking, query_rewrite, final_answer)
    SEARCH = "search"        # 搜索结果列表 (knowledge_search, grep_chunks)
    DATA = "data"            # 结构化数据卡片 (annuity_query, get_document_info)
    FILE = "file"            # 文件交付 (WordMaster, PPTMaster, download_url)
    CHECKLIST = "checklist"  # 清单/任务列表 (todo_write, select_documents)
    PROGRESS = "progress"    # 计划进度/步骤追踪
    RAW = "raw"              # 兜底 — 原始 JSON 展示


# 允许的 display_type 白名单
ALLOWED_DISPLAY_TYPES = {t.value for t in ToolDisplayType}


# 允许的 content_type 白名单 — 前端按此分发渲染组件
ALLOWED_CONTENT_TYPES = {
    "text",       # 纯文本段落
    "table",      # 表格数据 (data 为 {headers: [], rows: [[]]})
    "list",       # 列表数据 (data 为 [str])
    "checklist",  # 任务清单 (data 为 [{text, status, type?, children?}])
    "metric",     # 指标卡 (data 为 {label: str, value: str/number, unit?: str}[])
    "code",       # 代码块 (data 为 {language: str, code: str})
    "key_value",  # 键值对 (data 为 {key: value})
    "progress",   # 进度 (data 为 {current: int, total: int, label?: str})
    "status",     # 状态标记 (data 为 {status: str, message: str})
}


# ────────────────────────────────────────────
# 核心骨架类型
# ────────────────────────────────────────────

@dataclass
class MinimalView:
    """极简模式展示结构 — 前端渲染为紧凑的单行/卡片

    Attributes:
        title: 必填，标题（工具/操作名称）
        summary: 必填，一行摘要文本
        icon: 图标标识（前端图标库中的 key）
        status: 状态标记 (success/warning/error/info/loading)
    """

    title: str
    summary: str
    icon: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"title": self.title, "summary": self.summary}
        if self.icon:
            d["icon"] = self.icon
        if self.status:
            d["status"] = self.status
        return d


@dataclass
class ViewSection:
    """DetailedView 的结构化分段 — 前端按 content_type 分发渲染组件

    Attributes:
        heading: 段落标题
        content_type: 渲染类型，必须是 ALLOWED_CONTENT_TYPES 中的值
        data: 该类型对应的结构化数据
    """

    heading: str
    content_type: str
    data: Any

    def __post_init__(self):
        if self.content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(
                f"ViewSection.content_type='{self.content_type}' 不合法，"
                f"允许值: {sorted(ALLOWED_CONTENT_TYPES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "content_type": self.content_type,
            "data": self.data,
        }


@dataclass
class DetailedView:
    """详细模式展示结构 — 由强类型 ViewSection 列表组成

    Attributes:
        title: 标题
        sections: 结构化分段列表（每段有 heading + content_type + data）
    """

    title: str
    sections: list[ViewSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class FrontendDigest:
    """双模式前端展示数据 — 通过 AGUI custom 事件发送

    一次计算产出两套视图（minimal + detailed），前端可根据当前模式选择渲染，
    也可在本地切换模式无需再请求后端。

    Attributes:
        tool_name: 工具标识
        display_type: 展示类型（前端按此选择渲染模板），必须是 ALLOWED_DISPLAY_TYPES 中的值
        minimal: 极简模式视图
        detailed: 详细模式视图
    """

    tool_name: str
    display_type: str
    minimal: MinimalView
    detailed: DetailedView

    def __post_init__(self):
        if self.display_type not in ALLOWED_DISPLAY_TYPES:
            raise ValueError(
                f"FrontendDigest.display_type='{self.display_type}' 不合法，"
                f"允许值: {sorted(ALLOWED_DISPLAY_TYPES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        """序列化为完整字典（含两种模式）"""
        return {
            "tool_name": self.tool_name,
            "display_type": self.display_type,
            "minimal": self.minimal.to_dict(),
            "detailed": self.detailed.to_dict(),
        }

    def select(self, mode: DisplayMode) -> dict[str, Any]:
        """根据模式返回对应视图的字典"""
        if mode == DisplayMode.MINIMAL:
            return self.minimal.to_dict()
        return self.detailed.to_dict()
