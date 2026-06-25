"""Template-based PPT filling helpers.

This package is intentionally separate from pptmaster's free-generation
pipeline. It focuses on fixed PPTX pages with a small number of placeholders.
"""

from .filler import (
    ChartFill,
    FillResult,
    PptTemplateFiller,
    TableFill,
)

__all__ = [
    "ChartFill",
    "FillResult",
    "PptTemplateFiller",
    "TableFill",
]
