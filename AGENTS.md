# Vesta Agent Guide

This file is the shared project guide for coding agents. Keep project facts and
engineering rules here so Codex and Claude Code work from the same source.

## Project

Vesta is a Python 3.12+ Flask application for reviewing uploaded or live video,
detecting people, generating candidate observable events, and recording human
review decisions. Read `README.md` for setup and endpoints, `PRODUCT.md` for
product boundaries, `DESIGN.md` for UI direction, and `docs/context/README.md`
for current architecture, decisions, and roadmap.

Key areas:

- `main.py`: original unified Flask application and live/recording flows.
- `behavior/`: review workflow, persistence, analysis jobs, and API behavior.
- `templates/` and `static/`: browser UI.
- `evaluation/`: manifests, replay, and evaluation metrics.
- `tests/`: fast unit and API behavior tests.
- `person-detect/` and `video-understanding/`: standalone/reference experiments.
- `deploy/` and `scripts/`: host services and compute-host utilities.

## Commands

- Install or update the environment: `uv sync`
- Run the app: `./run.sh`
- Run the focused test suite: `uv run python -m unittest discover -s tests`
- Run a single test module: `uv run python -m unittest tests.test_behavior`

The local service normally listens at `http://127.0.0.1:33263`. GPU inference,
RTSP access, ffmpeg, and the llama.cpp-compatible endpoint are environment
dependent; do not treat their absence as a unit-test failure.

## Engineering Rules

- Preserve existing behavior unless the task explicitly changes it.
- Use `uv` and keep `pyproject.toml` and `uv.lock` consistent when dependencies
  change.
- Add or update focused tests for behavior, persistence, API, or metric changes.
- Keep generated data and large artifacts out of git. This includes `runtime/`,
  uploads, recordings, model weights, datasets, caches, and local `.env` files.
- Never commit RTSP URLs, camera credentials, API tokens, private video, or host
  secrets. Document variables in example files with placeholder values.
- Treat `person-detect/` and `video-understanding/` as separate experiments
  unless a task explicitly integrates them into the main application.
- Avoid broad cleanup in the same change as a focused feature or fix.

## Product and Safety Boundaries

- Present model output as advisory candidate events, with evidence, uncertainty,
  and a human correction path.
- Do not infer or claim identity, intent, guilt, authorization, or other traits
  that are not directly observable in the video.
- Do not invent threat scores, probabilities, capture times, schedules, model
  availability, notification delivery, or other unavailable facts.
- Preserve the distinction between model suggestions, human decisions, and
  approved scene context.
- Keep reviewer actions keyboard accessible and do not encode state by color
  alone.

## Working With Other Agents

- Assume another agent or the user may have uncommitted work. Inspect the
  worktree first and preserve unrelated changes.
- Use this file for shared project guidance. Put Claude-only configuration under
  `.claude/` and Codex-only configuration under `.codex/` only when a real
  tool-specific need exists.
- Do not copy conversation memory, credentials, permission databases, caches, or
  session history between tools.
