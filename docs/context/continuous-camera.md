# Continuous browser camera demo

The review page has an opt-in, one-browser-camera demo. It is not a live alert
or distributed-camera system. A reviewer must choose **Start monitoring** before
the browser asks for video permission. Capture is video-only; no audio track is
requested. **Stop monitoring**, page navigation, and recorder errors stop media
tracks, recording timers, and pending retries.

The browser creates a new recording about every 10 seconds from the same media
stream. A new recording makes each submitted WebM independently decodable by
the ordinary upload worker. There can be a short gap at each recording rollover;
the interface says so. It also reports a visible gap when the bounded browser
queue fills or Stop ends an unfinished chunk.

Each browser keeps a generated `camera_id` locally when storage is available,
and creates a new `capture_session_id` for each Start action. Both identifiers
are constrained server-side and are metadata only. The service does not use
them for biometrics, identity matching, or cross-camera tracking. The server
stores camera-source and per-video provenance separately, so ordinary uploads
remain compatible.

The camera ID labels a browser source for this loopback demo. It is not an
authenticated physical-camera identity: clearing browser storage creates a new
label. The global queue bound remains the capacity boundary; device enrollment
and authentication are outside this demo.

`POST /api/cameras/<camera_id>/chunks` accepts a finite supported video file and
a session id plus a generated chunk id. The browser reuses that chunk id on a
retry, so a response lost after commit returns the original video/job rather
than adding another durable analysis job. The endpoint rejects a declared body
larger than the chunk cap before multipart parsing and copies streams with the
same cap before they enter durable media. It then prepares normal upload evidence, then transactionally
checks and reserves both limits with the video and analysis job creation:

- default global browser-camera pending limit: 8 jobs;
- default per-camera pending limit: 2 jobs;
- browser local limit: one in-flight upload and one pending chunk.

The pending count includes queued and processing analysis jobs. At capacity the
endpoint returns HTTP 429 with `Retry-After` and `retry_after_s`; the browser
pauses capture and retains the in-flight chunk for retry. It does not present a
rejected chunk as queued. The global limit prevents endlessly creating new
camera IDs from bypassing the bound. Configure server limits with
`BEHAVIOR_CAMERA_GLOBAL_PENDING`, `BEHAVIOR_CAMERA_PER_CAMERA_PENDING`, and
`BEHAVIOR_CAMERA_RETRY_AFTER_S`.

Camera health is deliberately basic: `GET /api/cameras/<camera_id>` exposes the
last server receipt time, last session, and the most recent capacity or analysis
error. Its `active` versus `stale` state is calculated only from the server
receipt timestamp (45 seconds), never from guessed camera activity. Browser UI
separates queued/backpressure, disconnected retry, capture failure, and delayed
or failed inference. Existing candidate-event review and evidence clips remain
the human decision path.

The focused regression suite is `uv run python -m unittest discover -s tests
-p 'test_camera.py' -v`. It uses a deterministic analyzer and FFmpeg stand-in
to establish chunk admission through job completion, a review event, and its
evidence-clip endpoint. That test does not establish physical webcam behavior;
browser media capture uses a simulated stream when hardware permission is not
available.
