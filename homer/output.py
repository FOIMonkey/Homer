"""CSV writer, progress reporter, checkpoint manager, forensic formatter."""

import csv
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, IO

from .detector import RedactionHit

logger = logging.getLogger("homer")


# ------------------------------------------------------------------
# CSV sanitisation (prevents formula injection in Excel/Sheets)
# ------------------------------------------------------------------
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_csv_cell(val: str) -> str:
    """Prefix dangerous characters so spreadsheets don't interpret them as formulas."""
    if val and val[0] in _FORMULA_PREFIXES:
        return "'" + val
    return val


# ------------------------------------------------------------------
# CSV fieldnames (backward-compatible + new)
# ------------------------------------------------------------------
CSV_FIELDNAMES = [
    "filename", "status", "total_pages", "analyzed_pages",
    "has_homer_redaction", "homer_pages_count", "homer_page_numbers",
    "visual_only_pages_count", "visual_only_page_numbers",
    "hidden_words_by_page",
    "processed_timestamp",
]


def result_to_row(result: Dict[str, Any], no_text: bool = False) -> Dict[str, Any]:
    if result["status"] == "processed":
        if no_text:
            hidden_words_str = ""
        else:
            hidden_map = []
            for pr in result["page_results"]:
                if pr["is_homer_redaction"] and pr["hidden_words"]:
                    hidden_map.append(f"p{pr['page_num']}: {' '.join(pr['hidden_words'])}")
            hidden_words_str = " | ".join(hidden_map)
        return {
            "filename": _sanitize_csv_cell(result["filename"]),
            "status": result["status"],
            "total_pages": result["total_pages"],
            "analyzed_pages": result["analyzed_pages"],
            "has_homer_redaction": result["has_homer_redaction"],
            "homer_pages_count": len(result["homer_pages"]),
            "homer_page_numbers": ",".join(map(str, result["homer_pages"])),
            "visual_only_pages_count": len(result["visual_only_pages"]),
            "visual_only_page_numbers": ",".join(map(str, result["visual_only_pages"])),
            "hidden_words_by_page": _sanitize_csv_cell(hidden_words_str),
            "processed_timestamp": datetime.now().isoformat(),
        }
    return {
        "filename": _sanitize_csv_cell(result.get("filename", "")),
        "status": result.get("status", "error"),
        "total_pages": 0,
        "analyzed_pages": 0,
        "has_homer_redaction": False,
        "homer_pages_count": 0,
        "homer_page_numbers": "",
        "visual_only_pages_count": 0,
        "visual_only_page_numbers": "",
        "hidden_words_by_page": "",
        "processed_timestamp": datetime.now().isoformat(),
    }


class CSVWriter:
    def __init__(self, f: IO[str], no_text: bool = False):
        self.writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        self.writer.writeheader()
        self.no_text = no_text

    def write(self, result: Dict[str, Any]):
        self.writer.writerow(result_to_row(result, no_text=self.no_text))


# ------------------------------------------------------------------
# Progress reporter (feature #2)
# ------------------------------------------------------------------
class ProgressReporter:
    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self._start = time.monotonic()

    def tick(self, filename: str, classification: str):
        self.done += 1
        elapsed = time.monotonic() - self._start
        rate = self.done / max(elapsed, 0.001)
        remaining = (self.total - self.done) / max(rate, 0.001)
        print(
            f"[{self.done}/{self.total}] {filename}: {classification} "
            f"({rate:.1f} docs/sec, ETA: {remaining:.0f}s)"
        )


# ------------------------------------------------------------------
# Checkpoint manager (feature #1)
# ------------------------------------------------------------------
class CheckpointManager:
    def __init__(self, path: str, interval: int = 10):
        self.path = Path(path)
        self.interval = interval
        self._completed: List[str] = []
        self._count_since_save = 0
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self._completed = data.get("completed", [])
            except Exception:
                self._completed = []

    def is_done(self, filename: str) -> bool:
        return filename in self._completed

    def mark_done(self, filename: str):
        self._completed.append(filename)
        self._count_since_save += 1
        if self._count_since_save >= self.interval:
            self.save()
            self._count_since_save = 0

    def save(self):
        try:
            # Atomic write: write to temp file then rename to avoid corruption
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w") as tmp_f:
                    json.dump({"completed": self._completed}, tmp_f)
                os.replace(tmp_path, str(self.path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.warning(f"Checkpoint save failed: {e}")

    @property
    def completed_set(self) -> set:
        return set(self._completed)


# ------------------------------------------------------------------
# Forensic single-file output (feature #3)
# ------------------------------------------------------------------
def print_forensic(result: Dict[str, Any], no_text: bool = False):
    print()
    print("=" * 60)
    print(f"HOMER REDACTION ANALYSIS: {result['filename']}")
    print("=" * 60)

    if result["status"] != "processed":
        print(f"Error: {result.get('error', 'unknown')}")
        return

    if result["has_homer_redaction"]:
        print("HOMER REDACTION DETECTED")
        print(f"  Affected pages: {result['homer_pages']}")
    else:
        print("No homer redaction detected")

    if result["visual_only_pages"]:
        print(f"  Properly redacted pages: {result['visual_only_pages']}")

    for pr in result["page_results"]:
        hits = pr.get("hits", [])
        if not hits:
            continue
        print(f"\n  Page {pr['page_num']}:")
        for hit in hits:
            if isinstance(hit, RedactionHit):
                print(f"    Box: ({hit.rect[0]:.0f},{hit.rect[1]:.0f})-({hit.rect[2]:.0f},{hit.rect[3]:.0f})")
                print(f"    Source: {hit.source}  Dark: {hit.dark_ratio:.2f}  Confidence: {hit.confidence:.2f}")
                if not no_text:
                    print(f"    Hidden words: {hit.hidden_words}")
                print(f"    Reason: {hit.reason}")
            else:
                if not no_text:
                    print(f"    Hidden words: {pr['hidden_words']}")
