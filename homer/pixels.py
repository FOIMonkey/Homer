"""NumPy-accelerated pixel analysis for darkness detection and raster fallback."""

import logging
from typing import List, Dict, Any

import fitz

from .config import HomerConfig

logger = logging.getLogger("homer")

# Try NumPy for vectorised pixel ops (100-1000x speedup)
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    HAS_NUMPY = False

# Optional PIL for raster fallback
try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None


# ------------------------------------------------------------------
# Dark ratio (standalone function -- fixes bug #1: no more self=None)
# ------------------------------------------------------------------
def dark_ratio_of_clip(
    page: fitz.Page, rect: fitz.Rect, config: HomerConfig
) -> float:
    """Fraction of pixels in *rect* that are dark. Uses NumPy when available."""
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return 0.0
    try:
        pix = page.get_pixmap(
            matrix=fitz.Matrix(config.zoom_for_clips, config.zoom_for_clips),
            clip=rect, alpha=False,
        )
        if pix.width == 0 or pix.height == 0:
            return 0.0

        thresh = config.black_rgb_thresh

        if HAS_NUMPY:
            arr = np.frombuffer(pix.samples, dtype=np.uint8)
            n = pix.n
            total = pix.width * pix.height
            if n == 1:
                dark = int(np.count_nonzero(arr < thresh))
            else:
                arr = arr.reshape(-1, n)
                dark = int(np.count_nonzero(np.all(arr[:, :3] < thresh, axis=1)))
            return dark / max(total, 1)
        else:
            return _dark_ratio_pure_python(pix, thresh)
    except Exception:
        return 0.0


def _dark_ratio_pure_python(pix, thresh: int) -> float:
    """Fallback when NumPy is unavailable."""
    samples = pix.samples
    n = pix.n
    total = pix.width * pix.height
    blackish = 0
    if n == 1:
        for i in range(len(samples)):
            if samples[i] < thresh:
                blackish += 1
    else:
        for i in range(0, len(samples), n):
            r = samples[i]
            g = samples[i + 1] if i + 1 < len(samples) else r
            b = samples[i + 2] if i + 2 < len(samples) else r
            if r < thresh and g < thresh and b < thresh:
                blackish += 1
    return blackish / max(total, 1)


# ------------------------------------------------------------------
# Light-text detection (accuracy #1: false positive on dark UI)
# ------------------------------------------------------------------
def text_is_light_colored(
    page: fitz.Page, rect: fitz.Rect, config: HomerConfig
) -> bool:
    """Return True if the majority of text spans under *rect* use light colours.

    Light text on a dark background is intentional styling, not hidden text.
    """
    try:
        data = page.get_text("dict", clip=rect, flags=fitz.TEXT_PRESERVE_WHITESPACE)
    except Exception:
        return False

    light_count = 0
    total_count = 0
    thresh = config.light_color_thresh

    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                total_count += 1
                color_int = span.get("color", 0)
                r = (color_int >> 16) & 0xFF
                g = (color_int >> 8) & 0xFF
                b = color_int & 0xFF
                if r > thresh and g > thresh and b > thresh:
                    light_count += 1

    if total_count == 0:
        return False
    return (light_count / total_count) > 0.5


# ------------------------------------------------------------------
# Raster dark-region finder (performance #3: NumPy array ops)
# ------------------------------------------------------------------
def raster_find_dark_regions(
    page: fitz.Page, config: HomerConfig
) -> List[Dict[str, Any]]:
    """Render page at low res and find large dark rectangular regions."""
    if PILImage is None:
        return []
    try:
        m = fitz.Matrix(config.raster_scale, config.raster_scale)
        pix = page.get_pixmap(matrix=m, alpha=False)
        if pix.width == 0 or pix.height == 0:
            return []

        gx = max(config.raster_grid_min, pix.width // config.raster_grid_divisor)
        gy = max(config.raster_grid_min, pix.height // config.raster_grid_divisor)

        page_w = float(page.rect.width)
        page_h = float(page.rect.height)
        sx = page_w / gx
        sy = page_h / gy
        min_area = page.rect.get_area() * config.min_rect_area_ratio

        if HAS_NUMPY:
            candidates = _raster_dark_numpy(pix, gx, gy, sx, sy, min_area, page, config)
        else:
            candidates = _raster_dark_pil(pix, gx, gy, sx, sy, min_area, page, config)

        return _merge_rects(candidates)
    except Exception:
        return []


def _raster_dark_numpy(pix, gx, gy, sx, sy, min_area, page, config):
    """NumPy-accelerated raster grid scanning."""
    img_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    # Resize to grid via simple block averaging
    bh = pix.height // gy
    bw = pix.width // gx
    if bh == 0 or bw == 0:
        return []

    # Crop to exact multiples
    cropped = img_arr[:bh * gy, :bw * gx, :3]
    # Reshape into grid blocks and take mean
    grid = cropped.reshape(gy, bh, gx, bw, 3).mean(axis=(1, 3))
    # A cell is dark if all channels < threshold
    dark_grid = np.all(grid < config.black_rgb_thresh, axis=2)

    candidates = []
    visited = np.zeros((gy, gx), dtype=bool)

    for y in range(gy):
        for x in range(gx):
            if dark_grid[y, x] and not visited[y, x]:
                # Flood-fill to find rectangular extent
                x_end = x
                while x_end < gx and dark_grid[y, x_end] and not visited[y, x_end]:
                    x_end += 1
                y_end = y + 1
                while y_end < gy:
                    if all(dark_grid[y_end, xx] and not visited[y_end, xx] for xx in range(x, x_end)):
                        y_end += 1
                    else:
                        break
                for yy in range(y, y_end):
                    for xx in range(x, x_end):
                        visited[yy, xx] = True

                rect = fitz.Rect(x * sx, y * sy, x_end * sx, y_end * sy)
                if rect.get_area() >= min_area:
                    dr = dark_ratio_of_clip(page, rect, config)
                    if dr >= config.dark_ratio_thresh:
                        candidates.append({"rect": rect, "source": "raster", "dark_ratio": dr})

    return candidates


def _raster_dark_pil(pix, gx, gy, sx, sy, min_area, page, config):
    """PIL-based fallback raster grid scanning."""
    img = PILImage.frombytes("RGB", (pix.width, pix.height), pix.samples)
    small = img.resize((gx, gy), PILImage.BILINEAR).convert("L")
    bw = small.point(lambda v: 255 if v < config.black_rgb_thresh else 0, mode="1")

    candidates = []
    visited = [[False] * gx for _ in range(gy)]

    for y in range(gy):
        for x in range(gx):
            if bw.getpixel((x, y)) == 255 and not visited[y][x]:
                x_end = x
                while x_end < gx and bw.getpixel((x_end, y)) == 255 and not visited[y][x_end]:
                    x_end += 1
                y_end = y + 1
                while y_end < gy:
                    if all(bw.getpixel((xx, y_end)) == 255 and not visited[y_end][xx] for xx in range(x, x_end)):
                        y_end += 1
                    else:
                        break
                for yy in range(y, y_end):
                    for xx in range(x, x_end):
                        visited[yy][xx] = True

                rect = fitz.Rect(x * sx, y * sy, x_end * sx, y_end * sy)
                if rect.get_area() >= min_area:
                    dr = dark_ratio_of_clip(page, rect, config)
                    if dr >= config.dark_ratio_thresh:
                        candidates.append({"rect": rect, "source": "raster", "dark_ratio": dr})

    return candidates


def _merge_rects(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge overlapping rectangles."""
    merged: List[Dict[str, Any]] = []
    for c in candidates:
        r = c["rect"]
        merged_any = False
        for m in merged:
            if r.intersects(m["rect"]):
                m["rect"] = m["rect"] | r
                m["dark_ratio"] = max(m["dark_ratio"], c["dark_ratio"])
                merged_any = True
                break
        if not merged_any:
            merged.append(dict(c))
    return merged
