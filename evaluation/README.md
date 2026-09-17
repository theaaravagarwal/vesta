# Reproducible video evaluation

Run on the primary compute host; footage stays in ignored `datasets/` and results
in ignored `runs/`. The public samples are real MEVA footage, not footage from the
project's camera. Their annotations are deliberately **unreviewed** until a person
labels event spans; zero model alerts does not prove correctness.

```bash
.venv/bin/python evaluation/fetch_meva.py
.venv/bin/python evaluation/replay.py datasets/meva-demo/manifest.json --output runs/meva-predictions.json
.venv/bin/python evaluation/evaluate.py datasets/meva-demo/manifest.json runs/meva-predictions.json
```

The initial three clips are a small, same-session exploratory smoke set. They are
not an accuracy benchmark and must not be split across training and held-out
partitions. [Official MEVA download instructions](https://mevadata.org/resources/README-meva-kf1-data.html)
and [CC BY 4.0 license](https://mevadata.org/resources/MEVA-data-license.txt).
Attribution: MEVA, Kitware Inc. and IARPA; Corona et al., *MEVA: A Large-Scale
Multiview, Multimodal Video Dataset for Activity Detection*, WACV 2021.

## Labeling and scoring

Each manifest clip has `id`, `path`, `sha256`, `source_url`, `license`,
`session_group`, `split`, `label_status`, `duration_s`, and `events`.
Reviewed events have `action`, `start_s`, and `end_s`; reviewed ordinary footage
has an empty events list. Use consistent observable action names, not personal
appearance, criminality, or guessed authorization. Explicitly label ambiguity in
review notes; do not silently force uncertain footage into positive/negative labels.

`evaluate.py` matches same-action spans one-to-one using temporal intersection over
union (default 0.3). Duplicate predictions count as false positives. Failures,
missing predictions, and unreviewed footage are exposed separately rather than
counted as negatives. Precision/recall and Wilson intervals are conditional on
reviewed successful clips; intervals assume independent events and can understate
uncertainty for correlated scenes. This is not a calibration or readiness claim.

Freeze code commit, model digest, prompt/config version, sampling, annotations,
and source checksums for comparisons. Keep held-out recordings and sessions out of
prompt tuning/training. Report per-camera/action/lighting slices, processing time,
peak GPU memory and reviewer workload beside aggregate metrics. Run the previous
repository baseline in an isolated checkout if comparing old prompts; never restore
credential-bearing defaults into the active service.

The evaluator also returns `clip_outcomes` with each clip's status, duration, and
event counts, plus `coverage` counts and seconds for reviewed successful, failed,
missing, and unreviewed footage. These coverage categories can overlap when an
unreviewed clip also fails or has no prediction; that overlap keeps both facts
visible. Failed, missing, and unreviewed footage is excluded from event and
false-alert denominators.

`ordinary_false_alerts_per_video_hour` measures alerts on reviewed clips with no
labeled events, using only successfully analyzed ordinary footage. The result
includes `ordinary_reviewed_successful_seconds`, the denominator used for that
rate; it is null when there is no such exposure. If valid prediction records
contain `elapsed_s`, `processing` summarizes that wall time across available
records. It may include upload, queue, and processing time, so it is not an
inference-latency or live-capacity measurement.

For an explicitly selected offline spatial diagnostic, run the paired probe with
the same 2 fps, at most 8-second input contract used by the analyzer:

```bash
.venv/bin/python -m evaluation.probe_views --video path/to/video.mp4 \
  --start 12 --end 20 --focus-box 0.20 0.20 0.60 0.80 \
  --output runs/spatial-probe.json
```

This is a diagnostic comparison, not a benchmark score or a production default.

## Learning

Confirm/dismiss/correction metadata is retained as annotation candidates, never
self-certified training labels. Export and adjudicate before fine-tuning. There is
no automated identity enrollment, self-training, or model promotion in this demo.
Fence-climbing and tampering coverage must be added and independently labeled;
these three samples do not establish it.

## Public publisher-labeled benchmark

`fetch_uca.py` imports a fixed six-video UCA subset using timestamped publisher
annotations and checksum-verified Hugging Face media. See
[selection, licensing and results](../docs/context/public-benchmark.md). The
action mapping is explicit and exploratory; no user labeling is needed to run it.

```bash
.venv/bin/python evaluation/fetch_uca.py --download
.venv/bin/python evaluation/replay.py datasets/uca-demo/manifest.json --output runs/uca-predictions.json
.venv/bin/python evaluation/evaluate.py datasets/uca-demo/manifest.json runs/uca-predictions.json
.venv/bin/python evaluation/evaluate.py datasets/uca-demo/manifest.json runs/uca-predictions.json --ignore-action
```

Action-agnostic matching evaluates temporal overlap only and explicitly omits
per-action statistics. It is not a claim that the predicted action was correct.

## Private candidate diagnostics

Each analysis job now records every sampled window, its frame count, model
candidate count, and inference status in SQLite. Every valid model candidate is
recorded with its action, evidence, and the evidence-gate decision/reason before
alerts are merged. A zero-candidate window therefore remains distinguishable
from a rejected candidate. Traces are kept across reanalysis jobs and removed
when storage cleanup evicts that video's source. They are not exposed through
the review API or notification outbox.

On the compute host, export one video's trace to the ignored `runs/` directory:

```bash
.venv/bin/python evaluation/export_candidates.py VIDEO_ID --output runs/VIDEO_ID-candidates.json
```

The export may contain descriptions of people and private scene context; keep
it out of Git. This trace diagnoses model/gate behavior but does not establish
that an event was visible in a frame. Review the source video for that judgment.

## Run provenance and variants

`replay.py` reads `GET /api/system` before uploading anything and writes the
serving model and `config_version` into the predictions file under the reserved
`run` key. `evaluate.py` echoes that block in its results, so a score is never
separated from the variant that produced it, and a run with zero events still
records what produced the zero. Prediction files written before this existed
simply report `"run": null`. A manifest may not use `run` as a clip ID.

## Post-processing sweeps

`merge_sweep.py` re-scores a recorded prediction file under different event-merge
gaps without running inference, so a post-processing choice can be compared
deterministically and without GPU time:

```bash
.venv/bin/python evaluation/merge_sweep.py datasets/uca-development-v3.json \
  runs/focus-off-2-predictions.json --gaps 0 0.5 1 2 4 --out runs/merge-sweep.json
```

It re-groups events the model already produced. It cannot show what a different
setting would have made the model say, so a candidate that looks good here still
needs a live run before any default changes.

## Recording a run

After replay and scoring, generate the machine-readable record that the
[public benchmark](../docs/context/public-benchmark.md) table links:

```bash
.venv/bin/python evaluation/record_run.py \
  --manifest datasets/uca-development-v3.json \
  --run control=runs/focus-off-2-predictions.json \
  --run focus=runs/focus-on-2-predictions.json \
  --purpose "what this run was for" --verdict "what was decided" \
  --out docs/context/benchmarks/<run>.json
```

It scores both matching modes, summarizes per-clip status including failures,
and captures provenance: git head and whether the tree was dirty, SHA-256 of the
analysis sources, GPU, and model digests from the serving endpoint. Anything it
could not inspect is recorded as `null` rather than guessed, so a record never
implies a check that did not happen. Write the prose section from the generated
record, not from memory, and append new runs instead of editing old ones.

Variant experiments run against `evaluation.serve:create_app()` in an isolated
`VESTA_EXPERIMENT_RUNTIME`, never the production database. That app now requires
`BEHAVIOR_EVENT_POLICY` and `BEHAVIOR_FOCUS_VIEW` to be set explicitly: the event
prompt and the spatial-crop view are independent variables, and changing both at
once produced the uninterpretable results recorded in
[the public benchmark](../docs/context/public-benchmark.md). Use one runtime
directory per variant and keep the model digest fixed across the comparison.

## Supervised pilot acceptance

The fixed-camera pilot protocol and JSON artifact are documented in
[docs/context/pilot-plan.md](../docs/context/pilot-plan.md). Score a frozen
session with:

```bash
uv run python -m evaluation.pilot pilot-session.json --output pilot-score.json
```

The scorer requires exactly 20 independently staged crossings, accepts at least
18 matches, requires known first-dashboard-render latency of no more than 10
seconds for every match, and requires at least one hour of ordinary exposure
with at most three ordinary false alerts. Missing, failed, duplicate, and gap
records stay visible; unknown latency never passes.
