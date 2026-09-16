"""Build a machine-readable benchmark record from run artifacts.

Every recorded comparison in docs/context/benchmarks/ is written by this script
rather than copied by hand, so the prose and the stored numbers cannot drift
apart. Provenance that could not be inspected is recorded as null instead of
being guessed, and failed clips stay visible instead of becoming negatives.

  python evaluation/record_run.py --manifest datasets/uca-development-v3.json \\
      --run control=runs/focus-off-2-predictions.json \\
      --run focus=runs/focus-on-2-predictions.json \\
      --purpose "..." --verdict "..." --out docs/context/benchmarks/x.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

try:  # invoked as a script from the repository root, or imported as a module
    from metrics import score
except ImportError:  # pragma: no cover - import style only
    from evaluation.metrics import score

ROOT = Path(__file__).resolve().parents[1]
HASHED_SOURCES = ("behavior/__init__.py", "behavior/views.py", "evaluation/metrics.py")


def _command(args: list[str]) -> str | None:
    try:
        out = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def source_hashes() -> dict[str, str | None]:
    hashes = {}
    for name in HASHED_SOURCES:
        path = ROOT / name
        hashes[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    return hashes


def models(base_url: str | None) -> dict | None:
    """Model digests from the serving endpoint; null when it was not reachable."""
    if not base_url:
        return None
    try:
        with urlopen(base_url.rstrip("/") + "/api/tags", timeout=15) as response:
            listed = json.load(response)["models"]
    except Exception:
        return None
    return {m["name"]: {"digest": m["digest"], "size_bytes": m["size"]} for m in listed}


def provenance(base_url: str | None) -> dict:
    gpu = _command(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]
    )
    return {
        "git_head": _command(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(_command(["git", "status", "--porcelain"])),
        "source_sha256": source_hashes(),
        "gpu": gpu,
        "models": models(base_url),
        "camera_accessed": False,
    }


def clip_summary(predictions: dict) -> dict:
    return {
        cid: {
            "status": p["status"],
            "candidate_count": len(p.get("events", [])),
            "elapsed_s": p.get("elapsed_s"),
            "error": (p.get("job") or {}).get("error"),
        }
        for cid, p in predictions.items()
        if cid != "run"
    }


def build(manifest_path: Path, runs: dict[str, Path], purpose: str, verdict: str | None, iou: float, base_url: str | None) -> dict:
    manifest = json.loads(manifest_path.read_text())
    record = {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "purpose": purpose,
        "verdict": verdict,
        "temporal_iou_threshold": iou,
        "manifest": {
            "path": str(manifest_path),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "clips": [c["id"] for c in manifest["clips"]],
            "labeled_events": sum(len(c.get("events", [])) for c in manifest["clips"]),
        },
        "provenance": provenance(base_url),
        "runs": {},
    }
    for label, path in runs.items():
        predictions = json.loads(path.read_text())
        record["runs"][label] = {
            "predictions_path": str(path),
            "run": predictions.get("run"),
            "clips": clip_summary(predictions),
            "metrics": {
                "action_aware": score(manifest, predictions, iou),
                "action_agnostic": score(manifest, predictions, iou, ignore_action=True),
            },
        }
    return record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--run", action="append", required=True, metavar="LABEL=PATH")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--purpose", required=True)
    p.add_argument("--verdict")
    p.add_argument("--iou", type=float, default=0.3)
    p.add_argument("--base-url", default="http://127.0.0.1:8078")
    a = p.parse_args()
    runs = {}
    for item in a.run:
        if "=" not in item:
            p.error("--run expects LABEL=PATH")
        label, path = item.split("=", 1)
        runs[label] = Path(path)
    record = build(a.manifest, runs, a.purpose, a.verdict, a.iou, a.base_url)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=1) + "\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
