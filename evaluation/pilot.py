"""Scoring and CLI for the supervised Vesta pilot sessions.

Pilot artifacts keep manually staged crossings and dashboard observations in
separate arrays.  This module deliberately does not turn an absent or failed
observation into a negative result.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from evaluation.metrics import temporal_iou

PILOT_SCHEMA_VERSION = 1
REQUIRED_STAGED = 20
MIN_STAGED_MATCHES = 18
MAX_LATENCY_S = 10.0
MIN_ORDINARY_EXPOSURE_S = 3600.0
MAX_ORDINARY_FALSE_ALERTS = 3


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _span(row: dict[str, Any], name: str, *, allow_point: bool = False) -> None:
    if not _finite(row.get("start_s")) or not _finite(row.get("end_s")):
        raise ValueError(f"{name} requires finite start_s/end_s")
    if row["start_s"] < 0 or (row["end_s"] < row["start_s"] or (row["end_s"] == row["start_s"] and not allow_point)):
        raise ValueError(f"{name} has invalid span")


def validate_pilot_session(session: dict[str, Any]) -> None:
    if session.get("schema_version", PILOT_SCHEMA_VERSION) != PILOT_SCHEMA_VERSION:
        raise ValueError("schema_version must be 1")
    if not (session.get("session_id") or session.get("id")):
        raise ValueError("session_id is required")
    if session.get("split") not in ("development", "held_out", None):
        raise ValueError("split must be development or held_out when frozen")
    if session.get("ordinary_exposure_s") is not None and (not _finite(session.get("ordinary_exposure_s")) or session["ordinary_exposure_s"] < 0):
        raise ValueError("ordinary_exposure_s must be null or finite and non-negative")
    staged = session.get("staged_crossings")
    alerts = session.get("alerts")
    ordinary_intervals = session.get("ordinary_intervals", [])
    if not isinstance(staged, list) or not isinstance(alerts, list):
        raise ValueError("staged_crossings and alerts must be arrays")
    if not isinstance(ordinary_intervals, list):
        raise ValueError("ordinary_intervals must be an array")
    for interval in ordinary_intervals:
        _span(interval, "ordinary interval")
    for expected in staged:
        _span(expected, "staged crossing")
        for interval in ordinary_intervals:
            if expected["start_s"] < interval["end_s"] and interval["start_s"] < expected["end_s"]:
                raise ValueError("ordinary interval overlaps an expected crossing")
    seen = set()
    for row in staged:
        if not isinstance(row, dict) or not row.get("id"):
            raise ValueError("each staged crossing requires an id")
        if row["id"] in seen:
            raise ValueError("duplicate staged crossing id")
        seen.add(row["id"])
        _span(row, "staged crossing")
        if not row.get("direction"):
            raise ValueError("staged crossing requires direction")
    seen = set()
    for row in alerts:
        if not isinstance(row, dict) or not row.get("id"):
            raise ValueError("each alert requires an id")
        if row["id"] in seen:
            raise ValueError("duplicate alert id")
        seen.add(row["id"])
        _span(row, "alert", allow_point=True)
        if not row.get("direction"):
            raise ValueError("alert requires direction")
        for field in ("source_observed_at_s", "dashboard_render_ack_received_at_s"):
            if row.get(field) is not None and not _finite(row[field]):
                raise ValueError(f"{field} must be finite when present")


def _match(staged: list[dict[str, Any]], alerts: list[dict[str, Any]], iou_threshold: float):
    candidates = []
    for ai, alert in enumerate(alerts):
        for si, crossing in enumerate(staged):
            if alert.get("direction") != crossing.get("direction"):
                continue
            if alert["start_s"] == alert["end_s"]:
                overlap = 1.0 if crossing["start_s"] <= alert["start_s"] <= crossing["end_s"] else 0.0
            else:
                overlap = temporal_iou(alert, crossing)
            if overlap >= iou_threshold:
                candidates.append((overlap, si, ai))
    assigned_staged: set[int] = set()
    assigned_alerts: set[int] = set()
    pairs = []
    for overlap, si, ai in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
        if si in assigned_staged or ai in assigned_alerts:
            continue
        assigned_staged.add(si)
        assigned_alerts.add(ai)
        pairs.append((si, ai, overlap))
    return pairs, assigned_staged, assigned_alerts


def score_pilot_session(session: dict[str, Any], *, iou_threshold: float = 0.3) -> dict[str, Any]:
    """Return a reviewable pilot score and an explicit acceptance decision."""
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0,1]")
    validate_pilot_session(session)
    staged = session["staged_crossings"]
    raw_alerts = session["alerts"]
    failed_alerts = [a["id"] for a in raw_alerts if a.get("status", "done") == "failed"]
    alerts = [a for a in raw_alerts if a.get("status", "done") != "failed"]
    pairs, matched_staged, matched_alerts = _match(staged, alerts, iou_threshold)
    pair_by_alert = {ai: si for si, ai, _ in pairs}
    ordinary = [a for i, a in enumerate(alerts) if i not in matched_alerts and a.get("exposure", "ordinary") == "ordinary"]

    # An extra alert overlapping a staged crossing is visible as a duplicate,
    # rather than a second successful crossing or a fabricated ordinary false alert.
    duplicates = []
    for ai, alert in enumerate(alerts):
        if ai in matched_alerts:
            continue
        if any((
            (1.0 if crossing["start_s"] <= alert["start_s"] <= crossing["end_s"] else 0.0)
            if alert["start_s"] == alert["end_s"] else temporal_iou(alert, crossing)
        ) >= iou_threshold and alert["direction"] == crossing["direction"] for crossing in staged):
            duplicates.append(alert["id"])
    ordinary_false_alerts = len([a for a in ordinary if a["id"] not in duplicates])

    latencies = []
    unknown_latency = []
    late = []
    crossing_rows = []
    for si, crossing in enumerate(staged):
        ai = next((ai for s, ai, _ in pairs if s == si), None)
        if ai is None:
            crossing_rows.append({"id": crossing["id"], "status": "missing"})
            continue
        alert = alerts[ai]
        source = alert.get("source_observed_at_s")
        visible = alert.get("dashboard_render_ack_received_at_s")
        latency = visible - source if _finite(source) and _finite(visible) else None
        row = {"id": crossing["id"], "alert_id": alert["id"], "status": "matched", "latency_s": latency}
        if latency is None:
            unknown_latency.append(crossing["id"])
            row["status"] = "matched_latency_unknown"
        elif latency < 0:
            raise ValueError(f"alert {alert['id']} has negative dashboard latency")
        elif latency > MAX_LATENCY_S:
            late.append(crossing["id"])
            row["status"] = "matched_late"
        else:
            latencies.append(latency)
        crossing_rows.append(row)

    exposure = session.get("ordinary_exposure_s")
    exposure_known = _finite(exposure) and exposure >= 0
    gates = {
        "staged_count_at_least_20": len(staged) >= REQUIRED_STAGED,
        "staged_matches_at_least_90_percent": len(staged) >= REQUIRED_STAGED and len(pairs) >= MIN_STAGED_MATCHES and len(pairs) / len(staged) >= 0.9,
        "matched_latencies_known": not unknown_latency,
        "matched_latencies_at_most_10s": not late,
        "ordinary_exposure_at_least_1h": exposure_known and exposure >= MIN_ORDINARY_EXPOSURE_S,
        "ordinary_false_alerts_at_most_3": ordinary_false_alerts <= MAX_ORDINARY_FALSE_ALERTS,
    }
    return {
        "schema_version": PILOT_SCHEMA_VERSION,
        "session_id": session.get("session_id", session.get("id")),
        "split": session.get("split"),
        "passed": all(gates.values()),
        "gates": gates,
        "staged_crossings": len(staged),
        "matched_crossings": len(pairs),
        "missing_crossings": [r["id"] for r in crossing_rows if r["status"] == "missing"],
        "late_crossings": late,
        "unknown_latency_crossings": unknown_latency,
        "ordinary_exposure_s": exposure,
        "ordinary_false_alerts": ordinary_false_alerts,
        "duplicate_alerts": duplicates,
        "failed_alerts": failed_alerts,
        "gaps": session.get("gaps", []),
        "latency_s": latencies,
        "mean_latency_s": sum(latencies) / len(latencies) if latencies else None,
        "crossing_results": crossing_rows,
        "note": "Missing, failed, gaps, and unknown latency are reported explicitly and never count as safe negatives; unknown latency cannot pass. Render ack receipt is an upper bound, not a claimed exact client display timestamp.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a Vesta supervised pilot session")
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iou", type=float, default=0.3)
    args = parser.parse_args()
    result = score_pilot_session(json.loads(args.session.read_text()), iou_threshold=args.iou)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
