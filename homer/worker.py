"""Multiprocessing worker function -- must be in an importable module (not __main__)."""

from typing import Dict, Any

from .config import HomerConfig
from .detector import TrueHomerDetector


def worker_analyze(pdf_path: str, config_dict: dict) -> Dict[str, Any]:
    """Each spawned process creates its own detector and analyses one file."""
    config = HomerConfig(**config_dict)
    det = TrueHomerDetector(config)
    result = det.analyze_document(pdf_path)
    # Strip RedactionHit objects (may not pickle cleanly across processes)
    for pr in result.get("page_results", []):
        pr.pop("hits", None)
    return result
