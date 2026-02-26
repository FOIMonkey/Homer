"""Robust PDF opening, temp cleanup, and safe word extraction."""

import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

import fitz

logger = logging.getLogger("homer")


# ------------------------------------------------------------------
# Temp file tracker (fixes bug #3: temp file leak)
# ------------------------------------------------------------------
class TempFileTracker:
    """Tracks temp files/dirs created during PDF repair and guarantees cleanup."""

    def __init__(self):
        self._paths: List[str] = []

    def register(self, path: str) -> str:
        self._paths.append(path)
        return path

    def cleanup(self):
        for p in self._paths:
            try:
                pp = Path(p)
                if pp.is_dir():
                    shutil.rmtree(pp, ignore_errors=True)
                elif pp.exists():
                    pp.unlink(missing_ok=True)
            except Exception:
                pass
        self._paths.clear()


# ------------------------------------------------------------------
# PDF validation
# ------------------------------------------------------------------
def is_probably_pdf(path: Path, min_bytes: int = 1024) -> bool:
    try:
        if path.stat().st_size < min_bytes:
            return False
        with path.open("rb") as f:
            head = f.read(1024)
        return head.startswith(b"%PDF-") or (b"%PDF-" in head)
    except Exception:
        return False


# ------------------------------------------------------------------
# External repair helpers
# ------------------------------------------------------------------
def _repair_with_pikepdf(src: str, tracker: TempFileTracker) -> Optional[str]:
    try:
        import pikepdf
        with pikepdf.open(src) as pdf:
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            out_path = tmp.name
            tmp.close()
            tracker.register(out_path)
            pdf.save(out_path)
        return out_path
    except Exception:
        return None


def _repair_with_external(src: str, tracker: TempFileTracker) -> Optional[str]:
    tmpdir = tempfile.mkdtemp()
    tracker.register(tmpdir)

    qpdf = shutil.which("qpdf")
    if qpdf:
        qpdf_out = os.path.join(tmpdir, "repaired-qpdf.pdf")
        try:
            proc = subprocess.run(
                [qpdf, "--repair", "--object-streams=preserve", src, qpdf_out],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if proc.returncode == 0:
                return qpdf_out
            if proc.stderr:
                logger.debug("qpdf stderr: %s", proc.stderr.decode(errors="replace").strip())
        except Exception:
            pass

    gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    if gs:
        gs_out = os.path.join(tmpdir, "repaired-gs.pdf")
        try:
            proc = subprocess.run(
                [gs, "-o", gs_out, "-sDEVICE=pdfwrite",
                 "-dPDFSETTINGS=/prepress", "-dCompatibilityLevel=1.7",
                 "-dDetectDuplicateImages=true", "-dCompressFonts=true",
                 "-dNOPAUSE", "-dBATCH", src],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if proc.returncode == 0:
                return gs_out
            if proc.stderr:
                logger.debug("gs stderr: %s", proc.stderr.decode(errors="replace").strip())
        except Exception:
            pass

    return None


# ------------------------------------------------------------------
# Robust PDF open (context manager -- fixes bug #3)
# ------------------------------------------------------------------
@contextmanager
def open_pdf_robust(pdf_path: str):
    """Open a PDF with automatic repair fallback and guaranteed temp cleanup."""
    p = Path(pdf_path)
    if not is_probably_pdf(p):
        raise ValueError(f"Not a valid PDF (header/size check failed): {p.name}")

    tracker = TempFileTracker()
    doc = None
    try:
        try:
            doc = fitz.open(pdf_path)
            yield doc
            return
        except Exception as first_err:
            repaired = _repair_with_pikepdf(str(p), tracker)
            if repaired:
                doc = fitz.open(repaired)
                yield doc
                return

            repaired = _repair_with_external(str(p), tracker)
            if repaired:
                doc = fitz.open(repaired)
                yield doc
                return

            raise first_err
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        tracker.cleanup()


# ------------------------------------------------------------------
# Safe page loading
# ------------------------------------------------------------------
def load_page_safe(doc: fitz.Document, i: int) -> Optional[fitz.Page]:
    try:
        return doc.load_page(i)
    except Exception as e:
        logger.warning(f"Skipping page {i + 1}: {e}")
        return None


# ------------------------------------------------------------------
# Safe word extraction (fixes bug #2 + accuracy #6)
# ------------------------------------------------------------------
def _estimate_word_rects(
    block_rect: fitz.Rect, text: str
) -> List[Tuple[float, float, float, float, str]]:
    """Distribute block width across words proportionally to char count (fixes bug #2)."""
    raw_words = text.split()
    if not raw_words:
        return []

    total_chars = sum(len(w) for w in raw_words)
    if total_chars == 0:
        return []

    results = []
    x_cursor = block_rect.x0
    block_width = block_rect.x1 - block_rect.x0

    for word in raw_words:
        proportion = len(word) / total_chars
        word_width = block_width * proportion
        results.append((
            x_cursor, block_rect.y0,
            x_cursor + word_width, block_rect.y1,
            word,
        ))
        x_cursor += word_width

    return results


def _annotation_words(page: fitz.Page) -> List[Tuple[float, float, float, float, str]]:
    """Extract text from annotations and form fields (accuracy #6)."""
    words = []
    try:
        annots = page.annots()
        if annots:
            for annot in annots:
                content = annot.info.get("content", "")
                if content and content.strip():
                    r = annot.rect
                    for w in content.strip().split():
                        words.append((r.x0, r.y0, r.x1, r.y1, w))
    except Exception:
        pass

    try:
        widgets = page.widgets()
        if widgets:
            for widget in widgets:
                val = widget.field_value
                if val and val.strip():
                    r = widget.rect
                    for w in val.strip().split():
                        words.append((r.x0, r.y0, r.x1, r.y1, w))
    except Exception:
        pass

    return words


def get_words_safe(
    page: fitz.Page,
) -> List[Tuple[float, float, float, float, str]]:
    """Get words with fallbacks; supplements with annotation text."""
    words = []

    try:
        raw = page.get_text("words")
        words = [(w[0], w[1], w[2], w[3], w[4]) for w in raw]
    except Exception:
        try:
            blocks = page.get_text("blocks")
            for b in blocks or []:
                text = b[4] or ""
                if text.strip():
                    block_rect = fitz.Rect(b[:4])
                    words.extend(_estimate_word_rects(block_rect, text))
        except Exception:
            pass

    words.extend(_annotation_words(page))
    return words
