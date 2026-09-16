import os
import subprocess
import sys
import unittest
from pathlib import Path

from evaluation.metrics import match_events, score, validate_manifest


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
