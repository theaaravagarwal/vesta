# Supervised pilot session plan

The pilot is a fixed, supervised acceptance check for one camera view. It does
not create automatic training labels. A session is either `development` (used
to tune the setup) or `held_out`; never mix the two. The selected camera view,
line revision, vocabulary, and split should be recorded before a held-out run.
The application records those choices but does not claim to enforce external
prospective preregistration. Operators freeze human annotations only after the
recording stops and before export or scoring.
The acceptance target is **at least 18 of 20 staged crossings**, with every
matched crossing's first dashboard-render acknowledgement receipt at most 10 seconds
after the source observation, and no more than 3 ordinary false alerts during
at least one hour of ordinary exposure. Unknown latency cannot pass.

## Session artifact

`evaluation/pilot.py` scores a JSON artifact with this shape:

```json
{
  "schema_version": 1,
  "session_id": "pilot-2026-09-16-camera-1",
  "split": "held_out",
  "ordinary_exposure_s": 3600,
  "staged_crossings": [
    {"id":"s01", "start_s":120, "end_s":124, "direction":"a_to_b",
     "notes":"manual stage"}
  ],
  "alerts": [
    {"id":"a01", "start_s":120, "end_s":124, "direction":"a_to_b",
     "source_observed_at_s":120.0, "dashboard_render_ack_received_at_s":126.0,
     "status":"done"}
  ],
  "gaps": []
}
```

The operator saves staged spans independently of model alerts. `direction` is
an observable scene direction defined for that fixed view. The dashboard writes
`dashboard_render_ack_received_at_s` on the first UI-render acknowledgement for an alert;
the collector and browser test clock must be the same test clock (or include a
calibrated offset). Server insertion time alone is not dashboard visibility.
An alert acknowledgement is idempotent per alert id. Failed alerts and capture
`gaps` remain in the artifact. A missing alert, failed run, duplicate alert,
late timestamp, or unknown timestamp is reported explicitly; none is silently
converted to a negative example. An alert matched to a staged crossing is a
matched observable crossing and is not counted as an ordinary false alert. Additional
alerts over that span are listed as duplicates.

Run the scorer with:

```bash
uv run python -m evaluation.pilot session.json --output score.json
```

A held-out score is interpretable only when the camera view, line geometry,
event vocabulary, model/config revision, and intended session split were
recorded before the run, and its human annotations were frozen before scoring.
The scorer uses one-to-one matching and exact direction matching. Positive-duration alerts use temporal overlap (IoU 0.3); point alerts match when their crossing timestamp falls inside an independently authored expected window. Expected windows are never generated from candidate alerts; a session with no explicit expectations has no scored denominator. It does not infer identity, intent, guilt, or
authorization.

## Operator workflow

1. In local setup, record camera details, placement, view-line coordinates,
   direction vocabulary, lighting, and the unknowns. The user does not need to
   build a dataset by hand.
2. Use the bounded reference capture and manually approve the fixed view lines.
   Movement invalidates the view and requires manual reapproval; do not resume
   detection from a proposal automatically.

   During collection, the sidecar periodically compares the current frame to
   the approved reference and also repeats that comparison before publishing a
   crossing. It fails closed on an observed reframing beyond the stricter of
   four pixels or 1% of the frame diagonal, or after repeated unverifiable
   comparisons. This is a safety gate, not a claimed movement-detection
   performance result: low-texture, lighting, occlusion, or compression changes
   can pause the view and require a new manual review.
3. Stage 20 observable crossings, recording spans and direction in the session
   artifact while a reviewer watches the dashboard. Capture at least one hour
   of ordinary exposure in the same view.
4. Keep RTSP capture co-located with the compute host when possible. SSH access
   is localhost-only. A browser webcam is a separate source and needs its own
   setup and held-out session.
5. Review every candidate in the UI, including evidence and uncertain cases.
   Candidate confirmations, dismissals, and corrections remain review metadata,
   not automatic training labels.
6. Retain pilot sessions for 7 days, except for explicitly pinned incident or
   adjudication evidence. Delete ordinary source and derived media at expiry.

The backend owns session persistence, provenance, view revision, evidence
references, and the first-render acknowledgement. The UI owns the manual
staging/review flow. The artifact is the handoff between them and is suitable
for audit without trusting model output as ground truth.

## Compute-host rollout (not a completed pilot)

The collector is a separate user service so a web restart cannot interrupt an
active capture. Before any rollout, stop the web and collector user services,
then use SQLite's backup API to make a timestamped copy of
`runtime/behavior/behavior.sqlite3`. Preserve the prior service unit for
rollback. Pull the reviewed revision and run `uv sync --frozen` only when the
lockfile or dependencies changed. Install `deploy/vesta-boundary-collector.service`,
reload the user systemd manager, then enable and start the collector after the
web service is healthy.

The service starts idle. Do not create a camera secret, set a desired state,
open an RTSP stream, download a model, or invoke a GPU check as part of this
rollout. The read-only `scripts/boundary_preflight.py` reports local model and
credential-file readiness without opening a camera. Confirm the dashboard
reports the collector as unavailable/idle until an operator explicitly captures
a reference. Keep a backup of the old unit and restore it with the database
backup if the web health check fails.

No site session, 20-crossing run, or one-hour ordinary-exposure result has been
recorded by this repository. The targets above are a protocol, not performance
claims.

### Operator handoff

On the compute host, create the private directory and copy the placeholder
shape from `deploy/camera-secret.example.json` only when an operator is ready
to configure a camera:

```bash
install -d -m 700 ~/.config/vesta/cameras
install -m 600 deploy/camera-secret.example.json ~/.config/vesta/cameras/cam_example12345678.json
```

Replace the placeholder locally; this file is never committed and its URL is
never returned by the API or logged. The collector reads this directory through
`BEHAVIOR_BOUNDARY_SECRETS_DIR` in `~/.config/vesta/host.env`.

Before updating host code, make a consistent SQLite backup through Python's
SQLite backup API while both user services are stopped:

```bash
systemctl --user stop vesta-web.service vesta-boundary-collector.service
uv run python - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
source = Path.home() / "vesta/runtime/behavior/behavior.sqlite3"
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = source.with_name(f"behavior.before-boundary-{stamp}.sqlite3")
with sqlite3.connect(source) as from_db, sqlite3.connect(backup) as to_db:
    from_db.backup(to_db)
print(backup)
PY
```

Keep a copy of the prior collector unit, then install the reviewed unit and
start it idle:

```bash
install -m 644 deploy/vesta-boundary-collector.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vesta-web.service vesta-boundary-collector.service
```

From an operator computer, use the existing private tunnel and browser URL:

```bash
ssh -N -L 33263:127.0.0.1:33263 software@100.64.0.7
# open http://127.0.0.1:33263/review
```

The initial expected state is no configured camera or an unavailable/idle
collector. Run `uv run python scripts/boundary_preflight.py` only as a
read-only readiness check. A reviewer must explicitly capture a reference view,
approve lines, and start collection; none of those actions happens during unit
installation.
