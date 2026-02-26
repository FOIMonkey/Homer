"""Order-aware text similarity using SequenceMatcher."""

from difflib import SequenceMatcher


def normalize_text(s: str) -> str:
    return " ".join(s.lower().split())


def text_similarity(a: str, b: str) -> float:
    """Order-aware similarity using SequenceMatcher (replaces lossy Jaccard)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    na, nb = normalize_text(a), normalize_text(b)
    if not na and not nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
