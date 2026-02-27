"""Argument parsing (backward-compatible + new flags)."""

import argparse
import sys

from .config import HomerConfig


def _bounded_int(lo: int, hi: int, name: str):
    """Return an argparse type function that enforces an integer range."""
    def _check(value: str) -> int:
        try:
            iv = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be an integer")
        if iv < lo or iv > hi:
            raise argparse.ArgumentTypeError(f"{name} must be between {lo} and {hi}")
        return iv
    return _check


def _bounded_float(lo: float, hi: float, name: str):
    """Return an argparse type function that enforces a float range."""
    def _check(value: str) -> float:
        try:
            fv = float(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{name} must be a number")
        if fv < lo or fv > hi:
            raise argparse.ArgumentTypeError(f"{name} must be between {lo} and {hi}")
        return fv
    return _check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="homer",
        description="Homer Redaction Detector -- find hidden text under PDF redaction boxes",
    )

    # Existing flags (backward compatible)
    parser.add_argument("--directory", default=".", help="Directory of PDFs (batch mode)")
    parser.add_argument("--output", default="homer_results.csv", help="Output CSV path")
    parser.add_argument("--single-file", help="Analyse one PDF with forensic detail")
    parser.add_argument("--workers", type=_bounded_int(1, 64, "--workers"), default=1,
                        help="Parallel worker processes (1-64, default: 1)")

    # New flags
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint (skip already-processed files)")
    parser.add_argument("--dark-thresh", type=_bounded_float(0.0, 1.0, "--dark-thresh"),
                        default=None,
                        help=f"Dark-ratio threshold 0.0-1.0 (default: {HomerConfig.dark_ratio_thresh})")
    parser.add_argument("--black-rgb", type=_bounded_int(0, 255, "--black-rgb"),
                        default=None,
                        help=f"Black RGB threshold 0-255 (default: {HomerConfig.black_rgb_thresh})")
    parser.add_argument("--similarity", type=_bounded_float(0.0, 1.0, "--similarity"),
                        default=None,
                        help=f"OCR similarity threshold 0.0-1.0 (default: {HomerConfig.similarity_thresh})")
    parser.add_argument("--coverage", type=_bounded_float(0.0, 1.0, "--coverage"),
                        default=None,
                        help=f"Word coverage threshold 0.0-1.0 (default: {HomerConfig.word_coverage_thresh})")
    parser.add_argument("--no-text", action="store_true",
                        help="Omit hidden words from CSV and terminal output")
    parser.add_argument("--max-pages", type=_bounded_int(1, 100000, "--max-pages"),
                        default=None,
                        help="Maximum pages to analyse per PDF (default: unlimited)")
    parser.add_argument("--max-file-mb", type=_bounded_int(1, 10000, "--max-file-mb"),
                        default=None,
                        help="Skip PDFs larger than this size in MB (default: 500)")
    parser.add_argument("--no-zorder", action="store_true",
                        help="Disable z-order analysis (fall back to OCR/darkness only)")

    # Fix mode
    parser.add_argument("--fix", action="store_true",
                        help="Flatten Homer-redacted pages to images, destroying hidden text")
    parser.add_argument("--fix-dir", default="./fixed",
                        help="Output directory for fixed PDFs (default: ./fixed/)")
    parser.add_argument("--fix-dpi", type=_bounded_int(72, 600, "--fix-dpi"),
                        default=None,
                        help=f"DPI for flattening pages 72-600 (default: {HomerConfig.fix_dpi})")
    parser.add_argument("--fix-timeout", type=_bounded_int(10, 3600, "--fix-timeout"),
                        default=None,
                        help=f"Per-file fix timeout in seconds 10-3600 (default: {HomerConfig.fix_timeout})")
    parser.add_argument("--fix-workers", type=_bounded_int(0, 32, "--fix-workers"),
                        default=None,
                        help="Parallel fix workers 0-32, 0 = same as --workers (default: 1)")

    return parser


def config_from_args(args: argparse.Namespace) -> HomerConfig:
    config = HomerConfig()
    if args.dark_thresh is not None:
        config.dark_ratio_thresh = args.dark_thresh
    if args.black_rgb is not None:
        config.black_rgb_thresh = args.black_rgb
    if args.similarity is not None:
        config.similarity_thresh = args.similarity
    if args.coverage is not None:
        config.word_coverage_thresh = args.coverage
    if args.max_pages is not None:
        config.max_pages = args.max_pages
    if args.max_file_mb is not None:
        config.max_file_mb = args.max_file_mb
    if args.no_zorder:
        config.use_zorder = False
    if args.fix_dpi is not None:
        config.fix_dpi = args.fix_dpi
    if args.fix_timeout is not None:
        config.fix_timeout = args.fix_timeout
    if args.fix_workers is not None:
        config.fix_workers = args.fix_workers
    return config
