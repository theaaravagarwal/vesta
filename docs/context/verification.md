# Implementation verification

Verified 2026-09-15 on the primary compute host and local browser.

## Results

- 20 unit/API/evaluation tests pass, including job recovery/cancellation, protected retention, scene validation, relative media paths/HTTP Range, and evaluation matching/failure reporting. JavaScript syntax check passes.
- RTX 4090 Laptop CUDA detector inference and synthetic optimizer/backward step pass. The dedicated vision endpoint accepts the application's 16-image request at 32,768 context tokens. See [host details](compute-host.md) for versions and model digest.
- Three official MEVA public clips (20, 16, and 14 seconds) completed the final structured-schema replay in 32.266, 24.222, and 20.230 seconds, respectively, with zero candidate events and no job errors. This tiny sample is slower than realtime and does not establish campus camera capacity.
- Model scene suggestion completed and remained unapproved until human review. Suggested geometry is advisory and needs visual correction.
- Browser checks verified source playback and seeking on the host, desktop/mobile layout, and zero console errors. A separate explicitly synthetic local fixture verified correction persistence, confirmation, pinning, and normalized polygon drawing/approval.
- The host serves normalized H.264 MP4; byte-range requests returned HTTP 206. Both Vesta user services are enabled. No camera was contacted and the secondary computer was not configured.

## Reproducibility and discovered failures

Host replay manifest: `datasets/meva-demo/manifest.json`. Final machine-readable results: `runs/meva-schema-predictions.json`. Earlier smoke output is preserved as `runs/meva-initial-predictions.json`; earlier uploaded records remain distinguishable by creation time in the review queue. Those outputs used a permissive schema and are not current model results or labeled evidence.

The first smoke run exposed a string-versus-list model output error and empty-evidence non-events. Strict JSON schemas, runtime validation, and explicit exclusion of non-events fixed these observed failures. Integration also exposed Flask-relative media paths and source codec compatibility; absolute runtime paths and normalized MP4 fixed playback. Initial 8,192-token vision context was insufficient; 32,768 passed the actual image-sequence payload.

Public source and license: [MEVA data documentation](https://mevadata.org/resources/README-meva-kf1-data.html), [CC BY 4.0 terms](https://mevadata.org/resources/MEVA-data-license.txt). Downloader records source URLs and SHA-256 hashes. Structured-output implementation follows [Ollama documentation](https://docs.ollama.com/capabilities/structured-outputs).

## What is not established

The public clips have no independently reviewed ground truth in this project. Zero candidates is not proof of safety or accuracy. Climbing, boundary entry and tampering recall, precision, false alerts per camera-hour, and target-camera performance remain unmeasured. No target-camera footage was available. The evaluator explicitly reports missing labels and failed jobs rather than inventing metrics.

GPU training capability was checked with synthetic data; no behavior model was trained. Reviewed corrections export for later adjudication, not automatic training labels. Notifications remain an outbox without delivery. The service is loopback-only through SSH, with a single analysis worker; campus deployment still needs authentication, authorized camera data, labeled evaluation, measured capacity, operational ownership and incident response integration. Do not expose the current review API publicly.

## Repeat checks

```bash
uv run python -m unittest discover -s tests
node --check static/review.js
```

See [evaluation tooling](../../evaluation/README.md) for replay and metrics commands, and [compute operations](compute-host.md) for service access and GPU scheduling.

## Subsequent labeled public evaluation

The [six-video UCA baseline](public-benchmark.md) now provides an exploratory
publisher-labeled test. It exposed missed actions, false alerts on ordinary
activity, and one failed job. The earlier MEVA smoke success does not imply
behavior detection quality. See that report for complete counts and source provenance.

## Variant-selection change — 2026-09-15

Checked on the local development machine only, with no GPU, model endpoint or
camera involved. 38 unit/API/evaluation tests pass, including the new cases for a
focus-view run under the production event policy, the two-panel explanation
reaching the baseline prompt, `config_version` in `GET /api/system`, run
provenance echoed by the evaluator, and the experiment app refusing to start
without an explicit variant.

Not established by this change: any detection result. No replay, no host run and
no model inference were performed, focus views remain unscored and disabled in
production, and the failures recorded in the [public benchmark](public-benchmark.md)
are unchanged. What changed is that the spatial-crop hypothesis can now be tested
without simultaneously changing the event prompt.

## Focus-view comparison executed — 2026-09-15

Run on the primary compute host against the three development clips, control
first, isolated experiment runtimes, production database and web service
untouched. Both variants completed; focus views were **not promoted** and remain
disabled in production. Counts, artifacts and caveats are in the
[public benchmark](public-benchmark.md).

Newly established by this run: the `malformed JSON` job failures are at least
partly a server-side constrained-decoding exception (`Unexpected empty grammar
stack`) on long image-sequence requests, returned with HTTP 200, not only output
truncation as previously supposed. Vesta surfaced every instance as an explicit
job error and never as an empty result. A restart of `vesta-inference` cleared a
state in which the failure repeated on every request.

Not established: any accuracy, capacity or readiness claim. Two clips and three
labeled events decided nothing about spatial crops; the exact-action failure and
the alert volume are unresolved.
