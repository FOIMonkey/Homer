"""Multi-source rectangle detection: drawings, annotations, XObjects, raster fallback."""

import logging
from typing import List, Dict, Any

import fitz

from .config import HomerConfig
from .pixels import dark_ratio_of_clip, raster_find_dark_regions

logger = logging.getLogger("homer")


def _page_has_fullpage_image(page: fitz.Page) -> bool:
    """Return True if the page has an image covering >50% of its area (scanned page)."""
    page_area = page.rect.get_area()
    try:
        for img_info in page.get_images(full=True):
            try:
                for r in page.get_image_rects(img_info[0]):
                    if not r.is_empty and r.get_area() > page_area * MAX_XOBJECT_PAGE_RATIO:
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def collect_candidate_rects(
    page: fitz.Page, config: HomerConfig
) -> List[Dict[str, Any]]:
    """Collect all dark rectangular regions from multiple sources."""
    page_area = page.rect.get_area()
    min_area = page_area * config.min_rect_area_ratio
    candidates: List[Dict[str, Any]] = []

    is_scan = _page_has_fullpage_image(page)

    # 1. Filled vector drawings
    candidates.extend(_from_drawings(page, min_area, config))

    # 2. Redaction annotations
    candidates.extend(_from_annotations(page, min_area, config))

    # 3. Image-based overlays (accuracy #2: XObjects) -- skip for scanned pages
    if not is_scan:
        candidates.extend(_from_xobjects(page, min_area, config))

    # 4. Raster fallback if nothing found -- skip for scanned pages
    #    On scanned pages, dark regions are part of the image itself and the
    #    OCR text layer often has inaccurate positions (especially with rotation),
    #    leading to false positives.
    if not candidates and not is_scan:
        raster_cands = raster_find_dark_regions(page, config)
        for rc in raster_cands:
            if not any(rc["rect"].intersects(c["rect"]) for c in candidates):
                candidates.append(rc)

    return candidates


def _from_drawings(
    page: fitz.Page, min_area: float, config: HomerConfig
) -> List[Dict[str, Any]]:
    results = []
    try:
        drawings = page.get_drawings()
        for d in drawings:
            if d.get("type") != "f":
                continue
            for item in d.get("items", []):
                if not item or item[0] != "re":
                    continue
                r = fitz.Rect(item[1])
                if r.get_area() < min_area:
                    continue
                dr = dark_ratio_of_clip(page, r, config)
                if dr >= config.dark_ratio_thresh:
                    results.append({"rect": r, "source": "drawing", "dark_ratio": dr})
    except Exception as e:
        logger.debug(f"get_drawings() issue: {e}")
    return results


def _from_annotations(
    page: fitz.Page, min_area: float, config: HomerConfig
) -> List[Dict[str, Any]]:
    results = []
    try:
        annots = page.annots()
        if annots:
            for a in annots:
                if a.type[1] == "Redact":
                    r = a.rect
                    if r.get_area() >= min_area:
                        dr = dark_ratio_of_clip(page, r, config)
                        results.append({"rect": r, "source": "annotation", "dark_ratio": dr})
    except Exception:
        pass
    return results


MAX_XOBJECT_PAGE_RATIO = 0.5  # Skip images covering >50% of page (scanned pages)


def _from_xobjects(
    page: fitz.Page, min_area: float, config: HomerConfig
) -> List[Dict[str, Any]]:
    """Detect opaque image overlays (accuracy #2: image-based redactions).

    Skips images that cover more than 50% of the page area, since those
    are full-page scans rather than redaction overlays.
    """
    results = []
    page_area = page.rect.get_area()
    try:
        images = page.get_images(full=True)
        for img_info in images:
            xref = img_info[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue
            for r in rects:
                if r.is_empty or r.get_area() < min_area:
                    continue
                if r.get_area() > page_area * MAX_XOBJECT_PAGE_RATIO:
                    continue  # Full-page scan, not a redaction overlay
                dr = dark_ratio_of_clip(page, r, config)
                if dr >= config.dark_ratio_thresh:
                    results.append({"rect": r, "source": "xobject", "dark_ratio": dr})
    except Exception as e:
        logger.debug(f"XObject image check issue: {e}")
    return results
