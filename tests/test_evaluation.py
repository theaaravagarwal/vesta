import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evaluation.metrics import match_events, score, validate_manifest
from evaluation.merge_sweep import sweep
from evaluation.record_run import build, clip_summary


class MetricsTests(unittest.TestCase):
    def manifest(self, status="reviewed"):
        return {
            "schema_version": 1,
            "clips": [
                {
                    "id": "a",
                    "source_url": "https://example.test/a",
                    "license": "CC-BY-4.0",
                    "session_group": "day1",
                    "split": "held_out",
                    "path": "a.mp4",
                    "duration_s": 60,
                    "label_status": status,
                    "events": [{"action": "climbing", "start_s": 10, "end_s": 20}],
                }
            ],
        }

    def test_run_metadata_is_echoed_and_never_scored_as_a_clip(self):
        run = {"model": "qwen2.5vl:3b", "config_version": "temporal-v3-bounded-focus"}
        result = score(
            self.manifest(),
            {
                "run": run,
                "a": {
                    "status": "done",
                    "events": [{"action": "climbing", "start_s": 10, "end_s": 20}],
                },
            },
        )
        self.assertEqual(result["run"], run)
        self.assertEqual(result["evaluated_clips"], ["a"])
        self.assertEqual(result["true_positive"], 1)

    def test_predictions_without_run_metadata_report_none(self):
        result = score(self.manifest(), {"a": {"status": "done", "events": []}})
        self.assertIsNone(result["run"])

    def test_unknown_clip_ids_are_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown clip IDs"):
            score(self.manifest(), {"b": {"status": "done", "events": []}})

    def test_manifest_cannot_reuse_the_reserved_run_id(self):
        manifest = self.manifest()
        manifest["clips"][0]["id"] = "run"
        with self.assertRaisesRegex(ValueError, "reserved"):
            validate_manifest(manifest)

    def test_duplicate_is_false_alert(self):
        events = [{"action": "climbing", "start_s": 10, "end_s": 20}] * 2
        result = score(self.manifest(), {"a": {"status": "done", "events": events}})
        self.assertEqual((result["true_positive"], result["false_positive"]), (1, 1))

    def test_unknown_is_not_negative(self):
        result = score(
            self.manifest("unreviewed"), {"a": {"status": "done", "events": []}}
        )
        self.assertIsNone(result["recall"])
        self.assertFalse(result["complete"])

    def test_failed_is_not_safe(self):
        result = score(self.manifest(), {"a": {"status": "error", "events": []}})
        self.assertEqual(result["failed_predictions"], ["a"])
        self.assertIsNone(result["recall"])

    def test_unreviewed_errors_remain_visible(self):
        result = score(
            self.manifest("unreviewed"), {"a": {"status": "error", "events": []}}
        )
        self.assertEqual(result["failed_predictions"], ["a"])
        self.assertEqual(result["unreviewed_clips"], ["a"])

    def test_split_leak_rejected(self):
        m = self.manifest()
        c = dict(m["clips"][0], id="b", split="development")
        m["clips"].append(c)
        with self.assertRaises(ValueError):
            validate_manifest(m)

    def test_action_mismatch(self):
        a = [{"action": "walking", "start_s": 10, "end_s": 20}]
        self.assertEqual(match_events(self.manifest()["clips"][0]["events"], a), [])

    def test_ignore_action_matches_temporally_but_keeps_duplicates_false_alerts(self):
        events = [{"action": "walking", "start_s": 10, "end_s": 20}] * 2
        result = score(
            self.manifest(),
            {"a": {"status": "done", "events": events}},
            ignore_action=True,
        )
        self.assertEqual((result["true_positive"], result["false_positive"]), (1, 1))
        self.assertEqual(result["matching_mode"], "action_agnostic_temporal")
        self.assertEqual(result["by_action"], {})

    def test_perfect_match(self):
        m = self.manifest()
        r = score(m, {"a": {"status": "done", "events": m["clips"][0]["events"]}})
        self.assertEqual(r["recall"], 1)
        self.assertEqual(r["precision"], 1)
        self.assertEqual(r["mean_start_error_s"], 0)


class MergeSweepTests(unittest.TestCase):
    """Offline re-merge must not invent or lose matched events."""

    manifest = {
        "schema_version": 1,
        "clips": [
            {
                "id": "a",
                "source_url": "https://example.test/a",
                "license": "CC-BY-4.0",
                "session_group": "day1",
                "split": "development",
                "path": "a.mp4",
                "duration_s": 60,
                "label_status": "reviewed",
                "events": [{"action": "climbing", "start_s": 10, "end_s": 20}],
            }
        ],
    }

    def predictions(self):
        return {
            "run": {"model": "m", "config_version": "temporal-v3-bounded"},
            "a": {
                "status": "done",
                "events": [
                    {"action": "climbing", "start_s": 10.0, "end_s": 12.0},
                    {"action": "climbing", "start_s": 12.5, "end_s": 19.0},
                    {"action": "climbing", "start_s": 40.0, "end_s": 41.0},
                ],
            },
        }

    def test_gap_merges_fragments_without_losing_the_matched_event(self):
        rows = {r["gap_s"]: r for r in sweep(self.manifest, self.predictions(), [0, 1])}
        self.assertEqual(rows[0]["candidates"], 3)
        self.assertEqual(rows[1]["candidates"], 2)
        self.assertEqual(rows[0]["action_agnostic_true_positive"], 1)
        self.assertEqual(rows[1]["action_agnostic_true_positive"], 1)
        self.assertGreater(
            rows[1]["action_agnostic_precision"], rows[0]["action_agnostic_precision"]
        )

    def test_sweep_does_not_mutate_the_recorded_predictions(self):
        predictions = self.predictions()
        sweep(self.manifest, predictions, [4])
        self.assertEqual(len(predictions["a"]["events"]), 3)


class RecordRunTests(unittest.TestCase):
    """Benchmark records are generated from artifacts, never hand-copied."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, True)
        self.manifest = self.temp / "manifest.json"
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "clips": [
                        {
                            "id": "a",
                            "source_url": "https://example.test/a",
                            "license": "CC-BY-4.0",
                            "session_group": "day1",
                            "split": "development",
                            "path": "a.mp4",
                            "duration_s": 60,
                            "label_status": "reviewed",
                            "events": [
                                {"action": "climbing", "start_s": 10, "end_s": 20}
                            ],
                        },
                        {
                            "id": "b",
                            "source_url": "https://example.test/b",
                            "license": "CC-BY-4.0",
                            "session_group": "day2",
                            "split": "development",
                            "path": "b.mp4",
                            "duration_s": 30,
                            "label_status": "reviewed",
                            "events": [],
                        },
                    ],
                }
            )
        )

    def predictions(self, name, events):
        path = self.temp / name
        path.write_text(
            json.dumps(
                {
                    "run": {"model": "m", "config_version": "temporal-v3-bounded"},
                    "a": {"status": "done", "events": events, "elapsed_s": 1.5},
                    "b": {
                        "status": "error",
                        "events": [],
                        "elapsed_s": 0.5,
                        "job": {"error": "malformed JSON"},
                    },
                }
            )
        )
        return path

    def test_record_keeps_failures_visible_and_scores_both_modes(self):
        path = self.predictions(
            "p.json", [{"action": "boundary_entry", "start_s": 10, "end_s": 20}]
        )
        record = build(
            self.manifest, {"control": path}, "test purpose", "not promoted", 0.3, None
        )
        run = record["runs"]["control"]
        self.assertEqual(run["run"]["config_version"], "temporal-v3-bounded")
        self.assertEqual(run["clips"]["b"]["error"], "malformed JSON")
        self.assertEqual(run["metrics"]["action_aware"]["true_positive"], 0)
        self.assertEqual(run["metrics"]["action_agnostic"]["true_positive"], 1)
        self.assertEqual(run["metrics"]["action_aware"]["failed_predictions"], ["b"])
        self.assertFalse(run["metrics"]["action_aware"]["complete"])
        self.assertEqual(record["manifest"]["labeled_events"], 1)
        self.assertIs(record["provenance"]["models"], None)
        self.assertFalse(record["provenance"]["camera_accessed"])

    def test_offline_sweeps_are_embedded_in_the_record(self):
        sweep_path = self.temp / "sweep.json"
        sweep_path.write_text(json.dumps({"rows": [{"gap_s": 1, "candidates": 7}]}))
        record = build(
            self.manifest,
            {"control": self.predictions("r.json", [])},
            "p",
            None,
            0.3,
            None,
            {"dev": sweep_path},
        )
        self.assertEqual(
            record["offline_sweeps"]["dev"]["rows"][0]["candidates"], 7
        )

    def test_clip_summary_excludes_run_metadata(self):
        path = self.predictions("q.json", [])
        summary = clip_summary(json.loads(path.read_text()))
        self.assertEqual(sorted(summary), ["a", "b"])


class ExperimentAppTests(unittest.TestCase):
    """The isolated experiment app must never inherit an unrecorded variant."""

    root = Path(__file__).resolve().parents[1]

    def serve(self, **env):
        return subprocess.run(
            [sys.executable, "-c", "import evaluation.serve"],
            cwd=self.root,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
        )

    def test_unset_variant_refuses_to_start(self):
        result = self.serve(BEHAVIOR_EVENT_POLICY="", BEHAVIOR_FOCUS_VIEW="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("select a variant explicitly", result.stderr)

    def test_explicit_variant_starts(self):
        result = self.serve(BEHAVIOR_EVENT_POLICY="baseline", BEHAVIOR_FOCUS_VIEW="1")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
