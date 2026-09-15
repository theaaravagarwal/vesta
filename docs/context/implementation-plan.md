# Accepted implementation plan

Date: 2026-09-15. User authorized implementation, host setup, documentation and GitHub synchronization.

## Deliverable

Uploaded-video behavior review running on `software@100.64.0.7`, not a campus-ready
crime classifier. Describe observable actions over time with supporting clips,
uncertainty, temporary track IDs, and human review. Climbing, unusual entry and
possible tampering are examples rather than an exclusive single-class scope.

## Phases and acceptance

1. **Compute host:** isolated Python/CUDA environment, user-local media tools,
   official detector checkpoint, dedicated local vision endpoint and one-worker
   web service. Pass synthetic CUDA inference/backprop and image-sequence calls.
2. **Temporal backend:** durable SQLite jobs, 8-second windows at 4-second stride,
   2-fps vision samples plus persistent person tracks, structured evidence,
   duplicate merging, compressed event clips. All video windows are analyzed;
   person misses do not discard footage. Malformed/failed results are errors.
3. **Review surface:** upload/progress/cancel, video timeline, confirm/dismiss/pin,
   corrections, automatic proposed regions with editable normalized polygons,
   explicit approval and optional schedule. Unknown capture time remains unknown.
4. **Validation:** synthetic failure/regression tests, real public MEVA smoke
   replay, browser upload/review checks, benchmark provenance and evaluator.
   Public samples are unreviewed; target-camera accuracy remains unestablished.
5. **Sync and operations:** preserve all project context under docs/context,
   commit reviewed source to the fork, match host checkout, document access,
   service checks, update procedure, storage pressure and rollback.

## Defaults

- GPU host: RTX4090 Laptop16GB; one vision request at a time.
- Vision baseline: qwen2.5vl:3b, exact downloaded digest recorded by host setup.
- Temporary incident tracks only; no automatic identity enrollment or training.
- Notification outbox only: delivery adapter is not configured.
- Retention until pressure: trigger85% filesystem utilization or <50GiB free,
  recover toward75% and75GiB; caches then oldest unprotected media. Confirmed
  incidents/pinned clips protected; pause when protected evidence prevents recovery.
- School authorization and intent are not inferred from clothing or appearance.
- No secondary-machine configuration or assumed distributed/unified VRAM.

## Ownership

See [implementation-contract.md](implementation-contract.md). Backend and UI work
are independent only through that contract; root handles integration and changes
to the contract. Host setup runs separately. Existing unrelated files and services
must be preserved. Use bounded Terra/Luna workers and escalate only a concrete
blocker at reasonable effort.

## Rollback

Keep code/model/config versions in records. Stop only Vesta user services before
switching their source revision/configuration. Preserve runtime data and the
SQLite DB before any schema migration; do not silently overwrite protected clips
or restore historical embedded credentials. The original UI remains available
through the legacy main app, but it retains documented architectural limitations.
