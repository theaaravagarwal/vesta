# Evaluation and data handling

## Current evidence limits

No real camera data is available, so accuracy is unknown. A successful replay or a plausible model response is not evidence of detection quality. Keep exploratory outputs separate from scored evaluation results. Report results by camera and event category; avoid a single aggregate score hiding weak slices.

## Evaluation protocol

- Define observable event classes and annotation rules before labeling; include benign look-alikes and difficult/uncertain cases.
- Use consented, purpose-limited footage. Minimize collection, restrict access, encrypt storage and transfer, apply the agreed pressure-based retention policy, and delete source media and derived clips when unprotected and space is needed. Confirmed incident evidence and pinned clips are protected from automatic eviction; reviewed corrections are retained for adjudication.
- Have two reviewers independently label event type and start/end timestamps on a subset; adjudicate disagreements and report agreement. Keep a held-out set that is not used to tune prompts or thresholds.
- Freeze and record code revision, model/checkpoint, prompt, detector confidence, sampling configuration, and inference endpoint/model version for each run.
- Measure event-level precision/recall, false alerts per camera-hour, missed severe-event rate, score calibration if scores are presented probabilistically, abstention/uncertain rate, reviewer time, and latency. Include confidence intervals and per-camera/condition slices.
- Evaluate detector and event classifier separately. Audit events absent from the YOLO-selected frames, since a downstream model cannot recover evidence that was never sampled.
- Run shadow mode first. Human reviewers adjudicate all candidate alerts; model output must not be treated as a finding about identity, intent, guilt, or authorization.

## Acceptance gates to set before evaluation

Agree in advance on minimum recall for safety-relevant observable events, maximum false alerts per camera-hour, maximum review burden, acceptable latency, confidence interval width/sample sufficiency, and camera-specific stop criteria. Determine thresholds with a development set and evaluate once on a held-out set. Repeat validation when cameras, placement, firmware, lighting, model, or prompts change materially.

## Reference patterns

Frigate's official documentation separates alerts, detections, and motion for review, and documents replay/debug tools for inspecting detection behavior: [Review](https://docs.frigate.video/usage/review/) and [Analyzing Object Detection](https://docs.frigate.video/troubleshooting/dummy-camera/). These are workflow references only; they do not validate this project's model or policy.

## Implemented evaluation tooling

See [evaluation/README.md](../../evaluation/README.md) for the small official MEVA fetcher, replay/export client, manifest validation, and temporal event scorer. The starter public sample is exploratory and unreviewed; it cannot establish precision/recall until independently labeled.
