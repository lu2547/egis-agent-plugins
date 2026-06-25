"""环境变量注入工具

将指定的环境变量格式化为 system protocol 片段，供 agent 的 _build_protocol 调用。
避免每个 agent 重复写相同的 env → markdown 格式化逻辑。
"""

from __future__ import annotations

import os


def format_env_section(
    title: str,
    var_names: list[str],
    *,
    prefix: str = "- ",
    label_map: dict[str, str] | None = None,
) -> str:
    """将环境变量列表格式化为 Markdown section。

    只输出已设置（非空）的变量。

    Args:
        title: Section 标题，如 "PPT Master 运行时配置"。
        var_names: 要注入的环境变量名列表。
        prefix: 每行前缀，默认 "- "。
        label_map: 可选，变量名 → 展示标签映射。不传则直接用变量名。

    Returns:
        格式化后的 Markdown 字符串（含前后空行）。如果所有变量均未设置，返回空串。
    """
    label_map = label_map or {}
    lines: list[str] = []
    for name in var_names:
        value = os.getenv(name, "")
        if value:
            label = label_map.get(name, name)
            lines.append(f"{prefix}{label}: `{value}`")

    if not lines:
        return ""

    return f"\n### {title}\n" + "\n".join(lines) + "\n"
