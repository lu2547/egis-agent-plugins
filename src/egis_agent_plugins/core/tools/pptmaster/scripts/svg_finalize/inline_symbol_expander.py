"""Inline standard SVG ``<use href="#id">`` references into <g>+<shape>.

Background
----------
LLMs trained on web SVG conventions strongly prefer the
``<defs>+<symbol>+<use href>`` pattern for icons. The pptmaster pipeline,
however, only supports the project-internal ``<use data-icon="lib/name"/>``
placeholder syntax — drawingml_converter rejects raw ``<symbol>`` and
``<use href>`` as unsupported visuals.

This module is the machine-side fallback that translates the standard
pattern into the primitive subset (``<g>`` + ``<path>``/``<circle>``/...)
that the converter accepts. It runs as a finalize_svg step so the
expansion is *explicit and observable* (svg_final/ files contain the
expanded result; logs report counts) instead of silent in-memory magic.

Coordinate transform (viewBox-aware, precise version)
-----------------------------------------------------
For ``<symbol viewBox="vx vy vw vh">`` referenced by
``<use x="X" y="Y" width="W" height="H">``, the resulting wrapper is::

    <g transform="translate(X - vx*sx, Y - vy*sy) scale(sx, sy)">

where ``sx = W/vw``, ``sy = H/vh``. When ``width``/``height`` are omitted
on ``<use>``, scale falls back to 1:1. When ``viewBox`` is omitted, only
translate(X, Y) is applied. This matches SVG 1.1 §5.6 rendering rules
for ``<symbol>`` references.

Style propagation
-----------------
``<use>``'s ``fill`` / ``stroke`` / ``opacity`` / ``class`` / ``style``
attributes are placed on the wrapper ``<g>`` so they cascade into the
inlined children (matching SVG rendering semantics).

Scope
-----
Handles only same-document ``href="#fragment-id"`` references. Cross-
document references (``href="external.svg#id"``) are out of scope and
left untouched.

Public API
----------
``expand_inline_symbol_uses_in_tree(root)
    -> (expanded_count, removed_symbols_count)``
    In-memory variant; reused by svg_to_pptx so its native pipeline stays
    behaviourally aligned with finalize_svg.

``expand_inline_symbol_uses(svg_file, dry_run=False, verbose=False)
    -> (expanded_count, removed_symbols_count)``
    File-level wrapper used by the finalize_svg ``normalize-icons`` step.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'

# Preserve namespace declarations on round-trip.
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', XLINK_NS)


# ── tag/attribute helpers ────────────────────────────────────────────


def _local_tag(elem: ET.Element) -> str:
    """Return the local (un-namespaced) tag name."""
    return (
        elem.tag.split('}', 1)[-1]
        if isinstance(elem.tag, str) and '}' in elem.tag
        else str(elem.tag)
    )


def _get_href(elem: ET.Element) -> str | None:
    """Return the href value from <use href> or <use xlink:href>."""
    return (
        elem.get('href')
        or elem.get(f'{{{XLINK_NS}}}href')
        or elem.get('xlink:href')
    )


def _parse_float(s: str | None, default: float = 0.0) -> float:
    """Parse a float, stripping common SVG length units."""
    if s is None:
        return default
    try:
        cleaned = s.strip()
        for unit in ('px', 'pt', 'em', 'rem', '%'):
            if cleaned.endswith(unit):
                cleaned = cleaned[: -len(unit)]
                break
        return float(cleaned)
    except (ValueError, TypeError):
        return default


def _parse_viewbox(s: str | None) -> tuple[float, float, float, float] | None:
    """Parse a 'vx vy vw vh' viewBox string."""
    if not s:
        return None
    parts = s.replace(',', ' ').split()
    if len(parts) != 4:
        return None
    try:
        vals = tuple(float(p) for p in parts)
        return vals  # type: ignore[return-value]
    except ValueError:
        return None


def _fmt(v: float) -> str:
    """Format a float with trailing-zero trimming for cleaner output."""
    if v == int(v):
        return str(int(v))
    return f'{v:.4f}'.rstrip('0').rstrip('.')


# ── transform construction ───────────────────────────────────────────


def _build_transform(
    use_elem: ET.Element,
    ref_elem: ET.Element,
) -> str | None:
    """Compute the transform string that places ref's content at use's box.

    Implements the precise SVG 1.1 viewBox mapping:
        translate(X - vx*sx, Y - vy*sy) scale(sx, sy)
    where sx = W/vw, sy = H/vh.

    Returns None when the result is the identity transform.
    """
    x = _parse_float(use_elem.get('x'), 0.0)
    y = _parse_float(use_elem.get('y'), 0.0)
    use_w = use_elem.get('width')
    use_h = use_elem.get('height')

    sx, sy = 1.0, 1.0
    viewbox = _parse_viewbox(ref_elem.get('viewBox'))
    if viewbox and use_w and use_h:
        vx, vy, vw, vh = viewbox
        if vw > 0 and vh > 0:
            sx = _parse_float(use_w) / vw
            sy = _parse_float(use_h) / vh
            # Account for viewBox origin offset
            x -= vx * sx
            y -= vy * sy

    parts: list[str] = []
    if abs(x) > 1e-9 or abs(y) > 1e-9:
        parts.append(f'translate({_fmt(x)},{_fmt(y)})')
    if abs(sx - 1.0) > 1e-9 or abs(sy - 1.0) > 1e-9:
        if abs(sx - sy) < 1e-9:
            parts.append(f'scale({_fmt(sx)})')
        else:
            parts.append(f'scale({_fmt(sx)},{_fmt(sy)})')

    return ' '.join(parts) if parts else None


# ── expansion core ───────────────────────────────────────────────────


_INHERITED_USE_ATTRS = (
    'fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity',
    'stroke-opacity', 'class', 'style',
)


def _build_id_index(root: ET.Element) -> dict[str, ET.Element]:
    """Map id → element across the entire tree (depth-unrestricted)."""
    return {e.get('id'): e for e in root.iter() if e.get('id')}


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    """ElementTree elements lack a parent reference; build one explicitly."""
    parent_of: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parent_of[child] = parent
    return parent_of


def _collect_use_targets(root: ET.Element) -> list[ET.Element]:
    """Find all expandable <use> elements (same-document href, not data-icon)."""
    targets: list[ET.Element] = []
    for elem in root.iter():
        if _local_tag(elem) != 'use':
            continue
        if elem.get('data-icon'):
            continue  # handled by expand_use_data_icons elsewhere
        href = _get_href(elem)
        if not href or not href.startswith('#'):
            continue  # cross-document refs out of scope
        targets.append(elem)
    return targets


def _expand_one_use(
    use_elem: ET.Element,
    ref_elem: ET.Element,
    parent_of: dict[ET.Element, ET.Element],
) -> bool:
    """Replace use_elem with a <g> containing deepcopied ref content.

    Returns True on success, False when the parent can't be located.
    """
    parent = parent_of.get(use_elem)
    if parent is None:
        return False

    wrapper = ET.Element(f'{{{SVG_NS}}}g')

    transform = _build_transform(use_elem, ref_elem)
    if transform:
        wrapper.set('transform', transform)

    # Style attributes on <use> cascade onto wrapper so referenced
    # primitives inherit them (matches SVG rendering for fill="currentColor"
    # icons where color is supplied by the use site).
    for attr in _INHERITED_USE_ATTRS:
        v = use_elem.get(attr)
        if v is not None:
            wrapper.set(attr, v)

    # <symbol>/<g> wrappers contribute their *children* only; the wrapper
    # itself is dropped (symbol isn't a renderable element). For direct
    # primitive refs (path/circle/etc.), copy the element itself.
    ref_tag = _local_tag(ref_elem)
    if ref_tag in ('symbol', 'g'):
        for child in list(ref_elem):
            wrapper.append(copy.deepcopy(child))
    else:
        copied = copy.deepcopy(ref_elem)
        copied.attrib.pop('id', None)  # avoid duplicate ids
        wrapper.append(copied)

    idx = list(parent).index(use_elem)
    parent.remove(use_elem)
    parent.insert(idx, wrapper)
    return True


def _cleanup_unused_symbols_and_defs(
    root: ET.Element,
    expanded_ids: set[str],
    parent_of: dict[ET.Element, ET.Element],
) -> int:
    """Remove <symbol> elements that were inlined plus any empty <defs>.

    Returns the number of <symbol> elements removed. Empty <defs> blocks
    are also pruned (silently) to keep svg_final/ free of orphan structure
    that would otherwise still trip drawingml_converter's lint.
    """
    # Phase 1: remove inlined <symbol>s
    symbols_to_remove = [
        e for e in root.iter()
        if _local_tag(e) == 'symbol' and e.get('id') in expanded_ids
    ]
    removed = 0
    for sym in symbols_to_remove:
        parent = parent_of.get(sym)
        if parent is not None:
            parent.remove(sym)
            removed += 1

    # Phase 2: remove now-empty <defs>
    defs_to_remove = [
        e for e in root.iter()
        if _local_tag(e) == 'defs' and len(list(e)) == 0
    ]
    for d in defs_to_remove:
        parent = parent_of.get(d)
        if parent is not None:
            parent.remove(d)

    return removed


# ── public API ───────────────────────────────────────────────────────


def expand_inline_symbol_uses_in_tree(
    root: ET.Element,
    on_missing: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    """Expand same-document ``<use href="#id">`` references inside *root*.

    In-memory variant; mutates *root* directly. Used by both the finalize_svg
    ``normalize-icons`` step and the svg_to_pptx native pipeline so the two
    paths stay behaviourally aligned.

    Args:
        root: Root SVG element to walk.
        on_missing: Optional callback ``(href: str) -> None`` invoked when
            a ``<use>`` references an id that does not exist in the tree.

    Returns:
        ``(expanded_count, removed_symbols_count)``. ``(0, 0)`` means there
        was nothing expandable; *root* was left untouched.
    """
    targets = _collect_use_targets(root)
    if not targets:
        return 0, 0

    id_index = _build_id_index(root)
    parent_of = _build_parent_map(root)

    expanded_ids: set[str] = set()
    expanded_count = 0
    for use_elem in targets:
        href = _get_href(use_elem) or ''
        ref_id = href[1:]
        ref = id_index.get(ref_id)
        if ref is None:
            if on_missing is not None:
                on_missing(href)
            continue
        if _expand_one_use(use_elem, ref, parent_of):
            expanded_ids.add(ref_id)
            expanded_count += 1

    removed_symbols = 0
    if expanded_count:
        # Rebuild parent map after structural changes
        parent_of = _build_parent_map(root)
        removed_symbols = _cleanup_unused_symbols_and_defs(
            root, expanded_ids, parent_of,
        )

    return expanded_count, removed_symbols


def expand_inline_symbol_uses(
    svg_file: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> tuple[int, int]:
    """Expand same-document <use href="#id"> references in *svg_file* in place.

    File-level wrapper around :func:`expand_inline_symbol_uses_in_tree` for
    the finalize_svg pipeline.

    Args:
        svg_file: SVG file to process.
        dry_run: When True, do not write changes back to disk.
        verbose: Print a one-line summary per file.

    Returns:
        ``(expanded_count, removed_symbols_count)``. Both 0 means the file
        contained no expandable references and was left untouched.
    """
    try:
        tree = ET.parse(str(svg_file))
    except ET.ParseError as e:
        if verbose:
            print(f'   [ERROR] {svg_file.name}: parse failed: {e}')
        return 0, 0

    def _warn(href: str) -> None:
        if verbose:
            print(
                f'   [WARN] {svg_file.name}: <use href="{href}"> '
                f'references missing id; skipping'
            )

    expanded_count, removed_symbols = expand_inline_symbol_uses_in_tree(
        tree.getroot(), on_missing=_warn,
    )

    if expanded_count and not dry_run:
        tree.write(str(svg_file), encoding='utf-8', xml_declaration=False)

    if verbose and expanded_count:
        print(
            f'   [OK] {svg_file.name}: expanded {expanded_count} '
            f'<use href> reference(s), removed {removed_symbols} <symbol>(s)'
        )

    return expanded_count, removed_symbols
