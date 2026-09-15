# Behavior demo implementation contract

## HTTP interface

All JSON errors use `{ "error": "message" }` with an appropriate non-2xx status.
The new default page is `/review`; `/` redirects there, and the original UI remains `/legacy`.

- `POST /api/videos`: multipart `video`, optional `captured_at` ISO8601 with offset and `timezone` IANA name. Returns 202 `{video, job}`. Upload queues analysis; no camera access.
- `GET /api/videos`: `{videos: [...]}` newest first.
- `GET /api/videos/<id>`: `{video, job, events, scene}`.
- `GET /api/videos/<id>/media`: source playback, with range support.
- `GET /api/videos/<id>/frame`: representative JPEG for scene annotation.
- `POST /api/videos/<id>/cancel`: cancel outstanding job.
- `POST /api/videos/<id>/analyze`: enqueue reanalysis, reject when already active.
- `POST /api/videos/<id>/scene/suggest`: queue AI scene suggestions (returns 202); use GET detail to poll scene status.
- `PUT /api/videos/<id>/scene`: `{approved: bool, regions: [{id, label, kind, points: [[x,y],...]}], schedule: {start: "19:00", end: "06:00", timezone: "America/Los_Angeles"} | null}`. Coordinates normalized 0..1; kinds `fence`, `entrance`, `restricted`, `other`; polygons >=3 points. Approved geometry is advisory context, not proof of crossing. No schedule inferred when capture time absent.
- `PATCH /api/events/<id>`: `{review_status: "unreviewed"|"confirmed"|"dismissed", pinned: bool, correction: string}`; partial updates allowed, server validates.
- `GET /api/events/<id>/clip`: compressed evidence playback.
- `GET /api/outbox`: `{items: [...]}`; statuses are `pending_integration`, never falsely delivered.
- `GET /api/system`: `{storage: {used_percent, free_gb, paused, message}, worker: {alive}, model: string}`.

## Response objects

Video: `{id, name, created_at, captured_at, timezone, duration_s, status, media_url, frame_url, error}`. Statuses queued/processing/done/error/cancelled/evicted. Timestamps are ISO8601 UTC strings except duration/offset fields.

Job: `{id, status, progress, stage, error}`; progress 0..100, statuses queued/processing/done/error/cancelled.

Event: `{id, video_id, start_s, end_s, action, description, evidence: [string], uncertainty: string, track_ids: [string], review_status, pinned, correction, clip_url, model, config_version}`. `uncertainty` is human-readable explanation, not probability. No 0–100 threat scores.

Scene: `{status: "empty"|"queued"|"processing"|"suggested"|"approved"|"error", approved, regions, schedule, error}`. Human edits/approval cannot be overwritten by stale suggestion jobs.

Outbox item: `{id, event_id, created_at, status, action}`. Deduplicate by event ID.

## Ownership and integration

- Backend worker owns `behavior/`, `tests/test_behavior*.py`, and `main.py`. Expose `behavior.create_app()` so dedicated host service can avoid importing legacy model/global workers. Also integrate `/review` and blueprint into main if feasible without circular imports; host defaults to `behavior:create_app()`.
- UI worker owns `templates/review.html`, `static/review.css`, `static/review.js` and design docs only. Use this contract; no backend edits.
- Host worker owns remote environment, user services, and local `deploy/`, `scripts/compute-check.py`, `scripts/remote-compute.sh`, `docs/context/compute-host.md`. Do not start final service until backend ready.
- Root owns evaluation tooling/docs, dependency manifest/lock and integration. No worker commits or pushes.

Retain reviewed/pinned evidence during reanalysis: reject reanalysis if protected events exist unless an explicit future revisioning flow is implemented. Pending outbox entries for removed events must not survive reanalysis/eviction. Keep media/SQLite outside Git in runtime/behavior; run one worker per application process and exactly one server process.
