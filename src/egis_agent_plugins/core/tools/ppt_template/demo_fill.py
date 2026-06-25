"""CLI for trying PptTemplateFiller on a local PPTX file.

Example:
    python -m egis_agent_plugins.core.tools.ppt_template.demo_fill \
      /path/template.pptx \
      /path/output.pptx \
      --exclude-first-last \
      --fills-json '{"content1":"..."}'
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .filler import PptTemplateFiller


def _load_fills(args: argparse.Namespace) -> dict[str, Any]:
    if args.fills_file:
        return json.loads(Path(args.fills_file).read_text(encoding="utf-8"))
    if args.fills_json:
        return json.loads(args.fills_json)
    return {
        "content1": (
            "FinOps 通过预算、成本归因、资源优化和持续运营机制，将云资源使用与业务价值挂钩，"
            "帮助组织在保障稳定性的同时降低单位业务成本。"
        ),
        "table1": {
            "type": "table",
            "columns": ["年份", "养老险单位业务成本"],
            "rows": [
                ["2021", "10亿"],
                ["2022", "8亿"],
                ["2023", "3亿"],
            ],
        },
        "chart1": {
            "type": "chart",
            "chart_type": "line",
            "categories": ["周一", "周二", "周三", "周四", "周五"],
            "series": {
                "业务A": [120, 132, 128, 145, 150],
                "业务B": [98, 105, 111, 118, 126],
                "业务C": [76, 84, 90, 95, 102],
            },
            "title": "运维平台高频业务每日使用量",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill a fixed PPTX template.")
    parser.add_argument("template_pptx", help="Input PPTX template path.")
    parser.add_argument("output_pptx", help="Output PPTX path.")
    parser.add_argument("--fills-file", help="JSON file containing placeholder fill values.")
    parser.add_argument("--fills-json", help="JSON string containing placeholder fill values.")
    parser.add_argument("--exclude-first-last", action="store_true", help="Remove first and last slides.")
    parser.add_argument(
        "--exclude-slides",
        default="",
        help="Comma-separated 1-based slide numbers to remove, for example: 1,8.",
    )
    parser.add_argument(
        "--clear-unknown",
        action="store_true",
        help="Clear unresolved text placeholders instead of keeping them.",
    )
    args = parser.parse_args()

    exclude_slides = [
        int(item.strip())
        for item in args.exclude_slides.split(",")
        if item.strip()
    ]

    filler = PptTemplateFiller(args.template_pptx)
    result = filler.render(
        args.output_pptx,
        _load_fills(args),
        exclude_slide_numbers=exclude_slides,
        exclude_first=args.exclude_first_last,
        exclude_last=args.exclude_first_last,
        keep_unknown_placeholders=not args.clear_unknown,
    )
    print(json.dumps({
        "output_path": result.output_path,
        "placeholders_found": result.placeholders_found,
        "filled": result.filled,
        "missing": result.missing,
        "unused_values": result.unused_values,
        "deleted_slide_numbers": result.deleted_slide_numbers,
        "warnings": result.warnings,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
