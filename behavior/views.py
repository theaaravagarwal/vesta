"""Optional context-and-detail views; boxes guide framing, never event labels."""

from __future__ import annotations

import math
from pathlib import Path


def focus_bounds(observations: list[dict]) -> tuple[float, float, float, float] | None:
    """A stable normalized crop enclosing all observed people in this window."""
    boxes = []
    for item in observations:
        box = item.get("normalized_box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            continue
        x1, y1, x2, y2 = [min(1.0, max(0.0, v)) for v in box]
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))
    if not boxes:
        return None
    x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    # Keep nearby walls/doors and hands visible, with one crop for the whole window.
    padx, pady = max(0.06, (x2 - x1) * 0.35), max(0.06, (y2 - y1) * 0.25)
    bounds = max(0, x1 - padx), max(0, y1 - pady), min(1, x2 + padx), min(1, y2 + pady)
    if (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) > 0.65:
        return None  # Widely separated tracks: retaining the original view is clearer.
    return bounds


def context_detail_frames(frames: list[Path], observations: list[dict]) -> list[Path]:
    """Create 768x384 panels: full context left, stable enlarged region right."""
    bounds = focus_bounds(observations)
    if bounds is None:
        return frames
    import cv2
    import numpy as np

    def panel(image):
        h, w = image.shape[:2]
        scale = min(384 / w, 384 / h)
        resized = cv2.resize(
            image, (max(1, round(w * scale)), max(1, round(h * scale)))
        )
        canvas = np.zeros((384, 384, 3), dtype=np.uint8)
        rh, rw = resized.shape[:2]
        canvas[
            (384 - rh) // 2 : (384 - rh) // 2 + rh,
            (384 - rw) // 2 : (384 - rw) // 2 + rw,
        ] = resized
        return canvas

    out = []
    for path in frames:
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError("could not decode focus-view frame")
        h, w = image.shape[:2]
        x1, y1, x2, y2 = bounds
        region = image[
            int(y1 * h) : max(int(y1 * h) + 1, int(y2 * h)),
            int(x1 * w) : max(int(x1 * w) + 1, int(x2 * w)),
        ]
        combined = np.hstack((panel(image), panel(region)))
        target = path.with_name(path.stem + "-focus.jpg")
        if not cv2.imwrite(str(target), combined):
            raise RuntimeError("could not prepare focus-view frame")
        out.append(target)
    return out
