#!/usr/bin/env python3
"""Run the local fixed-camera collector sidecar.

It shares Vesta's SQLite store only on the same compute host.  The web service
writes desired states; this process owns RTSP connections, frames, and the
persisted tracker instance.  It never prints credential values.
"""

from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path

from behavior import Store
from behavior.boundary import BoundaryCollectorService, CameraSecrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Vesta's local boundary collector")
    parser.add_argument("--runtime", default=os.getenv("BEHAVIOR_RUNTIME", "runtime/behavior"))
    parser.add_argument("--secrets-dir", default=os.getenv("BEHAVIOR_BOUNDARY_SECRETS_DIR", "runtime/behavior/camera-secrets"))
    parser.add_argument("--model", default=os.getenv("BEHAVIOR_BOUNDARY_YOLO_MODEL", ""))
    parser.add_argument("--max-cameras", type=int, default=int(os.getenv("BEHAVIOR_BOUNDARY_MAX_CAMERAS", "4")))
    parser.add_argument("--once", action="store_true", help="reconcile desired state once, then exit")
    args = parser.parse_args()
    if args.max_cameras < 1 or args.max_cameras > 32:
        parser.error("--max-cameras must be between 1 and 32")
    # Do not run Store's web-worker crash recovery from this separate process.
    store = Store(Path(args.runtime), recover_jobs=False)
    service = BoundaryCollectorService(store, CameraSecrets(Path(args.secrets_dir)),
                                       model_path=args.model, max_cameras=args.max_cameras)
    if args.once:
        service.run_once()
        return 0

    def stop(_signal, _frame):
        service.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
