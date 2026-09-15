# Design

<!-- impeccable:design-schema 1 -->

## Visual World

Vesta's review surface extends the existing calm, warm monitoring interface: paper-toned canvas, ink-like text, restrained amber for the active review action, and dark evidence media. The workbench should feel dependable during careful review, with color reserved for state and action.

## Surface: Review workspace

Operate mode. The layout places the video and event timeline at the center of the task, a compact video queue on the left, and decision/context controls on the right. On narrow screens the queue and context move below the evidence instead of collapsing the task into a modal.

## Type and Color

Use the local system sans stack for compact product UI. The queue title is 24px, section headings 17px, and body text 14px, giving the primary heading a clear size step. Use the monospace stack only for timestamps and metadata. Neutral surfaces carry most hierarchy; amber marks the primary action/current item, green denotes confirmed, red denotes a dismissed or failed state, and blue marks advisory scene work.

## Components

Cards use a 14px radius, a single quiet border, and no decorative shadow. Buttons, inputs, badges, timeline marks, and polygon handles share clear focus treatment and semantic state colors. SVG line icons use a consistent rounded 1.8px stroke.

## Motion

Motion is limited to progress and disclosure feedback, within 180ms. It communicates upload/analysis state and panel changes; it is removed for reduced-motion preferences.

## Content Rules

Never render model strings with HTML. Event uncertainty is written as an explanation, never a numeric score. Capture time, schedules, and notification delivery status remain absent or explicitly unknown until supplied by the service or reviewer.
