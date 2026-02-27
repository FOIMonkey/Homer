"""Entry point: python -m homer [flags]"""

import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any

from .cli import build_parser, config_from_args
from .config import HomerConfig
from .detector import TrueHomerDetector
from .fixer import fix_homer_pages
from .output import CSVWriter, ProgressReporter, CheckpointManager, print_forensic
from .worker import worker_analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("homer")

# Silence MuPDF warning spam
try:
    import fitz
    fitz.TOOLS.mupdf_display_errors(False)
except Exception:
    pass


def main():
    parser = build_parser()
    args = parser.parse_args()
    config = config_from_args(args)

    no_text = args.no_text

    # ---- Single-file mode ----
    if args.single_file:
        det = TrueHomerDetector(config)
        result = det.analyze_document(args.single_file, verbose=True)
        print_forensic(result, no_text=no_text)

        if args.fix and result.get("has_homer_redaction"):
            fix_dir = Path(args.fix_dir)
            fix_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(fix_dir / Path(args.single_file).name)
            fix_result = fix_homer_pages(
                args.single_file, result["homer_pages"], output_path, config
            )
            if fix_result["status"] == "fixed":
                print(f"\nFixed PDF -> {fix_result['output']}")
                print(f"  Pages flattened: {fix_result['pages_fixed']}")
            else:
                print(f"\nFix failed: {fix_result.get('error', 'unknown')}")

        sys.exit(0 if result.get("status") == "processed" else 1)

    # ---- Batch mode ----
    pdfs = sorted(p for p in Path(args.directory).glob("*.pdf") if not p.is_symlink())
    total = len(pdfs)
    if total == 0:
        print(f"No PDF files found in {args.directory}")
        sys.exit(0)

    # Checkpoint / resume
    ckpt = None
    if args.resume:
        ckpt = CheckpointManager(config.checkpoint_file, config.checkpoint_interval)
        before = len(pdfs)
        pdfs = [p for p in pdfs if not ckpt.is_done(p.name)]
        skipped = before - len(pdfs)
        if skipped:
            print(f"Resuming: skipping {skipped} already-processed files")
        total = len(pdfs)
        if total == 0:
            print("All files already processed.")
            sys.exit(0)

    # Fix mode setup
    fix_dir = None
    files_to_fix = []  # Collect Homer files; fix after detection finishes
    if args.fix:
        fix_dir = Path(args.fix_dir)
        fix_dir.mkdir(parents=True, exist_ok=True)

    progress = ProgressReporter(total)
    print(f"Processing {total} files with {args.workers} worker(s)...")

    # Determine CSV open mode: append if resuming
    csv_mode = "a" if args.resume else "w"

    with open(args.output, csv_mode, newline="", encoding="utf-8") as f_out:
        csv_writer = CSVWriter(f_out, no_text=no_text) if csv_mode == "w" else None
        if csv_mode == "a":
            # For append mode, write rows directly (header already present)
            import csv as _csv
            from .output import CSV_FIELDNAMES, result_to_row
            row_writer = _csv.DictWriter(f_out, fieldnames=CSV_FIELDNAMES)
            # Write header if file is empty
            if f_out.tell() == 0:
                row_writer.writeheader()
        else:
            row_writer = None

        def _write_result(result):
            if csv_writer:
                csv_writer.write(result)
            elif row_writer:
                row_writer.writerow(result_to_row(result, no_text=no_text))

        def _classify(result):
            if result["status"] != "processed":
                return "ERROR"
            if result["has_homer_redaction"]:
                return "HOMER"
            return "CLEAN"

        def _collect_for_fix(pdf_path, result):
            if fix_dir and result.get("has_homer_redaction"):
                files_to_fix.append((str(pdf_path), result["homer_pages"]))

        if args.workers <= 1:
            det = TrueHomerDetector(config)
            for pdf in pdfs:
                result = det.analyze_document(str(pdf))
                _write_result(result)
                progress.tick(result.get("filename", pdf.name), _classify(result))
                _collect_for_fix(pdf, result)
                if ckpt:
                    ckpt.mark_done(pdf.name)
        else:
            config_dict = {
                k: getattr(config, k) for k in config.__dataclass_fields__
            }
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                future_map = {
                    ex.submit(worker_analyze, str(p), config_dict): p for p in pdfs
                }
                for fut in as_completed(future_map):
                    pdf = future_map[fut]
                    try:
                        result = fut.result()
                    except Exception as e:
                        logger.debug(f"Worker error for {pdf}: {e}")
                        result = {"filename": pdf.name, "status": "error",
                                  "error": TrueHomerDetector._sanitize_error(str(e))}

                    _write_result(result)
                    progress.tick(result.get("filename", pdf.name), _classify(result))
                    _collect_for_fix(pdf, result)
                    if ckpt:
                        ckpt.mark_done(pdf.name)

    if ckpt:
        ckpt.save()

    print(f"\nDone. CSV -> {args.output}")

    # Fix phase: runs after all detection is complete
    if files_to_fix:
        # Skip already-fixed files (non-empty output exists)
        to_fix = []
        skipped_fix = 0
        for pdf_path, homer_pages in files_to_fix:
            output_path = fix_dir / Path(pdf_path).name
            if output_path.exists() and output_path.stat().st_size > 0:
                skipped_fix += 1
            else:
                to_fix.append((pdf_path, homer_pages))

        if skipped_fix:
            print(f"\nSkipping {skipped_fix} already-fixed file(s)")

        if not to_fix:
            print("All files already fixed.")
        else:
            fix_n = config.fix_workers
            if fix_n == 0:
                fix_n = args.workers
            fix_n = min(fix_n, len(to_fix))

            print(f"\nFixing {len(to_fix)} file(s) with {fix_n} worker(s)...")
            fixed_count = 0
            failed_count = 0
            t0 = time.monotonic()

            def _do_fix(item):
                pdf_path, homer_pages = item
                output_path = str(fix_dir / Path(pdf_path).name)
                return fix_homer_pages(pdf_path, homer_pages, output_path, config)

            with ThreadPoolExecutor(max_workers=fix_n) as tex:
                futures = {tex.submit(_do_fix, item): item for item in to_fix}
                for i, fut in enumerate(as_completed(futures), 1):
                    pdf_path = futures[fut][0]
                    name = Path(pdf_path).name
                    try:
                        fix_result = fut.result()
                    except Exception as e:
                        failed_count += 1
                        print(f"  [{i}/{len(to_fix)}] Failed: {name} ({e})")
                        continue
                    if fix_result["status"] == "fixed":
                        fixed_count += 1
                        print(f"  [{i}/{len(to_fix)}] Fixed: {name} (pages {fix_result['pages_fixed']})")
                    else:
                        failed_count += 1
                        print(f"  [{i}/{len(to_fix)}] Failed: {name} ({fix_result.get('error', 'unknown')})")

            elapsed = int(time.monotonic() - t0)
            print(f"Fix complete: {fixed_count} fixed, {failed_count} failed ({elapsed}s) -> {fix_dir}/")


if __name__ == "__main__":
    main()
