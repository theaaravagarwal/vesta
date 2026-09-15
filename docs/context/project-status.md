# Project status

## Current implementation

The default service is now `behavior:create_app()` with the review UI at `/review`.
It is an uploaded-video demo, separate from the original camera process. SQLite
persists jobs, events, scene edits, review corrections and the notification outbox.
The worker normalizes media, tracks people, and analyzes every overlapping temporal
window using structured candidate-event output. Failed analysis remains explicit.

The public MEVA smoke set is available on the compute host; target-camera footage
and held-out behavior labels remain unavailable. Do not infer accuracy from a
completed job. See [verification](verification.md), [host runbook](compute-host.md),
and [API contract](implementation-contract.md) for current evidence and behavior.

The notes below preserve the original audit rather than describing the new
worker. Original camera scheduling/live-state limitations still apply to `/legacy`.


## Original baseline (commit 70243b5)

The repository contains a single-camera Flask app. `main.py` owns live RTSP capture, YOLO person detection, autonomous/manual recording, recording metadata and event JSON, upload analysis, and calls to a llama.cpp-compatible vision endpoint. Runtime state and media are local under `runtime/`; several queues, jobs, and worker state are in process memory. There is no camera footage available for evaluation.

The application has two materially different AI paths:

1. **Recorded clip auto-analysis** (`main.py:240-435`): a background queue analyzes recorded clips using its own strongly directive JSON prompt and writes a sidecar. Its rules equate proximity or posture change with violence, rigid held objects with weapons, and presence with elevated threat.
2. **Analyze/replay and autonomous clip understanding** (`main.py:1948-2161`, `2164-2258`, `505-535`): YOLO/person-frame or even temporal mosaic sampling feeds image captions, a summary pass, and a separate 0–100 threat assessment. Autonomous clips disable YOLO filtering; the upload path defaults to it. These outputs are not interchangeable labels or calibrated probabilities.

`test_recorded_pipeline.py` reproduces the `/analyze/start`-style recorded-video path. It selects the newest recording (or with `--newest`, a recording/upload), stages a copy and prints errors, timings, and outputs. It is a diagnostic script, not an evaluation suite: it has no fixed corpus, annotations, assertions, or accuracy metrics.

## Historical baseline concerns from code inspection

- **Credentials/configuration:** historical versions of `main.py:36-44` contained credential-bearing RTSP defaults. Those defaults have been removed from the current setup and `.gitignore` has been expanded; rotate any real credentials that may have been used and retained in prior history. Credential values are intentionally not reproduced here.
- **Prompt policy can force false positives:** `main.py:365-390` turns weak visual cues such as closeness, a posture change, or any rigid object into high-severity conclusions and bans uncertainty language. This is an implemented policy, not validated behavior.
- **Sparse evidence and partial scoring:** `main.py:2045-2143` samples frames/mosaics, summarizes captions, and generates the threat score from the first one or two mosaics plus text. This creates a risk that brief events are missed or context is incomplete; no measured miss rate is known.
- **No evaluation evidence:** the replay script reports execution diagnostics only. Current code and available inputs do not establish precision, recall, false-alert rate, or calibration.

These are code-level findings and evaluation gaps. They do not establish that a particular event has been missed or that any specific accuracy level exists.

## Runtime and security audit notes

- The `/analyze/start` path stops the live stream before processing an upload (`main.py:2385-2420`), coupling offline analysis to camera availability.
- The arm schedule is stored/applied in browser JavaScript (`templates/index.html:2499-2565`), so it is client-local rather than a durable server schedule.
- Camera state and the YOLO model are process-global singletons (`main.py:95-125`); this is a single-camera architecture. Each analysis request starts a daemon thread (`main.py:2419`), without a global concurrency bound. The recording-analysis queue is an in-memory `queue.Queue` with a single daemon worker (`main.py:239-240`, `392-431`); it is not durable across process restarts and a job interrupted while marked `processing` can remain in that state.
- Routes are not protected by application authentication/authorization, and `/files/<path>` serves from the runtime directory (`main.py:2727`). Treat runtime media and metadata as exposed to anyone who can reach the service; `send_from_directory` constrains serving to its configured directory but does not provide access control. Validate names at API boundaries as well as filesystem paths.
- Recording listing schedules pending analyses as a side effect (`main.py:217-235`); the search endpoint builds a query from the listing/sidecar metadata rather than an indexed search store (`main.py:2594-2645`). There is no configured retention/deletion policy visible in this code path.
- The recording writer is created without checking `VideoWriter.isOpened()` before publishing it as active (`main.py:938-952`), so a failed codec/path open may not be surfaced as a recording failure.

These findings describe implementation limits, not observed production incidents. They belong in phase 1 before a network-accessible or multi-camera pilot.
