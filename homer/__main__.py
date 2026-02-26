"""Entry point: python -m homer [flags]"""

import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any

from .cli import build_parser, config_from_args
from .config import HomerConfig
from .detector import TrueHomerDetector
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

        if args.workers <= 1:
            det = TrueHomerDetector(config)
            for pdf in pdfs:
                result = det.analyze_document(str(pdf))
                _write_result(result)
                progress.tick(result.get("filename", pdf.name), _classify(result))
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
                    if ckpt:
                        ckpt.mark_done(pdf.name)

    if ckpt:
        ckpt.save()

    print(f"\nDone. CSV -> {args.output}")


if __name__ == "__main__":
    main()
