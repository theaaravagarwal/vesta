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

For reproducibility, `evaluation.serve:create_app()` selects the rejected
`observable-v3` policy only in an explicitly isolated experiment runtime.
Production defaults to `BEHAVIOR_EVENT_POLICY=baseline`. Optional focus views
require the experimental policy and remain disabled; they have not been scored.
Raw run artifacts are `runs/v3-{3b,7b}-predictions.json` on the host. The candidate
7B model is still downloaded but not selected by the main service; digest
`5ced39dfa4bac325dc183dd1e4febaa1c46b3ea28bce48896c8e69c1e79611cc`.
[Official model metadata](https://ollama.com/library/qwen2.5vl:7b) and
[structured-output API reference](https://docs.ollama.com/capabilities/structured-outputs).
