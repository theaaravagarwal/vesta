# Decisions and provenance

## Decisions

- Treat this work as a human-reviewed safety aid. Model scores are uncalibrated outputs, not probabilities or determinations of misconduct.
- Build evaluation and configuration hygiene before adding more cameras or operational automation.
- First deliverable is uploaded public-footage behavior analysis; target-camera footage remains unavailable. Add consented target-camera examples before any deployment-quality claim.
- Do not assume cross-host GPU/VRAM pooling. Measure actual inference placement and contention before selecting a multi-camera design.
- Frigate is an architectural/workflow reference, not a dependency decision.

## Infrastructure provenance

- Primary host verified directly through SSH during setup: `software@100.64.0.7`, Ubuntu 24.04, RTX 4090 Laptop GPU with 16 GB VRAM, 32 GB RAM; intended checkout path `/home/software/vesta`.
- Possible future host: `robotics@100.64.0.9`, Windows with SSH and WSL Alpine. This remains user-reported and uninspected. It is out of scope for implementation until separately authorized and validated.
- See [compute-host.md](compute-host.md) for the host notes maintained separately.

## Repository provenance

The configured Git remotes identify `theaaravagarwal/vesta` as origin and `flappybird1084/vesta` as upstream. No license file or license declaration was found in the inspected repository, so reuse and redistribution terms remain unresolved; do not infer permission from the fork relationship.

## Evidence boundaries

Repository behavior claims refer to the checked-out files and line references in [project status](project-status.md). Primary-host hardware was inspected; future-host notes are user-reported unless separately attributed in compute.md. No camera footage or evaluation labels were available during this documentation pass.

## Accepted implementation decisions (2026-09-15)

- Broad observable behavior candidates: climbing, unusual entry, possible tampering, and descriptions of other visible actions. Clothing and appearance alone are not alert criteria.
- Favor event recall initially, but report false alerts on routine people and preserve uncertainty.
- Scene boundaries are proposed by AI, then approved or edited with polygons. Capture time and schedules are optional; unknown time cannot imply after-hours activity.
- Timeline review cards provide confirm/dismiss/correction/pin. Notification outbox has no external delivery integration yet.
- Retention has no fixed expiry: clean under storage pressure, protecting confirmed incidents and pinned clips. Pause rather than delete protected evidence.
- Learning means collecting reviewed corrections; no automatic identity enrollment or self-training. Future cross-camera work is incident-only path linking with uncertain matches.
- Primary host runs inference and future training; those workloads share 16 GB VRAM and must be scheduled, not assumed concurrent.

## Experiment hygiene decisions (2026-09-15)

- Event prompt policy and spatial focus views are independent settings. Coupling them meant the one recorded focus-capable configuration also changed the prompt, and the resulting suppression of every event could not be attributed to either change.
- The isolated experiment app refuses to start without an explicit variant selection rather than inheriting production defaults.
- Prediction artifacts carry the serving model and config version, so a run that produced no events still records the configuration that produced none.
- Neither focus views nor any other candidate is promoted by these changes; production defaults are unchanged and the known detection failures remain unresolved.

