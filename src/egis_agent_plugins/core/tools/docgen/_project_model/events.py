"""DocGen Events — events.jsonl 追加写入

每次项目状态变更、工具调用、用户交互都记录一条事件。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def append_event(
    project_path: Path | str,
    event_name: str,
    *,
    flow_id: str = "",
    step: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """向 events.jsonl 追加一条事件记录

    Args:
        project_path: 项目根目录路径
        event_name: 事件名称，如 "project_created", "file_uploaded"
        flow_id: 关联的 flow ID
        step: 关联的 step ID
        **extra: 其他事件字段（artifact_id, error 等）

    Returns:
        写入的事件 dict
    """
    project_path = Path(project_path)
    events_file = project_path / "events.jsonl"

    event: dict[str, Any] = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "event": event_name,
    }
    if flow_id:
        event["flow_id"] = flow_id
    if step:
        event["step"] = step
    event.update(extra)

    try:
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        logger.debug("[DocGen Event] %s: %s", event_name, extra)
    except Exception as e:
        logger.warning("[DocGen Event] Failed to append event '%s': %s", event_name, e)

    return event


def read_events(project_path: Path | str) -> list[dict[str, Any]]:
    """读取项目全部事件

    Args:
        project_path: 项目根目录路径

    Returns:
        事件列表（按时间顺序）
    """
    project_path = Path(project_path)
    events_file = project_path / "events.jsonl"

    if not events_file.exists():
        return []

    events: list[dict[str, Any]] = []
    with open(events_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events
