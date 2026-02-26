"""Text visibility classification using PyMuPDF's texttrace data.

Replaces the old ``text_is_light_colored()`` heuristic with richer
analysis that considers render mode, opacity, and colour channels.
"""

from typing import Any, Dict, List, Tuple

import fitz

from .config import HomerConfig


def classify_text_visibility(
    text_spans: List[Dict[str, Any]],
    rect: fitz.Rect,
    config: HomerConfig,
) -> str:
    """Classify text visibility under *rect* using texttrace span data.

    Returns one of:
        ``"invisible_by_design"`` – render mode 3 (invisible/OCR layer) or
                                    opacity below threshold.  Should NOT be
                                    flagged as a Homer redaction.
        ``"light_on_dark"``       – all colour channels > 0.7.  Intentional
                                    light text on dark background.
        ``"dark_on_dark"``        – text colour channels all < 0.2 under a
                                    dark rect.  Consistent with hidden text.
        ``"normal"``              – no special classification; continue with
                                    other checks.
    """
    overlapping = _spans_overlapping_rect(text_spans, rect)
    if not overlapping:
        return "normal"

    invisible_count = 0
    light_count = 0
    dark_count = 0
    total = len(overlapping)

    for span in overlapping:
        # Render mode 3 = invisible text (OCR layer)
        if span.get("type") == 3 and config.skip_invisible_text:
            invisible_count += 1
            continue

        # Transparent text
        opacity = span.get("opacity", 1.0)
        if opacity < config.transparency_thresh and config.skip_transparent_text:
            invisible_count += 1
            continue

        r, g, b = span.get("color", (0.0, 0.0, 0.0))

        # Light text (all channels > 0.7)
        if r > 0.7 and g > 0.7 and b > 0.7:
            light_count += 1
        # Dark text (all channels < 0.2)
        elif r < 0.2 and g < 0.2 and b < 0.2:
            dark_count += 1

    # Majority rules
    if invisible_count == total:
        return "invisible_by_design"
    if invisible_count + light_count == total and light_count > 0:
        return "light_on_dark"
    if light_count > total / 2:
        return "light_on_dark"
    if dark_count > total / 2:
        return "dark_on_dark"
    return "normal"


def _spans_overlapping_rect(
    text_spans: List[Dict[str, Any]],
    rect: fitz.Rect,
) -> List[Dict[str, Any]]:
    """Return text_spans whose bbox overlaps *rect*."""
    result = []
    for span in text_spans:
        sx0, sy0, sx1, sy1 = span["bbox"]
        if sx1 <= rect.x0 or sx0 >= rect.x1 or sy1 <= rect.y0 or sy0 >= rect.y1:
            continue
        result.append(span)
    return result
