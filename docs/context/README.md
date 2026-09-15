# Project context

Working notes for the Vesta campus-camera project. These documents separate inspected repository facts from user-reported infrastructure and from future proposals.

- [Project status](project-status.md)
- [Roadmap](roadmap.md)
- [Evaluation and data handling](evaluation-and-data.md)
- [Decisions and provenance](decisions.md)
- [Compute host notes](compute-host.md) (maintained separately)
- [Compute hardware and network measurements](compute.md)

## Scope today

The default service is now an uploaded-video temporal behavior review workflow with durable jobs, evidence clips, human corrections, approved scene regions and a notification outbox. The legacy Flask application also contains RTSP viewing and person-triggered recording. The project has one camera in current use and no real footage available for a measured evaluation. No accuracy or campus-readiness claim is established.

Frigate is a reference for event review, recording retention, and detection workflows, not an adopted dependency or a commitment to replace this app. See [Frigate Review](https://docs.frigate.video/usage/review/) and [Frigate object detectors](https://docs.frigate.video/configuration/object_detectors/).

- [Implementation API contract](implementation-contract.md)
- [Evaluation tooling](../../evaluation/README.md)
- [Accepted implementation plan](implementation-plan.md)
- [Verification results and limitations](verification.md)

- [Public labeled-video benchmark](public-benchmark.md)

- [Broader dataset survey and Mobius sample test](dataset-survey.md)
