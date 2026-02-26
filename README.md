# Homer Redaction Detector

Detect improperly redacted PDFs where text remains recoverable from redaction boxes.

Homer scans PDF documents for dark rectangular regions (redaction boxes) and checks whether the original text is still embedded in the file. When text is found in a visual redaction, this is a **Homer redaction** — the redaction is cosmetic only, and the original content can be recovered by simply selecting and copying the text.

## Quick start

```bash
pip install PyMuPDF

# Optional (recommended):
pip install Pillow pytesseract numpy pikepdf

# Scan a directory of PDFs
python -m homer --directory ./pdfs --output results.csv

# Forensic analysis of a single file
python -m homer --single-file document.pdf
```

## How it works

1. **Rectangle detection** — finds dark rectangular regions from vector drawings, redaction annotations, image overlays (XObjects), and a raster fallback scanner
2. **Text visibility classification** — uses PyMuPDF's text-trace data to filter out OCR-layer invisible text (render mode 3) and intentional light-on-dark styling, avoiding false positives
3. **Z-order analysis** (primary method) — uses the shared `seqno` counter from PyMuPDF's `get_texttrace()` and `get_drawings()` to determine whether text was drawn before or after a dark rectangle in the PDF content stream. Detects both text-underneath and dark-on-dark scenarios (black text drawn on top of a black box)
4. **OCR verification** (fallback) — for ambiguous cases where z-order is inconclusive, runs OCR on the region and compares against the embedded text
5. **Classification** — each page is classified as `HOMER_REDACTION`, `PROPER_REDACTION`, or `CLEAN`

## Usage

### Batch mode (default)

```bash
python -m homer --directory ./pdfs --output results.csv --workers 4
```

Produces a CSV with one row per PDF. Use `--workers` to parallelise across CPU cores.

### Single-file mode

```bash
python -m homer --single-file document.pdf
```

Prints detailed forensic output: every detected redaction box, its coordinates, confidence score, detection source, and the hidden words found.

### Resume from checkpoint

```bash
python -m homer --directory ./pdfs --output results.csv --resume
```

Skips files already processed in a previous run (tracked via `.homer_checkpoint.json`).

## CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--directory` | `.` | Directory of PDFs (batch mode) |
| `--output` | `homer_results.csv` | Output CSV path |
| `--single-file` | — | Analyse one PDF with forensic detail |
| `--workers` | `1` | Parallel worker processes (1–64) |
| `--resume` | off | Resume from checkpoint |
| `--no-text` | off | Omit hidden words from output |
| `--dark-thresh` | `0.80` | Dark-ratio threshold (0.0–1.0) |
| `--black-rgb` | `50` | Black RGB threshold (0–255) |
| `--similarity` | `0.70` | OCR similarity threshold (0.0–1.0) |
| `--coverage` | `0.50` | Word coverage threshold (0.0–1.0) |
| `--max-pages` | unlimited | Maximum pages to analyse per PDF |
| `--max-file-mb` | `500` | Skip PDFs larger than this (MB) |
| `--no-zorder` | off | Disable z-order analysis (fall back to OCR/darkness only) |

## Dependencies

**Required:**
- [PyMuPDF](https://pymupdf.readthedocs.io/) (`pip install PyMuPDF`) — PDF parsing and rendering

**Optional:**
- [Pillow](https://pillow.readthedocs.io/) + [pytesseract](https://github.com/madmaze/pytesseract) — OCR verification (significantly improves accuracy)
- [NumPy](https://numpy.org/) — accelerates pixel analysis (100–1000x speedup)
- [pikepdf](https://pikepdf.readthedocs.io/) — automatic repair of corrupted PDFs
- `qpdf` / `ghostscript` — additional PDF repair fallbacks (system packages)

Install everything at once:

```bash
pip install PyMuPDF Pillow pytesseract numpy pikepdf
```

## CSV output columns

| Column | Description |
|--------|-------------|
| `filename` | PDF filename |
| `status` | `processed` or `error` |
| `total_pages` | Total pages in the document |
| `analyzed_pages` | Pages successfully analysed |
| `has_homer_redaction` | `True` if hidden text was found |
| `homer_pages_count` | Number of affected pages |
| `homer_page_numbers` | Comma-separated list of affected page numbers |
| `visual_only_pages_count` | Pages with proper (non-leaking) redactions |
| `visual_only_page_numbers` | Comma-separated list |
| `hidden_words_by_page` | Recovered text grouped by page |
| `processed_timestamp` | ISO 8601 timestamp |

## License

MIT
