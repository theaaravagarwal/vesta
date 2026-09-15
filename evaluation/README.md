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
