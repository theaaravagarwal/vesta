# Door-contact recognition validation

This records a rejected development experiment for a narrow observable action.
It does not change the frozen UCA benchmark results or the production policy.
The temporary candidate prompt is retained in
`evaluation/door_event_experiment.py` for provenance; the application exposes
only its existing `baseline` and `observable-v3` policies, and its evidence
gate was unchanged throughout the diagnostic.

## Source sequence and label audit

The selected non-fence sequence is `uca-04`, source `Vandalism034`,
`datasets/uca-demo/sample04.mp4`, SHA-256
`df5afed820baee6c0497a9c770b44f18f857aef10da1c0e968c0718ca48c4a30` on the
compute host. The frozen importer maps publisher annotation indices 4 and 7
to one reviewed `object_tampering` event at 39.6–58.0 s because the repeated
actions are nearby.

Inspection of the source sequence at 0.125–0.5 s spacing shows a person in a
beige shirt repeatedly lunging and extending limbs toward the left-side
doorway/wall access surface. The person withdraws, crouches and reaches for an
object around 44.5–45.5 s and 50.0–50.5 s, then repeats the directed contact.
This supports the observable description “repeated forceful contact/impact at
an access surface.” It does not establish damage, motive, authorization,
identity, or criminality. The publisher action mapping is retained as imported;
this audit does not relabel the benchmark.

The existing reviewed ordinary controls are `uca-05` (`Normal_Videos609`,
0–20.108 s) and `uca-06` (`Normal_Videos610`, 0–28.096 s). They contain
street/cart and walking scenes, respectively, and no ordinary door-use span.
They are broad false-alert controls only. The existing MEVA exploratory clips
are unreviewed: the stairwell clip `2018-03-05.09-49-46.09-50-00.school.G419.r13`
shows a static door during the inspected 12.0–14.0 s sequence; an earlier
model output claiming entry is not source ground truth. No MEVA clip is used as
a scored negative. At the time of this initial audit, an ordinary door-use
control was pending bounded acquisition.

A bounded MEVA control is now available on the compute host under
`datasets/meva-door-control/`. The source is the 2,035,104-byte public clip
`2018-03-09.10-30-00.10-35-00.hospital.G479.r13.avi` (SHA-256
`c880358c3c8805898d2288912dd32ec720c83d2e1fa45e061a9c3998b2e9d86a`) and its
publisher annotation JSON (SHA-256
`a714ffa60d085e845aa55abe481bd1cd95de7977b9dd8ff56be204d173d66647`). The
10-second crop `hospital-G479-door-286-296.mp4` (SHA-256
`9907eda1431de30cb3f83b5443f230b3b8fed4c8088c8b1e84167f833d217f21`) shows
the ordinary door sequence in relative seconds 3.3–5.6 and 6.0–7.9. The
publisher labels are `person_opens_facility_door` at source 289.3–291.6 s and
`person_enters_through_structure` at 292.0–293.9 s. Its control manifest keeps
`events: []` with explicit publisher provenance because these routine actions
are outside the Vesta alert ontology; it is a reviewed ordinary control, not a
claim about authorization.

## Rejected candidate instruction

The door-contact instruction keeps the existing action vocabulary and gate. It
requires all of the following:

- the same closed door, gate, window, or access surface is targeted;
- at least two distinct contact moments are visible in the ordered frames;
- a release or withdrawal separates the moments, so adjacent frames from one
  sustained contact do not count twice; and
- evidence names the concrete contact, such as a strike, kick, forceful shove,
  repeated pull, or pry attempt.

Repeated visible striking, kicking, prying, cutting, or forceful impact maps to
`object_tampering`; repeated forceful pulls or pushes without visible impact or
damage maps to `access_interaction`. A single touch, reaching, standing near a
door, ordinary handle use, routine opening/closing, or passing through a door
must produce no event. The instruction makes no inference about intent,
authorization, damage, or criminality. The lexical evidence gate is unchanged;
the prompt must elicit the existing evidence terms rather than bypassing the
gate.

The fixed plan is generated without inference or downloads:

```bash
uv run python evaluation/door_event_experiment.py \
  runs/benchmark-20260916/manifest.json \
  --door-control-manifest datasets/meva-door-control/manifest.json \
  --output runs/door-contact-v1-plan.json
```

The plan fixes 2 fps sampling, 8 s windows, 4 s stride, the positive span, and
the two reviewed UCA ordinary controls. The MEVA door-control crop is run as a
separate matched ordinary-door check, with its publisher activity spans kept in
the manifest note rather than scored as alert events. Compute-host commands use
the pinned `.venv` because that is the deployed inference environment; local
unit tests use `uv run`. The isolated overlay used for the historical
diagnostic has been removed and must not be re-enabled.

Historical predictions, failed jobs, timeouts, model/config responses, and
server logs are retained under the runtime artifact directory below. A failed
or missing job is not a negative. The candidate was rejected because it did not
improve detection on the positive sequence; it is not a promotion case.

## Bounded diagnostic result (2026-09-16)

The first baseline attempt used the isolated policy app and the existing
schema-constrained serving mode. Both the positive 40–48 s window and the MEVA
2–10 s control timed out at the bounded 190 s request limit; the checkpointed
errors are retained in
`runtime/experiment-door-contact-20260916T2316Z/door-baseline-timeouts.json`.
After one explicit `vesta-inference` restart, the paired diagnostic completed:

| Window | Baseline raw/gated | Door-contact raw/gated | Baseline s | Candidate s |
| --- | ---: | ---: | ---: | ---: |
| UCA `uca-04` 40–48 s | 0 / 0 | 0 / 0 | 7.548 | 5.859 |
| MEVA door control 2–10 s | 0 / 0 | 0 / 0 | 5.759 | 5.794 |

All four calls used `qwen2.5vl:3b`, full-scene frames, 2 fps, the same
schema-constrained output mode, and the unchanged evidence gate. The candidate
did not recover the positive sequence and did not add an ordinary-door alert.
This is a bounded diagnostic result, not an accuracy estimate or a promotion
case. Raw outputs, config versions, source checksums, server logs, and failed
attempts are retained under the runtime directory above.
