"""Build the fixed six-clip UCA demonstration manifest without executing dataset code.

The command is intentionally dry-run by default.  Downloads are bounded, streamed
to temporary files, SHA-256 verified from Hugging Face LFS metadata, and ffmpeg is
used only to transcode selected source ranges into neutral output names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

GITHUB_REPO = "jia-wang-11/Surveillance-Video-Understanding-dataSet"
ANNOTATION_PATH = "UCF%20Annotation/json/UCFCrime_Test.json"
ANNOTATION_REVISION = "e478cb5b2277c6b7cf6a1dc4691021592003e216"
HF_REPO = "mteb/ucf-crime"
HF_REVISION = "8d783bb55269300114f9773a3c068b48a4940e4a"
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

# These source time spans are the fixed, publisher-annotation adaptation.  They
# are deliberately not inferred from file names or reused as model prompt text.
SELECTION = [
    (
        "Burglary081",
        None,
        [
            (4.9, 8.6, "object_tampering", "annotation index 1"),
            (9.3, 13.3, "climbing", "annotation index 2"),
        ],
    ),
    (
        "Burglary084",
        (0.0, 70.0),
        [
            (0.4, 40.5, "climbing", "annotation index 0"),
            (53.1, 63.1, "climbing", "annotation index 2"),
            (63.1, 83.1, "boundary_entry", "annotation index 3; clipped by crop"),
        ],
    ),
    (
        "Burglary076",
        (80.0, 150.0),
        [
            (87.9, 127.0, "object_tampering", "annotation index 4"),
            (125.9, 147.0, "boundary_entry", "annotation index 5"),
        ],
    ),
    (
        "Vandalism034",
        None,
        [
            (
                39.6,
                58.0,
                "object_tampering",
                "annotation indices 4 and 7 merged: nearby repeated action",
            )
        ],
    ),
    ("Normal_Videos609", (0.0, 20.13), []),
    ("Normal_Videos610", (0.0, 28.12), []),
]


ANNOTATION_INDICES = {
    "Burglary081": [[1], [2]],
    "Burglary084": [[0], [2], [3]],
    "Burglary076": [[4], [5]],
    "Vandalism034": [[4, 7]],
    "Normal_Videos609": [],
    "Normal_Videos610": [],
}


def validate_publisher_events(source_id, events, published):
    groups = ANNOTATION_INDICES[source_id]
    if len(events) != len(groups):
        raise ValueError("annotation mapping count differs")
    for (start, end, *_), indices in zip(events, groups):
        spans = [published["timestamps"][i] for i in indices]
        if (
            abs(start - min(t[0] for t in spans)) > 0.001
            or abs(end - max(t[1] for t in spans)) > 0.001
        ):
            raise ValueError(f"publisher indexed timing differs for {source_id}")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def pinned_commit() -> str:
    return ANNOTATION_REVISION


def hf_tree(revision: str = HF_REVISION) -> list[dict]:
    categories = ("Burglary", "Vandalism", "Training_Normal")
    return [
        item
        for category in categories
        for item in get_json(
            f"https://huggingface.co/api/datasets/{HF_REPO}/tree/{revision}/videos/{category}?limit=1000"
        )
    ]


def lfs_file(tree: list[dict], source_id: str) -> dict:
    matches = [
        x
        for x in tree
        if Path(x.get("path", "")).stem == f"{source_id}_x264" and x.get("lfs")
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one LFS video for {source_id}, found {len(matches)}"
        )
    record = matches[0]
    size = int(record["lfs"]["size"])
    if size > MAX_FILE_BYTES:
        raise ValueError(f"{source_id} exceeds per-file download limit")
    return record


def stream_download(
    url: str, destination: Path, expected_sha256: str | None, limit: int
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    total = 0
    try:
        with (
            urllib.request.urlopen(url, timeout=60) as response,
            temp.open("wb") as out,
        ):
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError("download exceeds size limit")
                digest.update(chunk)
                out.write(chunk)
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            raise ValueError("LFS SHA-256 mismatch")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def probe(path: Path) -> tuple[float, float]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "format=duration:stream=avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    duration = float(raw["format"]["duration"])
    rate = raw["streams"][0].get("avg_frame_rate", "0/1")
    a, b = rate.split("/")
    return duration, float(a) / float(b) if float(b) else 0.0


def duration_ok(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(0.5, expected * 0.01)


def adapt_events(events, crop, duration):
    offset = crop[0] if crop else 0.0
    limit = min(duration, crop[1] - crop[0]) if crop else duration
    adapted = []
    for start, end, action, rationale in events:
        start = max(start, offset) - offset
        end = min(end, offset + limit) - offset
        if end > start:
            adapted.append(
                {
                    "action": action,
                    "start_s": round(start, 3),
                    "end_s": round(end, 3),
                    "mapping_rationale": rationale,
                }
            )
    return adapted


def neutral_manifest(
    root: Path, annotation_revision: str, hf_revision: str, tree: list[dict]
) -> dict:
    clips = []
    for number, (source_id, crop, events) in enumerate(SELECTION, 1):
        source = lfs_file(tree, source_id)
        expected = (crop[1] - crop[0]) if crop else None
        source_ucf_split = "test" if source_id == "Burglary076" else "train"
        clips.append(
            {
                "id": f"uca-{number:02d}",
                "source_id": source_id,
                "source_url": f"https://huggingface.co/datasets/{HF_REPO}/resolve/{hf_revision}/{source['path']}",
                "license": "UCA README states Apache-2.0 and academic/research-only availability; HF mirror does not specify media license. Local research evaluation only; do not redistribute raw media or annotations.",
                "session_group": source_id,
                "split": "exploratory",
                "source_ucf_split": source_ucf_split,
                "source_uca_split": "test",
                "path": f"datasets/uca-demo/sample{number:02d}.mp4",
                "duration_s": expected,
                "label_status": "reviewed",
                "label_provenance": "Publisher UCA/UCF-Crime temporal annotations, independently adapted to this action ontology. No training or prompt tuning uses this baseline.",
                "events": events,
                "source_crop_s": crop,
                "lfs_sha256": source["lfs"]["oid"].removeprefix("sha256:"),
                "lfs_size": source["lfs"]["size"],
            }
        )
    return {
        "schema_version": 1,
        "dataset": "UCA public six-clip demo",
        "github_annotation_commit": annotation_revision,
        "hf_revision": hf_revision,
        "clips": clips,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("datasets/uca-demo"))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        args.output.resolve().relative_to(Path(__file__).resolve().parents[1])
    except ValueError:
        parser.error("output must be inside the repository for relative replay paths")
    if args.output.exists() and any(args.output.iterdir()) and not args.overwrite:
        parser.error("output exists; use --overwrite explicitly")
    revision = pinned_commit()
    tree = hf_tree(HF_REVISION)
    manifest = neutral_manifest(args.output, revision, HF_REVISION, tree)
    if not args.download:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "github_annotation_commit": revision,
                    "hf_revision": HF_REVISION,
                    "clips": len(manifest["clips"]),
                },
                indent=2,
            )
        )
        return
    # Download/transcode is deliberately explicit; source annotation snapshot is retained.
    total = sum(c["lfs_size"] for c in manifest["clips"])
    if total > MAX_TOTAL_BYTES:
        raise ValueError("selection exceeds total download limit")
    args.output.mkdir(parents=True, exist_ok=True)
    annotation_url = (
        f"https://raw.githubusercontent.com/{GITHUB_REPO}/{revision}/{ANNOTATION_PATH}"
    )
    stream_download(
        annotation_url, args.output / "UCFCrime_Test.json", None, MAX_FILE_BYTES
    )
    annotation_bytes = (args.output / "UCFCrime_Test.json").read_bytes()
    annotation = json.loads(annotation_bytes)
    manifest["annotation_url"] = annotation_url
    manifest["annotation_sha256"] = hashlib.sha256(annotation_bytes).hexdigest()
    repo_root = Path(__file__).resolve().parents[1]
    for clip in manifest["clips"]:
        published = annotation[clip["source_id"] + "_x264"]
        validate_publisher_events(clip["source_id"], clip["events"], published)
        clip["publisher_annotation_indices"] = ANNOTATION_INDICES[clip["source_id"]]
        source = args.output / "sources" / Path(clip["source_url"]).name
        if (
            not source.exists()
            or hashlib.sha256(source.read_bytes()).hexdigest() != clip["lfs_sha256"]
        ):
            stream_download(
                clip["source_url"], source, clip["lfs_sha256"], MAX_FILE_BYTES
            )
        source_duration, source_fps = probe(source)
        if not duration_ok(source_duration, published["duration"]):
            raise ValueError(
                f"source duration mismatch for {clip['source_id']}: {source_duration} vs {published['duration']}"
            )
        clip["source_duration_s"] = source_duration
        clip["source_fps"] = source_fps
        clip["publisher_duration_s"] = published["duration"]
        crop = clip["source_crop_s"]
        if crop and crop[1] > source_duration + 0.5:
            raise ValueError(f"crop exceeds source duration for {clip['source_id']}")
        target = args.output / Path(clip["path"]).name
        command = ["ffmpeg", "-v", "error", "-y"]
        if crop:
            command += ["-ss", str(crop[0]), "-to", str(crop[1])]
        command += [
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(target),
        ]
        subprocess.run(command, check=True)
        actual, _ = probe(target)
        expected = (crop[1] - crop[0]) if crop else source_duration
        if not duration_ok(actual, expected):
            raise ValueError(
                f"derived duration mismatch for {clip['source_id']}: {actual} vs {expected}"
            )
        clip["duration_s"] = actual
        clip["events"] = adapt_events(clip["events"], crop, actual)
        clip["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        clip["path"] = str(target.resolve().relative_to(repo_root))
        print(
            f"Prepared {clip['id']}: {actual:.3f}s, {len(clip['events'])} targets",
            flush=True,
        )
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
