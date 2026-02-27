"""Configuration dataclass for Homer Redaction Detector."""

from dataclasses import dataclass, field


@dataclass
class HomerConfig:
    """All tunable thresholds and settings, centralised."""

    # Darkness detection
    dark_ratio_thresh: float = 0.80
    black_rgb_thresh: int = 50
    zoom_for_clips: float = 2.0

    # OCR
    ocr_dpi: int = 300

    # Rectangle filtering
    min_rect_area_ratio: float = 0.0005

    # Word overlap
    word_coverage_thresh: float = 0.50  # intersection_area / word_area

    # Text similarity
    similarity_thresh: float = 0.70

    # Light-text filter (RGB channel threshold to consider "light")
    light_color_thresh: int = 180

    # Raster fallback grid
    raster_scale: float = 1.5
    raster_grid_divisor: int = 64
    raster_grid_min: int = 8

    # Minimum substantive words to count as a real hit
    # Words that are only punctuation/single chars are not counted
    min_substantive_words: int = 1

    # Resource limits
    max_pages: int = 0          # 0 = unlimited
    max_file_mb: int = 500      # Skip files larger than this (MB)

    # Z-order analysis
    use_zorder: bool = True
    zorder_confidence: float = 0.95

    # Text rendering mode filtering
    skip_invisible_text: bool = True    # Skip render mode 3 (OCR layer)
    skip_transparent_text: bool = True  # Skip opacity < threshold
    transparency_thresh: float = 0.05

    # Fix mode
    fix_dpi: int = 200      # DPI for flattening Homer pages to images
    fix_timeout: int = 120  # Per-file timeout in seconds
    fix_workers: int = 1    # Parallel fix workers (0 = same as --workers)

    # Checkpoint
    checkpoint_interval: int = 10
    checkpoint_file: str = ".homer_checkpoint.json"
