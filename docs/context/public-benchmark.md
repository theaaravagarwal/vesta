# Public labeled-video benchmark

## Purpose and sources

Use existing publisher annotations so the user does not need to collect or label a dataset to begin testing. This is a small exploratory behavior benchmark, not a campus-readiness certification.

- [UCA official project](https://github.com/jia-wang-11/Surveillance-Video-Understanding-dataSet): CVPR 2024 surveillance-video annotations with human-written activity descriptions and temporal spans, built on UCF-Crime.
- [UCF-Crime original project](https://www.crcv.ucf.edu/research/real-world-anomaly-detection-in-surveillance-videos/): original surveillance videos and anomaly benchmark.
- [Hugging Face video mirror](https://huggingface.co/datasets/mteb/ucf-crime): individual downloadable media files, avoiding a full dataset archive. Mirror identity is not treated as proof of licensing.

UCA's README states Apache-2.0 and separately restricts use to academic/research purposes. The inspected Hugging Face card does not specify a media license. This run uses the material for local research evaluation; it does not establish commercial training or redistribution rights. Raw footage and annotation snapshots stay out of Git.

## Frozen selection and adaptation

Selection occurs before model predictions. Six source videos cover window climbing, fence climbing, door prying/entry, repeated door impacts, and two ordinary walking/cart scenes. Filenames supplied to inference are neutral. Publisher descriptions, source category names, and target timestamps are not provided to the model.

Publisher temporal spans are adapted to Vesta's observable action vocabulary; criminal intent, identity and authorization are not labels. Nearby repeated impacts are treated as one event. Crop offsets are subtracted, partial spans are clipped, and source duration is checked against the annotation duration before scoring. UCA calls these records its Test split, but the underlying UCF-Crime split differs: only the door-prying source is UCF test; the other five are UCF train. This is not an official held-out UCF benchmark, and a public pretrained model may have encountered these videos. The source video remains the grouping unit; crops from the same video must never cross training/evaluation splits.

`label_status=reviewed` in the current evaluator means these are imported publisher human annotations with an explicit mapping, not newly verified local-camera ground truth. This selection is marked exploratory because the action mapping and sample choice are project-specific. Captions are not necessarily exhaustive labels for every possible observable event, so unmatched predictions require inspection.

Report both exact-action temporal matching and action-agnostic temporal matching at IoU 0.3. The latter checks whether an alert overlaps a labeled event without claiming the action classification was correct. Duplicate predictions still count as false positives. Missing/failed jobs remain explicit. Tiny, selected clips cannot establish an operational false-alert rate or independent-event confidence intervals.

## Reproduction

The importer, `evaluation/fetch_uca.py`, records pinned source revisions, source hashes, annotation provenance and crop mapping in `datasets/uca-demo/manifest.json`. Media and raw run artifacts are ignored by Git. The deployed model/prompt stays frozen during the initial run. The fixed selection and source revisions are recorded in the importer; run results are recorded below after execution.

## Expansion after this pilot

MEVA remains an alternative with clear CC BY 4.0 terms and public activity
annotations. Its inspected examples label ordinary door opening and facility entry,
which are useful for activity checks and benign comparisons but are not tampering
or unauthorized-entry labels. See the [official download guide](https://mevadata.org/resources/README-meva-kf1-data.html).
Do not treat unannotated intervals as verified negatives.

## Baseline results — 2026-09-15

Frozen application commit `6a82bf5`, `qwen2.5vl:3b`, prompt/config
`temporal-v2-schema`. [Machine-readable results](benchmarks/uca-baseline.json)
record exact model/source digests and per-clip counts. The source code and model
were not changed during replay.

| Clip | Target behavior | Labeled events | Candidates | Job |
| --- | --- | ---: | ---: | --- |
| sample01 | Window interaction/climbing | 2 | 1 | Done |
| sample02 | Fence climbing/entry | 3 | 1 | Done |
| sample03 | Door prying/entry | 2 | 0 | Done |
| sample04 | Repeated door impacts | 1 | Unavailable | Failed: incomplete JSON |
| sample05 | Ordinary cart/road activity | 0 | 2 | Done |
| sample06 | Ordinary walking | 0 | 2 | Done |

Five of six jobs completed. Across those five clips (seven labeled events),
exact-action matching found **0 true positives, 6 false positives, 7 false negatives**.
Action-agnostic temporal matching found **1 true positive, 5 false positives,
6 false negatives**: precision 16.7%, recall 14.3%, conditional on successful
jobs. The additional labeled event in the failed clip is excluded from those
metrics and reported as unavailable, never a safe negative. Across all eight
scheduled target events, only one received a temporally matched alert.

Both ordinary clips generated two alerts each: four false alerts in 48.2 seconds
of selected ordinary footage. This demonstrates a failure on these examples;
it is too small and selected to estimate an operational hourly alert rate.
The evaluator's per-hour extrapolation and Wilson intervals are included for
reproducibility, not operational forecasting. Zero exact-action matches does not
mean no relevant activity was ever noticed: the window-climbing moment was
flagged under a different action label.

Attempted footage duration was 260.596 seconds; sequential upload/analysis took
371.119 seconds (about 6.2 minutes), including the failed job. This is not a
steady-state campus capacity measurement. The door-impact job returned an
unterminated JSON string. Output truncation is a plausible cause, not yet proven
from a saved raw response. No retries were substituted into the baseline.

## Engineering implications

The current 3B model is not a reliable behavior detector. Priorities are to
handle incomplete model responses explicitly, stop routine standing/walking from
becoming candidate events, and improve action recognition and temporal coverage.
Compare a stronger vision baseline and spatial crops around tracked activity as
separate experiments. Keep this baseline immutable; label any tuning against this
subset as development, then expand evaluation using different source videos.
Do not train or deploy campus alerts based on these six clips.

The importer and matching changes pass 24 unit/API/evaluation tests. Source media
and raw predictions remain on the compute host in `datasets/uca-demo` and
`runs/uca-predictions.json`; the review UI contains the six neutral sample uploads.

## Rejected detection experiment — observable-v3

The follow-up compared the same three development clips (`uca-01`, `uca-04`,
`uca-05`) with clearer action definitions and bounded output, first using 3B,
then 7B. Each run used a separate runtime database. No original results were
replaced. Zoomed focus views were **disabled** in both runs.

| Candidate | Completed jobs | Matched targets (3 total) | Ordinary-clip alerts | Wall time |
| --- | ---: | ---: | ---: | ---: |
| observable-v3 + 3B | 3/3 | 0 | 0 | 136.527s |
| observable-v3 + 7B | 3/3 | 0 | 0 | 174.537s |

Both candidates suppressed all events. Neither is an accepted detection
improvement. The 7B model and the new event policy were **not promoted**.
No additional videos or focus variants were tested after scope was narrowed.
This was development testing on previously inspected clips, not held-out testing.

The main service retains its previous 3B event policy and now uses bounded
response handling (`temporal-v3-bounded`): at most four events, bounded evidence
and text, 1536 initial output tokens, and one retry at 3072 tokens only when the
provider explicitly reports truncation. A second truncation or malformed
response fails visibly; it never becomes an empty safe result. Thirty tests pass,
including actual truncated-JSON retry and exhausted-retry cases. The production
policy's known detection failures remain unresolved.

For reproducibility, `evaluation.serve:create_app()` runs only in an explicitly
isolated experiment runtime and now requires `BEHAVIOR_EVENT_POLICY` and
`BEHAVIOR_FOCUS_VIEW` to be set explicitly; it no longer selects the rejected
`observable-v3` policy implicitly. Reproduce the rejected run with
`BEHAVIOR_EVENT_POLICY=observable-v3 BEHAVIOR_FOCUS_VIEW=0`. Production still
defaults to `BEHAVIOR_EVENT_POLICY=baseline` and `BEHAVIOR_FOCUS_VIEW=0`.
Raw run artifacts are `runs/v3-{3b,7b}-predictions.json` on the host. The candidate
7B model is still downloaded but not selected by the main service; digest
`5ced39dfa4bac325dc183dd1e4febaa1c46b3ea28bce48896c8e69c1e79611cc`.
[Official model metadata](https://ollama.com/library/qwen2.5vl:7b) and
[structured-output API reference](https://docs.ollama.com/capabilities/structured-outputs).

## Pending experiment — spatial crops under the production policy

Prompt policy and spatial crops were previously coupled: `BEHAVIOR_FOCUS_VIEW=1`
was rejected at startup unless the `observable-v3` policy was also selected, so
the crop hypothesis could only have been tested together with the prompt change
that suppressed every event. They are now independent settings, and the baseline
prompt carries the same two-panel explanation the experimental prompt had, so a
crop run is no longer implicitly a prompt change as well. Events and
`GET /api/system` report the variant as `temporal-v3-bounded-focus`.

This is a change to what can be tested, not a detection result. Focus views
remain unscored, remain disabled in production, and no accuracy claim follows
from this change. The motivating observation is that the production 3B model
emitted climbing on the close portrait-format [Mobius sample](dataset-survey.md)
while missing the wider UCA views; spatial scale is a plausible but unmeasured
explanation, and `behavior/views.py` deliberately returns the original frames
when tracks are widely separated, so some windows will be unchanged.

Procedure, on the compute host, against the frozen development clips
(`uca-01`, `uca-04`, `uca-05`) and never against held-out sources:

The importer writes one six-clip manifest. The development subset used by the
rejected `observable-v3` comparison already exists on the host as
`datasets/uca-development-v3.json`: the same `uca-01`, `uca-04` and `uca-05`
records, marked `split: development`, still pointing at the `datasets/uca-demo/`
media so checksums keep verifying. Reuse it, and keep `uca-02`, `uca-03` and
`uca-06` out of every tuning run. On a fresh host, filter the importer's manifest
to those three IDs and set their split to `development` rather than re-importing.

Then run the variant:

```bash
VESTA_EXPERIMENT_RUNTIME=runtime/experiments/focus \
BEHAVIOR_EVENT_POLICY=baseline BEHAVIOR_FOCUS_VIEW=1 \
  .venv/bin/gunicorn --bind 127.0.0.1:33264 --workers 1 --threads 8 \
  --timeout 360 'evaluation.serve:create_app()'
.venv/bin/python evaluation/replay.py datasets/uca-dev/manifest.json \
  --base-url http://127.0.0.1:33264 --output runs/focus-predictions.json
.venv/bin/python evaluation/evaluate.py datasets/uca-dev/manifest.json \
  runs/focus-predictions.json --ignore-action
```

Compare against a `BEHAVIOR_FOCUS_VIEW=0` run in a separate runtime directory
with the same model digest. Three previously inspected development clips cannot
promote a variant; a candidate that survives this comparison needs different
source videos before any production change.

### First attempt — server-side constrained-decoding failure

The first comparison run (2026-09-15, 17:01–17:05 host time, `qwen2.5vl:3b`,
three development clips per variant) did not produce a usable control. With focus
views on, `uca-01` and `uca-04` completed and `uca-05` failed; with focus views
off, all three clips failed. Every failure was the same explicit job error:
`event model response failed (attempt=1, model=qwen2.5vl:3b,
finish_reason=unknown): malformed JSON`.

The cause is in the inference server, not in Vesta. `journalctl --user -u
vesta-inference` recorded `got exception: Unexpected empty grammar stack after
accepting piece` on a request with 23,346 context tokens, after which the server
returned HTTP 200 with a body the client could not parse. A schema-constrained
text-only request to the same endpoint immediately afterwards succeeded, so
JSON-schema decoding is not broken in general; the observed failures were on the
long image-sequence requests. This is the same failure family as the
`incomplete JSON` job in the six-clip baseline above, which was previously
attributed to plausible output truncation. That attribution now looks incomplete:
at least one such failure is a grammar exception, not a token budget.

Why the control failed on all three clips while the focus run failed on one is
not established. The failures are adjacent in time, which is consistent with
server state persisting across requests, but nothing here measures that, and the
ordering was not randomized. The variant is not a demonstrated cause.

Restarting `vesta-inference` cleared the condition. Failed artifacts are retained
on the host as `runs/focus-{on,off}-predictions.json` and
`runs/focus-{on,off}-server.log`; the retry uses the `-2` suffix and runs the
control first. A failed job is never scored as a safe negative, so no metrics
were produced from this attempt.

### Result — focus views not promoted (2026-09-15)

Retry from a restarted inference service, application commit `31bd835`,
`qwen2.5vl:3b`, development clips only, control run first. Artifacts on the host
are `runs/focus-{off,on}-2-{predictions,exact,agnostic}.json`.

The control completed all three clips. The focus run failed `uca-05` with the
same constrained-decoding exception described above, logged twice at 17:11:55 at
23,725 context tokens, inside the focus window; the control window logged none.
`uca-05` has now failed under focus views in both attempts. That is a
correlation across two runs, not a demonstrated cause.

Like-for-like on the two clips both variants completed (`uca-01`, `uca-04`;
72.392 seconds; three labeled events):

| | Candidates | Exact TP/FP/FN | Agnostic TP/FP/FN | Agnostic precision | Agnostic recall |
| --- | ---: | --- | --- | ---: | ---: |
| Control `temporal-v3-bounded` | 13 | 0 / 13 / 3 | 3 / 10 / 0 | 23.1% | 100% |
| Focus `temporal-v3-bounded-focus` | 8 | 0 / 8 / 3 | 1 / 7 / 2 | 12.5% | 33.3% |

Focus views are **not promoted**. They matched fewer labeled events and scored
lower precision than the control, while also losing a clip to an inference
failure. Production keeps `BEHAVIOR_FOCUS_VIEW=0`. The spatial-scale explanation
for the wide-view misses is not supported by this run, and is not refuted either:
two clips and three labeled events cannot settle it, ordering was not randomized,
each variant ran once, and sampling is not deterministic.

The control is itself the first scored run of the current production
configuration on these clips. Across all three control clips (92.500 seconds):
0 true positives, 18 false positives and 3 false negatives under exact-action
matching; 3 true positives and 15 false positives under action-agnostic matching,
precision 16.7%. The ordinary walking clip produced 5 candidates from footage with
no labeled events.

Two observations follow, both consistent with the six-clip baseline:

- **Action labelling, not temporal localization, is the visible failure.** Both
  variants scored zero exact-action true positives while the control's
  action-agnostic recall was 100%. Every match was found under the wrong action
  name, and the control called almost everything `access_interaction`.
- **Action-agnostic recall here is close to vacuous.** Thirteen candidates across
  72 seconds will overlap nearly any labeled span. Read it beside the false-alert
  count, never alone. Alert volume, not missed events, is what this run exposes.

Neither observation is a campus-readiness measurement, and no threshold, model or
prompt change is justified by three previously inspected development clips.
