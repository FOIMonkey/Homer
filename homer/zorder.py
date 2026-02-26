"""Z-order analysis using PyMuPDF's shared seqno counter.

get_texttrace() and get_drawings() both expose a `seqno` reflecting
content-stream rendering order.  If a text span's seqno < a rectangle's
seqno, the text is structurally proven to be drawn *underneath* the
rectangle -- no pixel rendering or OCR needed.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import fitz

logger = logging.getLogger("homer")


def get_text_spans_cached(page: fitz.Page) -> List[Dict[str, Any]]:
    """Return text-trace spans for *page* with seqno, bbox, and rendering info.

    Each returned dict has keys:
        seqno   – content-stream sequence number
        bbox    – (x0, y0, x1, y1) tuple
        type    – text rendering mode (0=fill, 1=stroke, 2=clip, 3=invisible …)
        opacity – span opacity (0.0–1.0)
        color   – (r, g, b) tuple with values 0.0–1.0
    """
    spans: List[Dict[str, Any]] = []
    try:
        trace = page.get_texttrace()
    except Exception:
        return spans

    for item in trace:
        try:
            spans.append({
                "seqno": item.get("seqno"),
                "bbox": tuple(item["bbox"]),
                "type": item.get("type", 0),
                "opacity": item.get("opacity", 1.0),
                "color": _extract_color(item),
            })
        except (KeyError, TypeError):
            continue
    return spans


def _extract_color(item: dict) -> Tuple[float, float, float]:
    """Normalise colour from a texttrace item to (r, g, b) floats in 0-1."""
    c = item.get("color")
    if c is None:
        return (0.0, 0.0, 0.0)
    if isinstance(c, (int, float)):
        v = float(c)
        return (v, v, v)
    if isinstance(c, (list, tuple)):
        if len(c) >= 3:
            return (float(c[0]), float(c[1]), float(c[2]))
        if len(c) == 1:
            v = float(c[0])
            return (v, v, v)
    return (0.0, 0.0, 0.0)


def find_span_for_word(
    text_spans: List[Dict[str, Any]],
    word_bbox: Tuple[float, float, float, float],
) -> Optional[Dict[str, Any]]:
    """Find the texttrace span that best overlaps *word_bbox*.

    Returns the span dict with the largest overlap area, or None.
    """
    wx0, wy0, wx1, wy1 = word_bbox
    word_area = (wx1 - wx0) * (wy1 - wy0)
    if word_area <= 0:
        return None

    best: Optional[Dict[str, Any]] = None
    best_overlap = 0.0

    for span in text_spans:
        sx0, sy0, sx1, sy1 = span["bbox"]
        ix0 = max(wx0, sx0)
        iy0 = max(wy0, sy0)
        ix1 = min(wx1, sx1)
        iy1 = min(wy1, sy1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        overlap = (ix1 - ix0) * (iy1 - iy0)
        if overlap > best_overlap:
            best_overlap = overlap
            best = span

    return best


def check_zorder(
    text_spans: List[Dict[str, Any]],
    rect_seqno: Optional[int],
    inside_word_bboxes: List[Tuple[float, float, float, float]],
) -> str:
    """Determine whether text is structurally underneath or on top of a rectangle.

    Returns:
        ``"text_underneath"`` – all text seqnos < rect seqno (hidden)
        ``"text_on_top"``     – all text seqnos > rect seqno (visible)
        ``"ambiguous"``       – mixed, missing seqno data, or no matches
    """
    if rect_seqno is None or not inside_word_bboxes or not text_spans:
        return "ambiguous"

    underneath = 0
    on_top = 0
    total = 0

    for wbbox in inside_word_bboxes:
        span = find_span_for_word(text_spans, wbbox)
        if span is None or span.get("seqno") is None:
            continue
        total += 1
        if span["seqno"] < rect_seqno:
            underneath += 1
        elif span["seqno"] > rect_seqno:
            on_top += 1

    if total == 0:
        return "ambiguous"
    if underneath == total:
        return "text_underneath"
    if on_top == total:
        return "text_on_top"
    return "ambiguous"
