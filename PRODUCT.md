# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Reviewers examine short, consented single-camera video after it is uploaded. Their task is to decide whether an observable event is supported by the available evidence and record a correction when it is not.

## Product Purpose

Vesta is a human-reviewed safety aid for turning uploaded video into candidate observable events and a reviewable scene context. Success is a reviewer reaching a defensible decision with the source and clip evidence close at hand.

## Positioning

The product presents model output as an advisory candidate with evidence, uncertainty, and a correction path; it does not present an automated finding about identity, intent, guilt, or authorization.

## Operating Context

Review happens after video upload in a browser, often while an analysis job is still progressing. Capture time may be unknown. Reviewers can define advisory scene regions and optionally apply a schedule only when enough context exists.

## Capabilities and Constraints

- Upload one video and queue analysis; there is no camera access in this surface.
- Review events by seeking the source video or opening a compressed evidence clip.
- Confirm, dismiss, pin, and correct event records.
- Define normalized polygon regions as fence, entrance, restricted, or other, then approve the advisory scene context.
- AI scene suggestions remain editable and must not overwrite human edits.
- A job can be cancelled while active or re-run when allowed by the service.
- Storage and worker state must be visible. Notification outbox items remain pending integration, never delivered.
- The interface must not invent threat scores, probability claims, capture timestamps, schedules, or model availability.

## Evidence on Hand

The implementation contract and project context provide API response shapes and safety boundaries. No real camera footage, model evaluation labels, benchmark metrics, or delivery integration are available.

## Product Principles

- Put the evidence and reviewer decision in the same working area.
- Keep uncertainty specific and visible.
- Preserve the difference between model suggestion, human decision, and approved scene context.
- Make unknown information explicit instead of filling it in.
- Keep recovery actions close to failures and job state.

## Accessibility & Inclusion

The review surface supports keyboard operation, visible focus, semantic labels, status announcements, responsive reflow, and controls that do not rely on color alone.
