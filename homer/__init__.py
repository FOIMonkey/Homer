"""Homer Redaction Detector -- find hidden text under PDF redaction boxes."""

__version__ = "2.0.0"

from .config import HomerConfig
from .detector import TrueHomerDetector, RedactionHit

__all__ = ["HomerConfig", "TrueHomerDetector", "RedactionHit", "__version__"]
