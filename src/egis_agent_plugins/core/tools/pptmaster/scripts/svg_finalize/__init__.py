"""svg_finalize — shared self-containment + DrawingML-compat utilities.

Used by two consumers:

  1. finalize_svg.py — writes svg_output/ → svg_final/ on disk
  2. svg_to_pptx (use_expander, tspan_flattener) — reuses these modules
     in memory during native pptx conversion

Deleting any module here is likely to break native pptx output, not just
svg_final/. See docs/technical-design.md "Post-Processing Pipeline" for
the full per-module consumer table.
"""

import os
from pathlib import Path

__all__ = ['get_icons_dir']


def get_icons_dir() -> Path:
    """Resolve the templates/icons directory in a single authoritative place.

    Resolution order:
      1. ``PPT_MASTER_SKILL_DIR`` env var (matches the in-process tools in
         pptmaster/resource_tools.py and pptmaster/layout_tools.py). This
         is propagated by pptmaster._base.run_script via ``child_env``.
      2. Best-effort sibling lookup (``../../skills/pptmaster/templates/icons``
         relative to this scripts/ tree) for environments where the env var
         is unset but the package is laid out the standard way.

    Raises:
        FileNotFoundError: When neither source yields an existing directory.
            We intentionally fail loud here — silently skipping icon
            expansion would turn every ``<use data-icon="..."/>`` into a
            DrawingML 'unsupported visual' error downstream, hiding the
            real cause (misconfigured env var / missing assets).
    """
    env_dir = os.getenv('PPT_MASTER_SKILL_DIR', '').strip()
    if env_dir:
        candidate = Path(env_dir) / 'templates' / 'icons'
        if candidate.exists():
            return candidate

    # Fallback: scripts/ is at .../tools/pptmaster/scripts/svg_finalize/;
    # the canonical templates location is at .../skills/pptmaster/templates/.
    here = Path(__file__).resolve()
    # __file__ -> .../tools/pptmaster/scripts/svg_finalize/__init__.py
    # parents[3] -> .../tools
    # parents[4] -> .../core
    try:
        core_dir = here.parents[4]
        sibling = core_dir / 'skills' / 'pptmaster' / 'templates' / 'icons'
        if sibling.exists():
            return sibling
    except IndexError:
        pass

    raise FileNotFoundError(
        'pptmaster icons directory not found. Set PPT_MASTER_SKILL_DIR '
        'to the skill root (containing templates/icons/), or install the '
        'skill assets next to the tools package.'
    )
