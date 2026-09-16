"""Event-level, one-to-one temporal matching for reviewed evaluation manifests."""

from __future__ import annotations

import math


def temporal_iou(a, b):
    overlap = max(0.0, min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"]))
    union = max(a["end_s"], b["end_s"]) - min(a["start_s"], b["start_s"])
    return overlap / union if union > 0 else 0.0


def wilson(successes, total):
    if not total:
        return None
    z = 1.96
    p = successes / total
    divisor = 1 + z * z / total
    center = (p + z * z / (2 * total)) / divisor
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return [max(0, center - radius), min(1, center + radius)]


def match_events(expected, predicted, threshold=0.3, ignore_action=False):
    # Maximum-cardinality matching avoids counting one truth event twice.
    edges = [
        [
            j
            for j, truth in enumerate(expected)
            if (ignore_action or pred["action"] == truth["action"])
            and temporal_iou(pred, truth) >= threshold
        ]
        for pred in predicted
    ]
    assigned = {}

    def augment(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in assigned or augment(assigned[j], seen):
                assigned[j] = i
                return True
        return False

    for i in range(len(predicted)):
        augment(i, set())
    return [(j, i) for j, i in assigned.items()]


def validate_manifest(manifest):
    if manifest.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    seen, group_splits = set(), {}
    for clip in manifest["clips"]:
        if clip["id"] == "run":
            raise ValueError("'run' is reserved for prediction run metadata")
        if clip["id"] in seen:
            raise ValueError("duplicate clip ID")
        seen.add(clip["id"])
        for field in ("source_url", "license", "session_group", "split", "path"):
            if not clip.get(field):
                raise ValueError(f"missing {field}")
        if clip["split"] not in ("development", "held_out", "exploratory"):
            raise ValueError("invalid split")
        group = clip["session_group"]
        if group in group_splits and group_splits[group] != clip["split"]:
            raise ValueError(f"session crosses splits: {group}")
        group_splits[group] = clip["split"]
        if clip.get("label_status") not in ("reviewed", "unreviewed"):
            raise ValueError("label_status must be explicit")
        if clip.get("label_status") == "reviewed":
            duration = clip.get("duration_s", 0)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("reviewed clips require a positive duration")
            for event in clip.get("events", []):
                if (
                    not event.get("action")
                    or not 0 <= event["start_s"] < event["end_s"] <= duration
                ):
                    raise ValueError("invalid labeled event span")


def score(manifest, predictions, threshold=0.3, ignore_action=False):
    """Score predictions and report event, footage-coverage, and timing metrics.

    Event metrics are conditional on reviewed clips whose prediction completed
    successfully. ``clip_outcomes`` and ``coverage`` keep missing, failed, and
    unreviewed footage visible instead of treating it as safe footage. Ordinary
    false alerts use only successful reviewed clips with no labeled events; the
    corresponding rate is per video hour, with its denominator exposed as
    ``ordinary_reviewed_successful_seconds``. Processing summaries are emitted
    only when predictions contain finite, non-negative ``elapsed_s`` values.
    """
    validate_manifest(manifest)
    expected_ids = {c["id"] for c in manifest["clips"]}
    # ``run`` carries model/config provenance written by replay.py; it is echoed
    # with the results so a score is never separated from the variant that
    # produced it. Older prediction files simply have no run metadata.
    run = predictions.get("run") if isinstance(predictions.get("run"), dict) else None
    predictions = {k: v for k, v in predictions.items() if k != "run" or run is None}
    if set(predictions) - expected_ids:
        raise ValueError("predictions contain unknown clip IDs")
    tp = fp = fn = 0
    by_action = {}
    seconds = 0.0
    errors, unreviewed, missing, evaluated = [], [], [], []
    start_errors, end_errors = [], []
    clip_outcomes = []
    ordinary_seconds = 0.0
    ordinary_clips = 0
    ordinary_fp = 0
    elapsed_values = []
    coverage = {
        "reviewed_successful": {"clips": 0, "seconds": 0.0},
        "failed": {"clips": 0, "seconds": 0.0},
        "missing": {"clips": 0, "seconds": 0.0},
        "unreviewed": {"clips": 0, "seconds": 0.0},
    }
    for clip in manifest["clips"]:
        cid = clip["id"]
        reviewed = clip["label_status"] == "reviewed"
        duration = clip.get("duration_s")
        duration_valid = isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0
        outcome = {
            "id": cid,
            "label_status": clip["label_status"],
            "duration_s": duration if duration_valid else None,
            "status": None,
            "true_positive": None,
            "false_positive": None,
            "false_negative": None,
        }
        if not reviewed:
            unreviewed.append(cid)
            coverage["unreviewed"]["clips"] += 1
            if duration_valid:
                coverage["unreviewed"]["seconds"] += duration
        prediction = predictions.get(cid)
        if prediction is not None:
            elapsed = prediction.get("elapsed_s")
            if (
                isinstance(elapsed, (int, float))
                and not isinstance(elapsed, bool)
                and math.isfinite(elapsed)
                and elapsed >= 0
            ):
                elapsed_values.append(elapsed)
        if prediction is None:
            missing.append(cid)
            outcome["status"] = "missing"
            coverage["missing"]["clips"] += 1
            if duration_valid:
                coverage["missing"]["seconds"] += duration
            clip_outcomes.append(outcome)
            continue
        if prediction.get("status") != "done":
            errors.append(cid)
            outcome["status"] = "failed"
            coverage["failed"]["clips"] += 1
            if duration_valid:
                coverage["failed"]["seconds"] += duration
            clip_outcomes.append(outcome)
            continue
        if not reviewed:
            outcome["status"] = "unreviewed"
            clip_outcomes.append(outcome)
            continue
        events = prediction.get("events", [])
        for event in events:
            if (
                not event.get("action")
                or not 0 <= event["start_s"] < event["end_s"] <= clip["duration_s"]
            ):
                raise ValueError(f"invalid prediction span for {cid}")
        truth = clip.get("events", [])
        pairs = match_events(truth, events, threshold, ignore_action=ignore_action)
        tp += len(pairs)
        fp += len(events) - len(pairs)
        fn += len(truth) - len(pairs)
        outcome.update(
            status="reviewed_successful",
            true_positive=len(pairs),
            false_positive=len(events) - len(pairs),
            false_negative=len(truth) - len(pairs),
        )
        clip_outcomes.append(outcome)
        if not ignore_action:
            for action in sorted({e["action"] for e in truth + events}):
                stats = by_action.setdefault(
                    action,
                    {"true_positive": 0, "false_positive": 0, "false_negative": 0},
                )
                matched = sum(truth[j]["action"] == action for j, i in pairs)
                stats["true_positive"] += matched
                stats["false_positive"] += (
                    sum(e["action"] == action for e in events) - matched
                )
                stats["false_negative"] += (
                    sum(e["action"] == action for e in truth) - matched
                )
        seconds += clip["duration_s"]
        evaluated.append(cid)
        coverage["reviewed_successful"]["clips"] += 1
        coverage["reviewed_successful"]["seconds"] += clip["duration_s"]
        if not truth:
            ordinary_clips += 1
            ordinary_seconds += clip["duration_s"]
            ordinary_fp += len(events)
        for j, i in pairs:
            start_errors.append(abs(truth[j]["start_s"] - events[i]["start_s"]))
            end_errors.append(abs(truth[j]["end_s"] - events[i]["end_s"]))
    result = {
        "run": run,
        "by_action": by_action,
        "matching_mode": "action_agnostic_temporal"
        if ignore_action
        else "action_aware_temporal",
        "temporal_iou_threshold": threshold,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "precision_wilson_95": wilson(tp, tp + fp),
        "recall_wilson_95": wilson(tp, tp + fn),
        "false_alerts_per_video_hour": fp / (seconds / 3600) if seconds else None,
        "ordinary_false_alerts": ordinary_fp,
        "ordinary_false_alerts_per_video_hour": ordinary_fp / (ordinary_seconds / 3600)
        if ordinary_seconds
        else None,
        "ordinary_reviewed_successful_clips": ordinary_clips,
        "ordinary_reviewed_successful_seconds": ordinary_seconds,
        "mean_start_error_s": sum(start_errors) / len(start_errors)
        if start_errors
        else None,
        "mean_end_error_s": sum(end_errors) / len(end_errors) if end_errors else None,
        "evaluated_seconds": seconds,
        "evaluated_clips": evaluated,
        "unreviewed_clips": unreviewed,
        "missing_predictions": missing,
        "failed_predictions": errors,
        "clip_outcomes": clip_outcomes,
        "coverage": coverage,
        "complete": not missing and not errors and not unreviewed,
        "note": "Conditional on successfully analyzed, reviewed clips. Failures are not safe negatives. Wilson intervals treat events as independent; correlated camera/session data needs grouped analysis.",
    }
    if elapsed_values:
        result["processing"] = {
            "clips_with_elapsed_s": len(elapsed_values),
            "elapsed_s_total": sum(elapsed_values),
            "elapsed_s_mean": sum(elapsed_values) / len(elapsed_values),
        }
    return result
