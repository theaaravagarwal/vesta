"""Re-score recorded predictions under different event-merge gaps, offline.

One continuous action is reported once per overlapping analysis window, so a
single action can leave several near-adjacent candidates. This sweep re-merges
the events a run already produced and re-scores them, which isolates the
post-processing choice from model sampling: no inference runs and the comparison
is deterministic. It cannot show what a different gap would have made the model
say, only how the recorded output would have been grouped.

  python evaluation/merge_sweep.py datasets/uca-development-v3.json \\
      runs/focus-off-2-predictions.json --gaps 0 0.5 1 2 4
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from behavior import _merge

try:  # invoked as a script from the repository root, or imported as a module
    from metrics import score
except ImportError:  # pragma: no cover - import style only
    from evaluation.metrics import score


def _mergeable(event: dict) -> dict:
    """Merging needs the full event shape; older artifacts may omit list fields."""
    return {
        **event,
        "evidence": list(event.get("evidence") or []),
        "track_ids": list(event.get("track_ids") or []),
    }


def sweep(manifest: dict, predictions: dict, gaps, threshold: float = 0.3) -> list[dict]:
    rows = []
    for gap in gaps:
        merged = {
            cid: (
                value
                if cid == "run"
                else {
                    **value,
                    "events": _merge(
                        [_mergeable(e) for e in value["events"]], gap=gap
                    ),
                }
            )
            for cid, value in predictions.items()
        }
        exact = score(manifest, merged, threshold)
        agnostic = score(manifest, merged, threshold, ignore_action=True)
        rows.append(
            {
                "gap_s": gap,
                "candidates": sum(
                    len(v["events"]) for c, v in merged.items() if c != "run"
                ),
                "action_aware_true_positive": exact["true_positive"],
                "action_agnostic_true_positive": agnostic["true_positive"],
                "action_agnostic_false_positive": agnostic["false_positive"],
                "action_agnostic_precision": agnostic["precision"],
                "action_agnostic_recall": agnostic["recall"],
                "evaluated_clips": agnostic["evaluated_clips"],
                "failed_predictions": agnostic["failed_predictions"],
            }
        )
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("predictions", type=Path)
    p.add_argument("--gaps", type=float, nargs="+", default=[0, 0.5, 1, 2, 4])
    p.add_argument("--iou", type=float, default=0.3)
    p.add_argument("--out", type=Path)
    a = p.parse_args()
    rows = sweep(
        json.loads(a.manifest.read_text()),
        json.loads(a.predictions.read_text()),
        a.gaps,
        a.iou,
    )
    header = f"{'gap_s':>6} {'cands':>6} {'exactTP':>8} {'agnTP':>6} {'agnFP':>6} {'agnPrec':>8} {'agnRec':>7}"
    print(header)
    for r in rows:
        precision = r["action_agnostic_precision"]
        recall = r["action_agnostic_recall"]
        print(
            f"{r['gap_s']:>6g} {r['candidates']:>6} {r['action_aware_true_positive']:>8} "
            f"{r['action_agnostic_true_positive']:>6} {r['action_agnostic_false_positive']:>6} "
            f"{'n/a' if precision is None else format(precision, '.3f'):>8} "
            f"{'n/a' if recall is None else format(recall, '.3f'):>7}"
        )
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(
            json.dumps(
                {
                    "manifest": str(a.manifest),
                    "predictions": str(a.predictions),
                    "temporal_iou_threshold": a.iou,
                    "note": "Offline re-merge of recorded events; no inference was run.",
                    "rows": rows,
                },
                indent=1,
            )
            + "\n"
        )
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
