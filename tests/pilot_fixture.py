"""Deterministic, model-free video fixture for pilot boundary tests.

The fixture draws a moving rectangle and a fixed vertical line.  It is kept in
tests so integration tests can exercise OpenCV metadata/capture and source clip
plumbing without camera hardware or inference.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def make_crossing_video(path: str | Path, *, frames: int = 24, fps: float = 12.0, width: int = 160, height: int = 96) -> Path:
    if frames < 2 or fps <= 0:
        raise ValueError("frames must be >= 2 and fps must be positive")
    output = Path(path)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create fixture video")
    try:
        for index in range(frames):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, width // 2 - 1 : width // 2 + 1] = (0, 180, 255)
            x = 8 + int((width - 24) * index / (frames - 1))
            frame[height // 2 - 8 : height // 2 + 8, x : x + 16] = (255, 255, 255)
            writer.write(frame)
    finally:
        writer.release()
    return output
