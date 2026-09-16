"""Export private per-window inference diagnostics; no media or public API route."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from collections import Counter


def export(db: Path, video_id: str) -> dict:
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        def has_table(name: str) -> bool:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone() is not None

        if not has_table("videos"):
            raise RuntimeError(
                "candidate diagnostics unavailable: database has no videos table; "
                "initialize the behavior database before exporting"
            )
        video = conn.execute("SELECT id,name,status,duration_s FROM videos WHERE id=?", (video_id,)).fetchone()
        if video is None:
            raise ValueError(f"video not found: {video_id}")
        windows_available = has_table("analysis_windows")
        traces_available = has_table("candidate_traces")
        jobs_available = has_table("jobs")
        windows = []
        candidates = []
        if windows_available:
            window_columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_windows)")}
            optional = ",model,config_version" if {"model", "config_version"} <= window_columns else ""
            windows = [dict(row) for row in conn.execute(
                "SELECT job_id,window_index,start_s,end_s,frame_count,status,candidate_count" + optional +
                " FROM analysis_windows WHERE video_id=? ORDER BY job_id,window_index", (video_id,)
            )]
        if traces_available:
            candidates = [dict(row) for row in conn.execute(
                "SELECT job_id,window_index,start_s,end_s,action,description,evidence,uncertainty,"
                "decision,reason,model,config_version,created_at FROM candidate_traces "
                "WHERE video_id=? ORDER BY job_id,window_index,start_s", (video_id,)
            )]
        jobs = {}
        if jobs_available:
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            job_filter = " AND type='analysis'" if "type" in job_columns else ""
            jobs = {
                row["id"]: {"status": row["status"], "error": row["error"]}
                for row in conn.execute(
                    "SELECT id,status,error FROM jobs WHERE video_id=?" + job_filter + " ORDER BY id", (video_id,)
                )
            }
            if "type" in job_columns:
                scene_ids = {
                    row[0] for row in conn.execute(
                        "SELECT id FROM jobs WHERE video_id=? AND type<>'analysis'", (video_id,)
                    )
                }
                windows = [row for row in windows if row["job_id"] not in scene_ids]
                candidates = [row for row in candidates if row["job_id"] not in scene_ids]
    for candidate in candidates:
        candidate["evidence"] = json.loads(candidate["evidence"])

    job_ids = set(jobs) | {row["job_id"] for row in windows} | {row["job_id"] for row in candidates}
    summaries = []
    for job_id in sorted(job_ids):
        job_windows = [row for row in windows if row["job_id"] == job_id]
        job_candidates = [row for row in candidates if row["job_id"] == job_id]
        done = sum(row["status"] == "done" for row in job_windows)
        errors = sum(row["status"] == "error" for row in job_windows)
        total = len(job_windows)
        reasons = Counter(row["reason"] for row in job_candidates if row["reason"])
        actions = Counter(row["action"] for row in job_candidates if row["action"] is not None)
        accepted = sum(row["decision"] == "accepted" for row in job_candidates)
        rejected = sum(row["decision"] == "rejected" for row in job_candidates)
        summary = {
            "job_id": job_id,
            "status": jobs.get(job_id, {}).get("status"),
            "error": jobs.get(job_id, {}).get("error"),
            "windows_total": total,
            "windows_done": done,
            "windows_error": errors,
            "windows_incomplete": total - done - errors,
            "windows_zero_candidates": sum(
                row["status"] == "done" and (row["candidate_count"] or 0) == 0
                for row in job_windows
            ),
            "candidates_total": len(job_candidates),
            "candidates_accepted": accepted,
            "candidates_rejected": rejected,
            "reason_counts": dict(sorted(reasons.items())),
            "action_counts": dict(sorted(actions.items())),
            # An available trace with zero candidates is a valid model result;
            # an absent historical table cannot support that conclusion.
            "trace_status": "unavailable" if not traces_available or not windows_available else (
                "not_recorded" if not job_windows and not job_candidates else (
                    "partial" if jobs.get(job_id, {}).get("status") != "done"
                    or any(row["status"] != "done" for row in job_windows)
                    else "available"
                )
            ),
        }
        if not traces_available:
            summary["trace_error"] = (
                "candidate_traces table is unavailable in this database; historical "
                "candidate output cannot be distinguished from zero model output"
            )
        elif not windows_available:
            summary["trace_error"] = (
                "analysis_windows table is unavailable; candidate trace completeness "
                "cannot be established"
            )
        summaries.append(summary)
    availability_errors = []
    if not jobs_available:
        availability_errors.append("jobs table is unavailable; job status and error are unavailable")
    if not windows_available:
        availability_errors.append("analysis_windows table is unavailable; window summaries are unavailable")
    if not traces_available:
        availability_errors.append(
            "candidate_traces table is unavailable; historical candidate output cannot be distinguished from zero model output"
        )
    return {
        "video": dict(video),
        "windows": windows,
        "candidates": candidates,
        "availability": {
            "jobs": "available" if jobs_available else "unavailable",
            "analysis_windows": "available" if windows_available else "unavailable",
            "candidate_traces": "available" if traces_available else "unavailable",
            "errors": availability_errors,
        },
        "job_summaries": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video_id")
    parser.add_argument("--db", type=Path, default=Path("runtime/behavior/behavior.sqlite3"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(export(args.db, args.video_id), indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
