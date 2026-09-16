"""Submit a local manifest to Vesta and export predictions for evaluate.py.

Uses curl for multipart uploads; requests are localhost by default. Dataset paths
resolve relative to repository root. No camera access or external notifications.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen
from metrics import validate_manifest

p = argparse.ArgumentParser()
p.add_argument("manifest", type=Path)
p.add_argument("--base-url", default="http://127.0.0.1:33263")
p.add_argument("--output", type=Path, required=True)
p.add_argument("--timeout", type=int, default=1800)
a = p.parse_args()
manifest = json.loads(a.manifest.read_text())
validate_manifest(manifest)
root = Path(__file__).resolve().parents[1]
# Record the serving variant before any upload. A run that produces no events
# still has to state which model and configuration produced that result.
with urlopen(a.base_url.rstrip("/") + "/api/system", timeout=30) as response:
    system = json.load(response)
results = {
    "run": {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest": str(a.manifest),
        "base_url": a.base_url,
        "model": system.get("model"),
        "config_version": system.get("config_version"),
    }
}
a.output.parent.mkdir(parents=True, exist_ok=True)
print("variant", results["run"]["model"], results["run"]["config_version"], flush=True)
for clip in manifest["clips"]:
    path = (root / clip["path"]).resolve()
    if (
        clip.get("sha256")
        and hashlib.sha256(path.read_bytes()).hexdigest() != clip["sha256"]
    ):
        raise ValueError(f"Checksum mismatch: {clip['id']}")
    started = time.monotonic()
    raw = subprocess.check_output(
        [
            "curl",
            "--fail-with-body",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            "-F",
            f"video=@{path}",
            a.base_url.rstrip("/") + "/api/videos",
        ],
        text=True,
    )
    created = json.loads(raw)
    video_id = created["video"]["id"]
    while True:
        with urlopen(
            a.base_url.rstrip("/") + "/api/videos/" + video_id, timeout=30
        ) as response:
            detail = json.load(response)
        job = detail.get("job") or {}
        if job.get("status") in ("done", "error", "cancelled"):
            break
        if time.monotonic() - started > a.timeout:
            raise TimeoutError(
                f"Job still active: {video_id}; cancel through review UI"
            )
        time.sleep(2)
    results[clip["id"]] = {
        "status": job["status"],
        "video_id": video_id,
        "events": detail.get("events", []),
        "job": job,
        "elapsed_s": round(time.monotonic() - started, 3),
    }
    a.output.write_text(json.dumps(results, indent=2) + "\n")
    print(clip["id"], job["status"], len(detail.get("events", [])), flush=True)
