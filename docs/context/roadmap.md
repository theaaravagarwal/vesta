# Roadmap

Each phase is gated on evidence from the preceding phase. The system remains advisory with a person reviewing alerts; it must not autonomously trigger disciplinary or emergency action.

## 1. Secure configuration and reproducible offline replay

Remove credential defaults, validate configuration at startup, avoid exposing secret values in logs/errors, and document safe local operation. Build a deterministic replay harness using consented, de-identified clips and annotations; pin model/config/prompt versions and record run metadata. Keep inference offline where feasible and document any endpoint/network traffic.

**Acceptance:** secret scan finds no active credentials; missing required config fails safely; fixed fixtures run repeatably; tests assert structured outputs and failure behavior; runtime reports detector/model/prompt versions and timings. This phase does not claim accuracy.

## 2. Observable events, uncertainty, and reviewed single-camera trial

Replace subjective intent/crime labels with observable events (for example, person enters area, running, contact-like motion, object carried), with evidence timestamps, confidence/uncertainty, and an explicit human-review state. Use a small, consented, single-camera dataset; reviewers independently label event intervals and benign confounders. Run in shadow mode and retain reviewer corrections.

**Acceptance:** pre-registered event definitions and review protocol; held-out clips; per-event precision/recall and false alerts per camera-hour with uncertainty intervals; detector miss audit; documented review burden and thresholds approved before any pilot alerting. Thresholds must reflect the intended use and be set before inspecting held-out results.

## 3. Bounded 4–8 camera pilot and durable processing

Only after phase 2 passes, introduce isolated per-camera capture/inference workers, a durable queue, and durable metadata/database plus object storage with access control and retention/deletion policy. Backpressure, restart recovery, camera health, and audit trails are pilot requirements. Do not assume VRAM can be pooled across machines; benchmark actual placement and contention.

**Acceptance:** sustained and peak-load tests at the target camera count; queue age, dropped-frame rate, storage growth, restart recovery, GPU memory, and alert latency stay within pre-agreed bounds. Per-camera shadow metrics and human review remain within phase 2 limits.

## 4. Campus rollout decision

Expand only after the pilot demonstrates stable operations and acceptable, camera-stratified false-alert and missed-event performance across lighting, weather, occlusion, and activity patterns. Maintain a staffed review path, retention controls, documented notice/consent, rollback, and incident response.

**Acceptance:** written approval of measured capacity, privacy/data governance, review staffing, and validation results; phased expansion with stop criteria. No current evidence satisfies this gate.
