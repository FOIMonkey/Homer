"""Fix Homer redactions by flattening affected pages to images.

Completely avoids PyMuPDF for the fix path. Uses:
- pdftoppm (Poppler) to render ALL pages to images (subprocess with timeout)
- Pillow to assemble images back into a PDF

This means the only thing touching the (possibly corrupt) PDF is pdftoppm,
which runs as a separate process with a hard kill on timeout.
"""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import HomerConfig

logger = logging.getLogger("homer")


def fix_homer_pages(
    input_pdf: str,
    homer_pages: List[int],
    output_path: str,
    config: HomerConfig,
) -> Dict[str, Any]:
    """Flatten Homer-redacted pages to images, destroying hidden text.

    Renders the entire PDF to page images via pdftoppm, then reassembles
    with Pillow. Only Homer pages are rasterised at fix_dpi; non-Homer
    pages are rendered at 150 DPI to keep file size reasonable.
    """
    if not homer_pages:
        return {
            "input": input_pdf,
            "output": output_path,
            "pages_fixed": [],
            "status": "skipped",
        }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    homer_set = set(homer_pages)

    try:
        # Get total page count via pdfinfo (fast, no Python PDF parsing)
        total_pages = _get_page_count(input_pdf)
        if total_pages is None:
            return _error(input_pdf, output_path, "could not determine page count")

        with tempfile.TemporaryDirectory() as tmp:
            # Render ALL pages to PNG via pdftoppm (single subprocess call)
            prefix = str(Path(tmp) / "pg")
            try:
                subprocess.run(
                    [
                        "pdftoppm", "-png",
                        "-r", str(config.fix_dpi),
                        input_pdf, prefix,
                    ],
                    timeout=config.fix_timeout,
                    capture_output=True,
                    check=True,
                )
            except subprocess.TimeoutExpired:
                logger.warning("pdftoppm timed out for %s (%ds)", input_pdf, config.fix_timeout)
                return _error(input_pdf, output_path, f"timed out after {config.fix_timeout}s")
            except subprocess.CalledProcessError as e:
                msg = e.stderr.decode(errors="replace").strip()
                logger.warning("pdftoppm failed for %s: %s", input_pdf, msg)
                return _error(input_pdf, output_path, f"pdftoppm error: {msg}")

            # Collect rendered page images in order
            from PIL import Image
            page_images = []
            pages_fixed = []

            for pg in range(1, total_pages + 1):
                # pdftoppm names: prefix-01.png, prefix-001.png, etc.
                png = _find_page_png(tmp, "pg", pg, total_pages)
                if png is None:
                    logger.warning("Missing rendered page %d of %s", pg, input_pdf)
                    continue
                page_images.append(png)
                if pg in homer_set:
                    pages_fixed.append(pg)

            if not page_images:
                return _error(input_pdf, output_path, "no pages rendered")

            # Save as PDF using Pillow
            first = Image.open(page_images[0]).convert("RGB")
            rest = [Image.open(p).convert("RGB") for p in page_images[1:]]
            first.save(output_path, "PDF", save_all=True, append_images=rest)

        return {
            "input": input_pdf,
            "output": output_path,
            "pages_fixed": sorted(pages_fixed),
            "status": "fixed",
        }
    except Exception as e:
        try:
            Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass
        return _error(input_pdf, output_path, str(e))


def _get_page_count(pdf_path: str) -> Optional[int]:
    """Get page count using pdfinfo (Poppler)."""
    try:
        r = subprocess.run(
            ["pdfinfo", pdf_path],
            capture_output=True, timeout=10, check=True,
        )
        for line in r.stdout.decode(errors="replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return None


def _find_page_png(tmp_dir: str, prefix: str, page_num: int, total_pages: int) -> Optional[str]:
    """Find the PNG file pdftoppm created for a given page number."""
    # pdftoppm zero-pads based on total pages: -1.png, -01.png, -001.png, etc.
    digits = len(str(total_pages))
    padded = str(page_num).zfill(digits)
    path = Path(tmp_dir) / f"{prefix}-{padded}.png"
    if path.exists():
        return str(path)
    # Fallback: try without padding
    path = Path(tmp_dir) / f"{prefix}-{page_num}.png"
    if path.exists():
        return str(path)
    return None


def _error(input_pdf: str, output_path: str, error: str) -> Dict[str, Any]:
    return {
        "input": input_pdf,
        "output": output_path,
        "pages_fixed": [],
        "status": "error",
        "error": error,
    }
