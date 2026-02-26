"""TrueHomerDetector -- core orchestrator for Homer redaction detection."""

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import fitz

from .config import HomerConfig
from .pdf_utils import open_pdf_robust, load_page_safe, get_words_safe
from .pixels import dark_ratio_of_clip, text_is_light_colored
from .rects import collect_candidate_rects
from .similarity import text_similarity

logger = logging.getLogger("homer")

# Optional OCR
try:
    from PIL import Image
    import pytesseract
except Exception:
    Image = None
    pytesseract = None


@dataclass
class RedactionHit:
    """Forensic detail for a single detected redaction."""
    rect: tuple  # (x0, y0, x1, y1)
    source: str
    dark_ratio: float
    confidence: float
    hidden_words: List[str]
    reason: str


class TrueHomerDetector:
    def __init__(self, config: Optional[HomerConfig] = None):
        self.config = config or HomerConfig()
        self.processed_count = 0
        self.error_count = 0
        self.homer_redaction_count = 0
        self.visual_only_count = 0
        self._ocr_warned = False

    def _warn_ocr_once(self):
        """Log OCR availability warning once at startup (feature #4)."""
        if not self._ocr_warned:
            self._ocr_warned = True
            if not (Image and pytesseract):
                logger.warning(
                    "OCR unavailable (PIL/pytesseract not installed). "
                    "Detection will use darkness-only mode -- accuracy may be reduced."
                )

    # ------------------------------------------------------------------
    # Coverage ratio (accuracy #4: partial overlap instead of 100% containment)
    # ------------------------------------------------------------------
    @staticmethod
    def _coverage_ratio(outer: fitz.Rect, inner: fitz.Rect) -> float:
        """Fraction of *inner* area that overlaps with *outer*."""
        ix0 = max(outer.x0, inner.x0)
        iy0 = max(outer.y0, inner.y0)
        ix1 = min(outer.x1, inner.x1)
        iy1 = min(outer.y1, inner.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        inter = (ix1 - ix0) * (iy1 - iy0)
        word_area = inner.get_area()
        if word_area <= 0:
            return 0.0
        return inter / word_area

    # ------------------------------------------------------------------
    # OCR inside a rect
    # ------------------------------------------------------------------
    def _ocr_text_in_rect(self, page: fitz.Page, rect: fitz.Rect) -> Optional[str]:
        if not (Image and pytesseract):
            return None
        try:
            dpi = self.config.ocr_dpi
            m = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = page.get_pixmap(matrix=m, clip=rect, alpha=False)
            if pix.width == 0 or pix.height == 0:
                return ""
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            txt = pytesseract.image_to_string(img, config="--psm 6")
            return txt.strip()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Check a single candidate rect for hidden text
    # ------------------------------------------------------------------
    def _check_candidate(
        self,
        page: fitz.Page,
        cand: Dict[str, Any],
        page_words: list,
    ) -> Optional[RedactionHit]:
        rect = cand["rect"]

        # Accuracy #1: skip if text under rect is light-colored (dark UI, not redaction)
        if text_is_light_colored(page, rect, self.config):
            return None

        # Find words overlapping this rect (accuracy #4: coverage ratio)
        inside_words = []
        for w in page_words:
            wrect = fitz.Rect(w[0], w[1], w[2], w[3])
            if self._coverage_ratio(rect, wrect) >= self.config.word_coverage_thresh:
                inside_words.append(w[4])

        if not inside_words:
            return None

        direct_text = " ".join(inside_words).strip()

        # OCR check
        ocr_text = self._ocr_text_in_rect(page, rect)
        if ocr_text is not None:
            sim = text_similarity(direct_text, ocr_text)
            if sim >= self.config.similarity_thresh:
                return None  # Text is visible in OCR -- not hidden
            reason = f"pdf_text_not_in_ocr (sim={sim:.2f})"
            confidence = min(1.0, cand["dark_ratio"] * (1.0 - sim))
        else:
            # No OCR -- rely on darkness
            dr = cand["dark_ratio"]
            if dr >= self.config.dark_ratio_thresh:
                reason = f"uniform_dark_box_no_ocr (dark={dr:.2f})"
                confidence = dr
            else:
                return None

        # Filter junk: require at least N substantive words (not just
        # punctuation, single characters, or dashes)
        substantive = [w for w in inside_words if len(w) > 1 and not all(
            c in "-–—.,;:!?'/()[]" for c in w
        )]
        if len(substantive) < self.config.min_substantive_words:
            return None

        return RedactionHit(
            rect=(rect.x0, rect.y0, rect.x1, rect.y1),
            source=cand["source"],
            dark_ratio=cand["dark_ratio"],
            confidence=confidence,
            hidden_words=inside_words,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Per-page analysis (performance #2: page-level word caching)
    # ------------------------------------------------------------------
    def analyze_page(self, doc: fitz.Document, page_num: int) -> Dict[str, Any]:
        page = load_page_safe(doc, page_num)
        if page is None:
            return {
                "page_num": page_num + 1,
                "has_visual_redaction": False,
                "has_hidden_text": False,
                "is_homer_redaction": False,
                "hidden_words": [],
                "visual_blocks_count": 0,
                "classification": "SKIPPED_BAD_PAGE",
                "hits": [],
            }

        candidates = collect_candidate_rects(page, self.config)
        page_words = get_words_safe(page)  # cached once per page

        has_visual = len(candidates) > 0
        hits: List[RedactionHit] = []

        for cand in candidates:
            try:
                hit = self._check_candidate(page, cand, page_words)
                if hit is not None:
                    hits.append(hit)
            except Exception as e:
                logger.debug(f"Candidate check failed on page {page_num + 1}: {e}")

        has_hidden = len(hits) > 0
        is_homer = has_visual and has_hidden

        classification = (
            "HOMER_REDACTION" if is_homer else
            "PROPER_REDACTION" if has_visual and not has_hidden else
            "CLEAN"
        )

        words_flat = []
        for hit in hits:
            words_flat.extend(hit.hidden_words)

        return {
            "page_num": page_num + 1,
            "has_visual_redaction": has_visual,
            "has_hidden_text": has_hidden,
            "is_homer_redaction": is_homer,
            "hidden_words": words_flat,
            "visual_blocks_count": len(candidates),
            "classification": classification,
            "hits": hits,
        }

    # ------------------------------------------------------------------
    # Error sanitisation (strip absolute paths from messages)
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_error(err: str) -> str:
        """Remove absolute paths from error messages to avoid leaking system info."""
        sanitized = re.sub(r"(/[^\s:]+)", lambda m: os.path.basename(m.group(1)), err)
        return sanitized

    # ------------------------------------------------------------------
    # Document analysis
    # ------------------------------------------------------------------
    def analyze_document(self, pdf_path: str, verbose: bool = False) -> Dict[str, Any]:
        from pathlib import Path
        filename = Path(pdf_path).name
        self._warn_ocr_once()

        # Resource limit: skip files exceeding max size
        if self.config.max_file_mb > 0:
            try:
                size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
                if size_mb > self.config.max_file_mb:
                    self.error_count += 1
                    return {
                        "filename": filename,
                        "status": "error",
                        "error": f"File too large ({size_mb:.0f} MB > {self.config.max_file_mb} MB limit)",
                    }
            except OSError:
                pass

        try:
            with open_pdf_robust(pdf_path) as doc:
                total_pages = len(doc)

                # Resource limit: cap pages analysed
                pages_to_scan = total_pages
                if self.config.max_pages > 0:
                    pages_to_scan = min(total_pages, self.config.max_pages)

                page_results = []
                homer_pages = []
                visual_only_pages = []

                for i in range(pages_to_scan):
                    res = self.analyze_page(doc, i)
                    page_results.append(res)
                    if res["is_homer_redaction"]:
                        homer_pages.append(res["page_num"])
                    elif res["has_visual_redaction"] and not res["has_hidden_text"]:
                        visual_only_pages.append(res["page_num"])
        except Exception as e:
            self.error_count += 1
            logger.debug(f"Error processing {pdf_path}: {e}")
            return {"filename": filename, "status": "error", "error": self._sanitize_error(str(e))}

        has_homer = len(homer_pages) > 0
        has_visual_only = len(visual_only_pages) > 0

        if has_homer:
            self.homer_redaction_count += 1
        elif has_visual_only:
            self.visual_only_count += 1
        self.processed_count += 1

        return {
            "filename": filename,
            "status": "processed",
            "total_pages": total_pages,
            "analyzed_pages": pages_to_scan,
            "has_homer_redaction": has_homer,
            "homer_pages": homer_pages,
            "visual_only_pages": visual_only_pages,
            "page_results": page_results,
        }
