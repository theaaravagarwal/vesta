"""Plan and validate the opt-in repeated door-contact experiment.

This module only describes the fixed offline comparison.  It does not download
media, call a model, or change the scored UCA manifest.  The experiment uses the
existing reviewed source clips and keeps ordinary door-use footage separate from
the scored ordinary controls when such footage is available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


POLICY = "door-contact-v1"
SAMPLE_FPS = 2.0
WINDOW_S = 8.0
STRIDE_S = 4.0
DOOR_CONTROL_ID = "meva-door-open-01"
DOOR_CONTROL_SOURCE_ID = "2018-03-09.10-30-00.10-35-00.hospital.G479"
DOOR_CONTROL_CLIP_SHA256 = "9907eda1431de30cb3f83b5443f230b3b8fed4c8088c8b1e84167f833d217f21"
DOOR_CONTROL_ANNOTATION_SHA256 = "a714ffa60d085e845aa55abe481bd1cd95de7977b9dd8ff56be204d173d66647"

# The positive is the only source span used to motivate this prompt change.
# Publisher annotation indices 4 and 7 were merged by the frozen importer.
POSITIVE = {
    "uca-04": {
        "source_id": "Vandalism034",
        "start_s": 39.6,
        "end_s": 58.0,
        "action": "object_tampering",
        "label_basis": "publisher indices 4 and 7 merged: nearby repeated action",
    }
}

# These are reviewed ordinary clips, but neither contains ordinary door use.
# They remain useful broad controls for standing/walking/cart false alerts.
ORDINARY_CONTROLS = {
    "uca-05": {"source_id": "Normal_Videos609", "door_use": False},
    "uca-06": {"source_id": "Normal_Videos610", "door_use": False},
}

PROMPT_CONTRACT = {
    "minimum_contact_moments": 2,
    "requires_release_between_contacts": True,
    "counts_adjacent_frames_as_one_contact": True,
    "routine_open_close_is_event": False,
    "infers_intent_or_authorization": False,
    "gate_change": False,
}

# Exact instruction used by the rejected isolated diagnostic.  It is retained
# here as an evidence artifact; ``behavior`` deliberately does not expose this
# policy after the candidate failed to improve detection.
OFFLINE_CONFIG_VERSION = "temporal-v4-door-contact-evidence2"
DOOR_CONTACT_PROMPT = (
    "Review the chronological frames as one temporal sequence. "
    "This is a narrow door/access-surface contact experiment. "
    "Return at most four concise events, one per continuous action. "
    "Only report an access-surface event when the same closed door, gate, window, or other access surface "
    "has at least two distinct, clearly visible contact moments in the ordered frames, such as a strike, kick, "
    "forceful shove, repeated pull, or pry attempt. Require a visible release or withdrawal before counting a "
    "second moment; adjacent frames from one sustained contact count as one moment. Describe the physical contact "
    "that is visible. "
    "Use object_tampering for visible repeated striking, kicking, prying, cutting, or other forceful impact on "
    "the surface; use access_interaction for repeated forceful pulls or pushes at a closed access point when no "
    "impact or damage is visible. A single touch, reaching, standing nearby, handling an item, ordinary handle use, "
    "or routine opening/closing and passing through a door must produce no event. "
    "Do not report ordinary door use even when a person is near the doorway. "
    'If two separate contact moments are not visible, return exactly {"events":[]}. '
    "Do not infer identity, intent, guilt, authorization, damage, or criminality. Do not call a surface damaged "
    "unless damage itself is visibly shown; uncertainty about intent does not erase a visible contact action. "
    "For each event give first and last supporting timestamps and one to three distinct evidence statements that "
    "name the visible contact and its target surface."
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _door_control_status(door_manifest: dict | None) -> dict:
    if door_manifest is None:
        return {
            "status": "unavailable",
            "reason": "existing reviewed ordinary clips contain no ordinary door-use span",
            "unreviewed_qualitative_sources_excluded_from_score": True,
        }
    clips = door_manifest.get("clips", [])
    if len(clips) != 1:
        raise ValueError("door-control manifest must contain exactly one clip")
    clip = clips[0]
    if clip.get("id") != DOOR_CONTROL_ID:
        raise ValueError(f"unexpected door-control clip: {clip.get('id')}")
    if clip.get("source_id") != DOOR_CONTROL_SOURCE_ID:
        raise ValueError("door-control source_id does not match the pinned MEVA source")
    if clip.get("label_status") != "reviewed" or clip.get("events"):
        raise ValueError("door-control clip must be reviewed with an empty alert event list")
    if clip.get("duration_s") != 10.0:
        raise ValueError("door-control clip must be the fixed 10-second crop")
    if clip.get("source_crop_s") != [286.0, 296.0]:
        raise ValueError("door-control crop does not match the pinned 286-296 s source span")
    if clip.get("sha256") != DOOR_CONTROL_CLIP_SHA256:
        raise ValueError("door-control crop checksum does not match the pinned source")
    if clip.get("annotation_sha256") != DOOR_CONTROL_ANNOTATION_SHA256:
        raise ValueError("door-control annotation checksum does not match the pinned source")
    return {
        "status": "available",
        "clip_id": clip.get("id"),
        "publisher_activity_note": clip.get("label_note"),
        "unreviewed_qualitative_sources_excluded_from_score": True,
    }


def build_plan(
    manifest: dict,
    manifest_path: Path | None = None,
    door_manifest: dict | None = None,
) -> dict:
    """Return a fixed, reviewable plan without changing ``manifest``."""
    clips = {clip["id"]: clip for clip in manifest.get("clips", [])}
    missing = sorted((set(POSITIVE) | set(ORDINARY_CONTROLS)) - set(clips))
    if missing:
        raise ValueError(f"experiment clips missing from manifest: {', '.join(missing)}")

    positive = []
    for clip_id, expected in POSITIVE.items():
        clip = clips[clip_id]
        if clip.get("source_id") != expected["source_id"]:
            raise ValueError(
                f"{clip_id} source_id changed: expected {expected['source_id']}"
            )
        event = next(
            (
                event
                for event in clip.get("events", [])
                if event.get("action") == expected["action"]
                and event.get("start_s") == expected["start_s"]
                and event.get("end_s") == expected["end_s"]
            ),
            None,
        )
        if clip.get("label_status") != "reviewed" or event is None:
            raise ValueError(
                f"{clip_id} must retain the frozen reviewed positive span before the experiment"
            )
        positive.append({"clip_id": clip_id, **expected})

    controls = []
    for clip_id, expected in ORDINARY_CONTROLS.items():
        clip = clips[clip_id]
        if clip.get("source_id") != expected["source_id"]:
            raise ValueError(
                f"{clip_id} source_id changed: expected {expected['source_id']}"
            )
        if clip.get("label_status") != "reviewed" or clip.get("events"):
            raise ValueError(f"{clip_id} must be a reviewed ordinary control with no events")
        controls.append(
            {
                "clip_id": clip_id,
                "source_id": expected["source_id"],
                "door_use": expected["door_use"],
                "duration_s": clip.get("duration_s"),
                "control_scope": "broad ordinary-scene false-alert control",
            }
        )

    return {
        "experiment": "repeated-door-contact-v1",
        "policy": POLICY,
        "sampling": {"fps": SAMPLE_FPS, "window_s": WINDOW_S, "stride_s": STRIDE_S},
        "positive": positive,
        "ordinary_controls": controls,
        "ordinary_door_use_control": {
            **_door_control_status(door_manifest),
        },
        "prompt_contract": PROMPT_CONTRACT,
        "manifest": {
            "path": str(manifest_path) if manifest_path else None,
            "sha256": (
                _sha256(manifest_path) if manifest_path and manifest_path.is_file() else None
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--door-control-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    door_manifest = (
        json.loads(args.door_control_manifest.read_text())
        if args.door_control_manifest
        else None
    )
    plan = build_plan(manifest, args.manifest, door_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
