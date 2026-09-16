"""Opt-in paired spatial probe for one short video window.

This module is an offline diagnostic. It does not alter production analysis or
produce benchmark scores; model calls occur only when the CLI is executed (or
when :func:`run_probe` is called explicitly).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path

from behavior import CONFIG_VERSION, SAMPLE_FPS, TemporalAnalyzer
from behavior import _candidate_rejection_reason
from behavior.views import context_detail_frames, focus_bounds


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def validate_probe(start, end, box):
    """Validate the bounded, normalized probe inputs before any inference."""
    start = _number(start, "start")
    end = _number(end, "end")
    if start < 0 or end <= start or end - start > 8:
        raise ValueError("window must satisfy 0 <= start < end and be at most 8 seconds")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError("focus box must contain x1 y1 x2 y2")
    values = [_number(v, "focus box coordinate") for v in box]
    x1, y1, x2, y2 = values
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise ValueError("focus box must satisfy 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
    return start, end, values


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gated(candidates, start=None, end=None):
    rows = []
    for candidate in candidates:
        reason = _invalid_candidate_reason(candidate, start, end)
        if reason is None:
            try:
                reason = _candidate_rejection_reason(candidate)
            except (KeyError, TypeError):
                reason = "invalid_output"
        rows.append({
            "raw": candidate,
            "decision": "rejected" if reason else "accepted",
            "reason": reason,
        })
    return rows


def _invalid_candidate_reason(candidate, window_start=None, window_end=None):
    """Mirror the worker's finite, bounded span checks before lexical gating."""
    if not isinstance(candidate, dict):
        return "invalid_output"
    required = ("start_s", "end_s", "action", "description", "evidence", "uncertainty")
    if any(key not in candidate for key in required):
        return "invalid_output"
    try:
        start = float(candidate["start_s"])
        end = float(candidate["end_s"])
    except (TypeError, ValueError):
        return "invalid_span"
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return "invalid_span"
    if window_start is not None:
        # The worker clamps overlapping spans to the analysis window before
        # checking whether the resulting span is valid.  Keep that behavior
        # here so the probe does not call a production-accepted candidate a
        # rejection merely because it straddles a window boundary.
        start = max(window_start, start)
        end = min(window_end, end)
        if end <= start:
            return "invalid_span"
    if not isinstance(candidate["action"], str) or candidate["action"] not in {
        "climbing", "boundary_entry", "access_interaction", "object_tampering",
        "other_observable_event",
    }:
        return "invalid_output"
    if not isinstance(candidate["description"], str) or not candidate["description"].strip():
        return "invalid_output"
    if (
        not isinstance(candidate["evidence"], list)
        or not candidate["evidence"]
        or any(not isinstance(item, str) or not item.strip() for item in candidate["evidence"])
    ):
        return "invalid_output"
    if not isinstance(candidate["uncertainty"], str):
        return "invalid_output"
    return None


def _variant(analyzer, frames, start, end, tracks):
    started = time.monotonic()
    raw = analyzer.infer(frames, start, end, {}, tracks)
    elapsed = time.monotonic() - started
    if not isinstance(raw, list):
        raise RuntimeError("analyzer returned malformed candidate list")
    return {
        "frame_count": len(frames),
        "raw_candidates": raw,
        "gated_candidates": _gated(raw, start, end),
        "elapsed_s": round(elapsed, 3),
    }


def run_probe(video, start, end, focus_box, output):
    """Run a paired full/focus diagnostic in a fresh temporary frame directory."""
    start, end, focus_box = validate_probe(start, end, focus_box)
    video = Path(video).resolve()
    output = Path(output)
    if not video.is_file():
        raise ValueError(f"video does not exist: {video}")
    output.parent.mkdir(parents=True, exist_ok=True)
    analyzer = TemporalAnalyzer()
    source_checksum = _sha256(video)
    result = {
        "label": "offline paired spatial diagnostic; not a benchmark or production score",
        "source": {"path": str(video), "sha256": source_checksum},
        "window": {"start_s": start, "end_s": end, "duration_s": end - start},
        "fps": SAMPLE_FPS,
        "model": getattr(analyzer, "model_name", None),
        "config_version": CONFIG_VERSION,
        "focus_box": focus_box,
        "focus_bounds": None,
        "variants": {},
    }
    with tempfile.TemporaryDirectory(prefix="vesta-probe-") as temp:
        root = Path(temp)
        duration = analyzer.metadata(video)
        if not math.isfinite(duration) or duration <= 0 or end > duration:
            raise ValueError("window must be within the source duration")
        tracks = analyzer.track_video(video, root / "tracks", duration)
        window_tracks = [item for item in tracks if start <= item.get("time_s", -1) <= end]
        frames = analyzer.frames(video, root / "frames", start, end)
        if not frames:
            result["error"] = "frame extraction produced no frames"
            output.write_text(json.dumps(result, indent=2) + "\n")
            raise RuntimeError(result["error"])
        result["frame_count"] = len(frames)
        result["track_count"] = len(window_tracks)
        try:
            result["variants"]["full"] = _variant(analyzer, frames, start, end, window_tracks)
        except Exception as exc:
            result["variants"]["full"] = {"status": "error", "error": str(exc)}
            output.write_text(json.dumps(result, indent=2) + "\n")
            raise
        bounds = focus_bounds([{"normalized_box": focus_box}])
        result["focus_bounds"] = list(bounds) if bounds is not None else None
        if bounds is None:
            result["variants"]["focus"] = {"status": "no_op", "reason": "focus_bounds returned None"}
        else:
            try:
                focus_frames = context_detail_frames(frames, [{"normalized_box": focus_box}])
                result["variants"]["focus"] = _variant(
                    analyzer, focus_frames, start, end, window_tracks
                )
            except Exception as exc:
                result["variants"]["focus"] = {"status": "error", "error": str(exc)}
                output.write_text(json.dumps(result, indent=2) + "\n")
                raise
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="Run an opt-in paired spatial video diagnostic")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--focus-box", type=float, nargs=4, required=True, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_probe(args.video, args.start, args.end, args.focus_box, args.output)


if __name__ == "__main__":
    main()
