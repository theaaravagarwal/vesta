"""Checkpointed inference-format diagnostic for the dedicated local endpoint.

It extracts a short, operator-selected public or consented clip window and
compares the current JSON-schema format with Ollama's JSON-object format.  Both
formats pass through :meth:`TemporalAnalyzer.infer`, so Vesta's existing strict
application validation is always retained.  The output contains only frame
dimensions, byte counts, status, and elapsed time -- never frames or model text.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from behavior import TemporalAnalyzer
from behavior.views import context_detail_frames


JSON_OBJECT_EVENT_CONTRACT = (
    "Output one JSON object only. Its only top-level field is events, an array of at most four items. "
    "Each item has exactly start_s (number), end_s (number), action (one of climbing, boundary_entry, "
    "access_interaction, object_tampering, other_observable_event), description (non-empty string), "
    "evidence (array of one to three non-empty strings), and uncertainty (string). "
    "Use {\"events\":[]} when there is no qualifying action."
)


class JsonObjectAnalyzer(TemporalAnalyzer):
    """Use Ollama's simpler JSON-object grammar without relaxing Vesta checks."""

    def __init__(self, include_contract: bool = False, timeout_s: float = 180):
        self.include_contract = include_contract
        self.timeout_s = timeout_s
        self.response_shape = None
        self.raw_response = None

    def _request_events(self, payload, token_budget, attempt):
        modified = copy.deepcopy(payload)
        modified["response_format"] = {"type": "json_object"}
        if self.include_contract:
            modified["messages"][0]["content"][0]["text"] += " " + JSON_OBJECT_EVENT_CONTRACT
        request_payload = {**modified, "max_tokens": token_budget}
        request = urllib.request.Request(
            self.base_url + "/v1/chat/completions",
            data=json.dumps(request_payload, separators=(",", ":"), ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
        )
        timeout = self.timeout_s
        if not 1 <= timeout <= 240:
            raise RuntimeError("probe timeout must be between 1 and 240")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"event model request failed (attempt={attempt}, model={self.model_name}, "
                f"http_status={exc.code}, finish_reason=unavailable): HTTP {exc.code}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                f"event model request failed (attempt={attempt}, model={self.model_name}, "
                f"request_timeout_s={timeout:g}, finish_reason=unavailable): {exc}"
            ) from exc
        model = str(data.get("model") or self.model_name) if isinstance(data, dict) else self.model_name
        try:
            choice = data["choices"][0]
            finish_reason = str(choice.get("finish_reason") or "unknown")
            content = choice["message"]["content"]
        except Exception as exc:
            raise RuntimeError(
                f"event model response failed (attempt={attempt}, model={model}, finish_reason=unknown): malformed response"
            ) from exc
        self.raw_response = content
        if finish_reason == "length":
            return {}, finish_reason, model
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except Exception as exc:
            raise RuntimeError(
                f"event model response failed (attempt={attempt}, model={model}, finish_reason={finish_reason}): malformed JSON"
            ) from exc
        self.response_shape = _output_shape(parsed)
        return parsed, finish_reason, model


def _output_shape(parsed: object) -> dict:
    """Describe structure only; never retain model-produced strings."""
    summary = {"top_type": type(parsed).__name__}
    if not isinstance(parsed, dict):
        return summary
    summary["top_fields"] = sorted(str(key) for key in parsed)
    events = parsed.get("events")
    summary["events_type"] = type(events).__name__
    if not isinstance(events, list):
        return summary
    summary["event_count"] = len(events)
    event_shapes = []
    for event in events[:4]:
        row = {"type": type(event).__name__}
        if isinstance(event, dict):
            row["fields"] = sorted(str(key) for key in event)
            evidence = event.get("evidence")
            row["evidence_type"] = type(evidence).__name__
            if isinstance(evidence, list):
                row["evidence_count"] = len(evidence)
                row["evidence_item_types"] = [type(item).__name__ for item in evidence]
                row["evidence_string_lengths"] = [len(item) for item in evidence if isinstance(item, str)]
        event_shapes.append(row)
    summary["events"] = event_shapes
    return summary


def _checkpoint(output: Path, result: dict) -> None:
    """Replace a complete prior record, so interruption never erases an attempt."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(output)


def _frame_shape(frames: list[Path]) -> list[dict]:
    details = []
    for frame in frames:
        with Image.open(frame) as image:
            details.append({"width": image.width, "height": image.height, "bytes": frame.stat().st_size})
    return details


def _variant(name: str, frames: list[Path], timeout_s: float):
    if name.startswith("strict-"):
        return TemporalAnalyzer(), frames
    if name.startswith("json-object-"):
        return JsonObjectAnalyzer(
            include_contract=name.startswith("json-object-contract-"), timeout_s=timeout_s
        ), frames
    raise ValueError(f"unsupported sequence item: {name}")


def run_probe(video: Path, start: float, end: float, focus_box: list[float], output: Path, sequence: list[str], timeout_s: float, retain_raw_response: bool = False) -> dict:
    if not video.is_file():
        raise ValueError("video does not exist")
    if not (0 <= start < end <= start + 8):
        raise ValueError("window must be non-empty and no more than eight seconds")
    if len(focus_box) != 4 or not (0 <= focus_box[0] < focus_box[2] <= 1 and 0 <= focus_box[1] < focus_box[3] <= 1):
        raise ValueError("focus box must be normalized x1 y1 x2 y2 coordinates")
    if not 1 <= timeout_s <= 240:
        raise ValueError("timeout must be between 1 and 240 seconds")
    result = {
        "label": "inference format diagnostic",
        "retain_raw_response": retain_raw_response,
        "status": "preparing",
        "window": {"start_s": start, "end_s": end},
        "sequence": [],
    }
    _checkpoint(output, result)
    with tempfile.TemporaryDirectory(prefix="vesta-inference-probe-") as directory:
        root = Path(directory)
        sampler = TemporalAnalyzer()
        full = sampler.frames(video, root / "full", start, end)
        focus = context_detail_frames(full, [{"normalized_box": focus_box}])
        result["inputs"] = {"full": _frame_shape(full), "focus": _frame_shape(focus)}
        if any(name.endswith("focus") for name in sequence) and focus == full:
            result.update(
                {
                    "status": "error",
                    "error": "focus bounds covered too much of the frame; no focus input was produced",
                }
            )
            _checkpoint(output, result)
            return result
        result["status"] = "running"
        _checkpoint(output, result)
        for name in sequence:
            analyzer, frames = _variant(
                name, full if name.endswith("full") else focus, timeout_s
            )
            row = {"name": name, "status": "running", "frame_count": len(frames)}
            result["sequence"].append(row)
            _checkpoint(output, result)
            started = time.monotonic()
            try:
                events = analyzer.infer(frames, start, end, {})
                row.update({"status": "ok", "event_count": len(events)})
            except Exception as exc:
                row.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:500]})
            if isinstance(analyzer, JsonObjectAnalyzer) and analyzer.response_shape:
                row["response_shape"] = analyzer.response_shape
            if retain_raw_response and isinstance(analyzer, JsonObjectAnalyzer) and analyzer.raw_response is not None:
                row["raw_response"] = analyzer.raw_response
            row["elapsed_s"] = round(time.monotonic() - started, 3)
            _checkpoint(output, result)
    result["status"] = "completed"
    _checkpoint(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--focus-box", type=float, nargs=4, required=True, metavar=("X1", "Y1", "X2", "Y2"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequence", default="strict-full,json-object-full", help="comma-separated strict/json-object full/focus calls")
    parser.add_argument("--timeout-s", type=float, default=190)
    parser.add_argument("--retain-raw-response", action="store_true", help="retain model text only in this ignored output artifact")
    args = parser.parse_args()
    result = run_probe(args.video, args.start, args.end, args.focus_box, args.output, [item for item in args.sequence.split(",") if item], args.timeout_s, args.retain_raw_response)
    print(json.dumps({"status": result["status"], "output": str(args.output), "attempts": len(result["sequence"])}))


if __name__ == "__main__":
    main()
