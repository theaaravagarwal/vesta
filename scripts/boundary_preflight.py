#!/usr/bin/env python3
"""Read-only local readiness report for a fixed-camera boundary pilot."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from behavior.boundary import CameraSecrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Check boundary pilot local prerequisites without opening a camera")
    parser.add_argument("--runtime", default=os.getenv("BEHAVIOR_RUNTIME", "runtime/behavior"))
    parser.add_argument("--secrets-dir", default=os.getenv("BEHAVIOR_BOUNDARY_SECRETS_DIR", "runtime/behavior/camera-secrets"))
    parser.add_argument("--model", default=os.getenv("BEHAVIOR_BOUNDARY_YOLO_MODEL", ""))
    args = parser.parse_args()
    database = Path(args.runtime) / "behavior.sqlite3"
    if not database.is_file():
        print(json.dumps({"ready": False, "error": "behavior store is not initialized"}))
        return 2
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        cameras = [row[0] for row in connection.execute("SELECT camera_id FROM boundary_cameras ORDER BY camera_id")]
    finally:
        connection.close()
    secrets = CameraSecrets(Path(args.secrets_dir))
    report = []
    for camera_id in cameras:
        valid = False
        try:
            secrets.rtsp_url(camera_id)
            valid = True
        except ValueError:
            pass
        report.append({"camera_id": camera_id, "credential_file_valid": valid})
    model_available = bool(args.model) and Path(args.model).is_file()
    print(json.dumps({"model_available_locally": model_available, "cameras": report}, separators=(",", ":")))
    return 0 if model_available and all(item["credential_file_valid"] for item in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
