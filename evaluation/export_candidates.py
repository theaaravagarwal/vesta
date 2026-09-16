"""Export private per-window inference diagnostics; no media or public API route."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def export(db: Path, video_id: str) -> dict:
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        video = conn.execute("SELECT id,name,status,duration_s FROM videos WHERE id=?", (video_id,)).fetchone()
        if video is None:
            raise ValueError(f"video not found: {video_id}")
        windows = [dict(row) for row in conn.execute(
            "SELECT job_id,window_index,start_s,end_s,frame_count,status,candidate_count "
            "FROM analysis_windows WHERE video_id=? ORDER BY job_id,window_index", (video_id,)
        )]
        candidates = [dict(row) for row in conn.execute(
            "SELECT job_id,window_index,start_s,end_s,action,description,evidence,uncertainty,"
            "decision,reason,model,config_version,created_at FROM candidate_traces "
            "WHERE video_id=? ORDER BY job_id,window_index,start_s", (video_id,)
        )]
    for candidate in candidates:
        candidate["evidence"] = json.loads(candidate["evidence"])
    return {"video": dict(video), "windows": windows, "candidates": candidates}


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
